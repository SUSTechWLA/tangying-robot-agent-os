package localapp

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/incidents"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Hold the final audit after it has read its state, exposing precisely the
// window in which a concurrent Cancel formerly made that snapshot stale.
type finishReadGate struct {
	tasks.Repository
	armed            atomic.Bool
	entered, release chan struct{}
	once             sync.Once
	appendArmed      atomic.Bool
	appendEntered    chan struct{}
	appendRelease    chan struct{}
	appendOnce       sync.Once
}

func (r *finishReadGate) Get(ctx context.Context, id string) (*tasks.Task, error) {
	task, err := r.Repository.Get(ctx, id)
	if _, bounded := ctx.Deadline(); bounded && err == nil && r.armed.CompareAndSwap(true, false) {
		close(r.entered)
		select {
		case <-r.release:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return task, err
}

func (r *finishReadGate) Update(ctx context.Context, task *tasks.Task) error {
	if len(task.Events) > 0 && task.Events[len(task.Events)-1].Type == "LOCAL_RUN_FINISHED" && r.appendArmed.CompareAndSwap(true, false) {
		close(r.appendEntered)
		select {
		case <-r.appendRelease:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return r.Repository.Update(ctx, task)
}

func (r *finishReadGate) unblockRead() { r.once.Do(func() { close(r.release) }) }
func (r *finishReadGate) unblock() {
	r.unblockRead()
	r.appendOnce.Do(func() { close(r.appendRelease) })
}

func newFinishRaceTask(t *testing.T, capabilityTask bool, runErr error) (*App, *tasks.Service, *finishReadGate, string, *testRobot) {
	t.Helper()
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "tasks.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	repository := &finishReadGate{
		Repository: store, entered: make(chan struct{}), release: make(chan struct{}),
		appendEntered: make(chan struct{}), appendRelease: make(chan struct{}),
	}
	t.Cleanup(repository.unblock)
	service := tasks.NewService(repository, intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if capabilityTask {
		task.Plan = &orchestration.Bundle{Capabilities: &capability.Plan{
			RobotID: "fixture", Calls: []capability.Call{{Tool: "robot.task"}},
		}}
		if err := store.Update(context.Background(), task); err != nil {
			t.Fatal(err)
		}
	}
	robot := &testRobot{}
	runner := agent.NewRunner(store, &failedGroundRobot{}, robot)
	if capabilityTask {
		runner.CapabilityRun = func(context.Context, *tasks.Task, agent.RunControl) (agent.RunResult, error) {
			return agent.RunResult{}, runErr
		}
	}
	app := New(service, runner, memory.NewQueue[string](1))
	if _, err := service.Approve(context.Background(), task.ID, "fixture-operator"); err != nil {
		t.Fatal(err)
	}
	return app, service, repository, task.ID, robot
}

func waitFinishRaceSignal(t *testing.T, signal <-chan struct{}, label string) {
	t.Helper()
	select {
	case <-signal:
	case <-time.After(3 * time.Second):
		t.Fatalf("timed out waiting for %s", label)
	}
}

func TestCancelSerializesWithFinishSnapshotAndAppend(t *testing.T) {
	for _, capabilityTask := range []bool{false, true} {
		name, wantAttempt := "legacy", taskgraph.StateRecoverableFailure
		if capabilityTask {
			name, wantAttempt = "capability", taskgraph.StatePaused
		}
		t.Run(name, func(t *testing.T) {
			app, service, repository, taskID, robot := newFinishRaceTask(t, capabilityTask, agent.ErrPauseRequested)
			repository.armed.Store(true)
			repository.appendArmed.Store(true)
			done := make(chan struct{})
			go func() { defer close(done); app.run(context.Background(), taskID) }()
			t.Cleanup(func() {
				repository.unblock()
				waitFinishRaceSignal(t, done, "fixture worker cleanup")
			})
			waitFinishRaceSignal(t, repository.entered, "finish snapshot")

			// This check makes the bad interleaving deterministic, independent of
			// how soon the cancellation goroutine below gets scheduled.
			if app.mu.TryLock() {
				app.mu.Unlock()
				t.Error("finish state snapshot is outside the lifecycle mutation lock")
			}
			cancelStarted, cancelled := make(chan struct{}), make(chan error, 1)
			go func() { close(cancelStarted); cancelled <- app.Cancel(taskID) }()
			waitFinishRaceSignal(t, cancelStarted, "cancellation contender")
			repository.unblockRead()
			waitFinishRaceSignal(t, repository.appendEntered, "finish append before persistence")
			if app.mu.TryLock() {
				app.mu.Unlock()
				t.Error("finish persistence released the lifecycle mutation lock too early")
			}
			repository.unblock()
			waitFinishRaceSignal(t, done, "attempt closure")
			select {
			case err := <-cancelled:
				if err != nil {
					t.Fatal(err)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("cancellation did not finish after audit released ownership")
			}
			current, err := service.Get(context.Background(), taskID)
			if err != nil {
				t.Fatal(err)
			}
			var finishSequence, cancelSequence uint64
			finishCount := 0
			for _, event := range current.Events {
				switch event.Type {
				case "LOCAL_RUN_FINISHED":
					finishCount++
					finishSequence = event.Sequence
					if event.Payload["state"] != string(wantAttempt) {
						t.Errorf("finish does not describe the ended attempt: %+v", event)
					}
				case "LOCAL_CANCEL_REQUESTED":
					t.Error("a finished runner cannot consume a cancellation request")
				case "STATE_CHANGED":
					if event.Message == "operator cancelled" {
						cancelSequence = event.Sequence
					}
				}
			}
			if current.State != taskgraph.StateCancelled || finishCount != 1 || cancelSequence <= finishSequence {
				t.Fatalf("cancellation must commit after the accurate finish audit: state=%s finish=%d count=%d cancel=%d", current.State, finishSequence, finishCount, cancelSequence)
			}
			if app.ExecutionActive(taskID) || robot.calls() != 0 {
				t.Fatal("closed fixture retained ownership or invoked physical tools")
			}
		})
	}
}

func TestEndedCapabilityRunnerRejectsCancellationDuringIncidentClosure(t *testing.T) {
	for _, scenario := range []struct {
		name  string
		err   error
		state taskgraph.TaskState
	}{
		{"paused", agent.ErrPauseRequested, taskgraph.StatePaused},
		{"failed", errors.New("fixture interrupted before dispatch"), taskgraph.StateRecoverableFailure},
		{"waiting_user", capabilityagent.ErrNeedsInput, taskgraph.StateWaitingUser},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			app, service, _, taskID, robot := newFinishRaceTask(t, true, scenario.err)
			entered, release := make(chan struct{}), make(chan struct{})
			var enteredOnce, releaseOnce sync.Once
			unblock := func() { releaseOnce.Do(func() { close(release) }) }
			t.Cleanup(unblock)
			writer := incidents.New(t.TempDir())
			writer.Now = func() time.Time {
				enteredOnce.Do(func() { close(entered); <-release })
				return time.Now()
			}
			app.WithIncidents(writer)
			done := make(chan struct{})
			go func() { defer close(done); app.run(context.Background(), taskID) }()
			t.Cleanup(func() {
				unblock()
				waitFinishRaceSignal(t, done, "fixture incident cleanup")
			})
			waitFinishRaceSignal(t, entered, "incident persistence after runner returned")
			if !app.ExecutionActive(taskID) {
				t.Fatal("fixture must retain ownership while final evidence is written")
			}
			err := app.Cancel(taskID)
			if err == nil || !strings.Contains(err.Error(), "previous execution is still stopping") {
				t.Errorf("cancel accepted without a live runner to consume it: %v", err)
			}
			unblock()
			waitFinishRaceSignal(t, done, "incident and attempt closure")
			current, err := service.Get(context.Background(), taskID)
			if err != nil {
				t.Fatal(err)
			}
			if current.State != scenario.state {
				t.Fatalf("rejected cancellation changed outcome: %s", current.State)
			}
			for _, event := range current.Events {
				if event.Type == "LOCAL_CANCEL_REQUESTED" {
					t.Fatal("dangling cancellation request persisted after capability runner ended")
				}
			}
			last := current.Events[len(current.Events)-1]
			if last.Type != "LOCAL_RUN_FINISHED" || last.Payload["state"] != string(scenario.state) {
				t.Fatalf("missing actual closure outcome: %+v", last)
			}
			if app.ExecutionActive(taskID) || robot.calls() != 0 {
				t.Fatal("closed fixture retained ownership or invoked physical tools")
			}
			// A subsequent explicit cancellation is handled synchronously after
			// ownership is released; the rejected request is never auto-replayed.
			if err := app.Cancel(taskID); err != nil {
				t.Fatal(err)
			}
			current, _ = service.Get(context.Background(), taskID)
			if current.State != taskgraph.StateCancelled {
				t.Fatalf("new cancellation after closure did not take effect: %s", current.State)
			}
		})
	}
}
