package worker

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

type interruptedRuntime struct {
	activityRuntime
	started, cancelling, release      chan struct{}
	stopHadDeadline, stopWasCancelled bool
}

func (r *interruptedRuntime) Invoke(ctx context.Context, _ runtime.Command) (runtime.Result, error) {
	close(r.started)
	<-ctx.Done()
	return runtime.Result{}, ctx.Err()
}
func (r *interruptedRuntime) Cancel(ctx context.Context, _, _ string) (bool, error) {
	_, r.stopHadDeadline = ctx.Deadline()
	r.stopWasCancelled = ctx.Err() != nil
	close(r.cancelling)
	select {
	case <-r.release:
		return true, nil
	case <-ctx.Done():
		return false, ctx.Err()
	}
}

func TestInterruptedInvocationWaitsForBoundedFreshStopAttempt(t *testing.T) {
	r := &interruptedRuntime{started: make(chan struct{}), cancelling: make(chan struct{}), release: make(chan struct{})}
	w := New(Config{RobotID: "robot-7", Runtime: r, ControlTimeout: time.Second})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := w.invokeTracked(ctx, runtime.Command{CommandID: "long-step"}); done <- err }()
	<-r.started
	cancel()
	<-r.cancelling
	select {
	case err := <-done:
		t.Fatalf("returned before stop settled: %v", err)
	default:
	}
	w.current.Lock()
	current := w.current.commandID
	w.current.Unlock()
	if current != "long-step" {
		t.Fatalf("lost stop ownership: %q", current)
	}
	close(r.release)
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if !r.stopHadDeadline || r.stopWasCancelled {
		t.Fatal("stop did not get a fresh bounded context")
	}
	w.current.Lock()
	current = w.current.commandID
	w.current.Unlock()
	if current != "" {
		t.Fatalf("command not drained: %q", current)
	}
}

func TestCommandDeadlineStopsWithoutEscalatingToEmergency(t *testing.T) {
	r := &interruptedRuntime{started: make(chan struct{}), cancelling: make(chan struct{}), release: make(chan struct{})}
	w := New(Config{RobotID: "robot-7", Runtime: r, ControlTimeout: 10 * time.Millisecond})
	started := time.Now()
	_, err := w.invokeTracked(context.Background(), runtime.Command{CommandID: "expired", Deadline: time.Now().Add(10 * time.Millisecond)})
	if !errors.Is(err, context.DeadlineExceeded) || time.Since(started) > time.Second {
		t.Fatalf("err=%v duration=%v", err, time.Since(started))
	}
	select {
	case <-r.cancelling:
	default:
		t.Fatal("deadline closed stream without stop attempt")
	}
}

func TestAlreadyExpiredInvocationNeverDispatches(t *testing.T) {
	r := &interruptedRuntime{started: make(chan struct{})}
	w := New(Config{Runtime: r})
	_, err := w.invokeTracked(context.Background(), runtime.Command{Deadline: time.Now().Add(-time.Second)})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	select {
	case <-r.started:
		t.Fatal("expired command dispatched")
	default:
	}
}

type drainingObserver struct{ started, cancelled, release chan struct{} }

func (o drainingObserver) Telemetry(ctx context.Context, _ string) (telemetry.Snapshot, error) {
	close(o.started)
	<-ctx.Done()
	close(o.cancelled)
	<-o.release
	return telemetry.Snapshot{}, ctx.Err()
}
func TestWorkerDrainsTelemetryBeforeReturning(t *testing.T) {
	observer := drainingObserver{make(chan struct{}), make(chan struct{}), make(chan struct{})}
	source := &receiptSource{next: func(context.Context) (string, error) { <-observer.started; return "", ErrSourceStopped }}
	w := New(Config{RobotID: "robot-7", ExecutionStore: openInboxStore(t, filepath.Join(t.TempDir(), "execution.db")),
		Source: source, Cloud: &activityCloud{}, Runtime: activityRuntime{}, Observer: observer, TelemetryInterval: time.Millisecond})
	done := make(chan error, 1)
	go func() { done <- w.Run(context.Background()) }()
	<-observer.cancelled
	select {
	case err := <-done:
		t.Fatalf("Run returned before telemetry drained: %v", err)
	default:
	}
	close(observer.release)
	if err := <-done; !errors.Is(err, ErrSourceStopped) {
		t.Fatal(err)
	}
}
