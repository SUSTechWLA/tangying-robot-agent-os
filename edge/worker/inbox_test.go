package worker

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type receiptSource struct {
	next        func(context.Context) (string, error)
	ack         func(context.Context, string) error
	reads, acks int
}

func (s *receiptSource) Next(ctx context.Context) (string, error) { s.reads++; return s.next(ctx) }
func (s *receiptSource) AckNotification(ctx context.Context, id string) error {
	s.acks++
	if s.ack != nil {
		return s.ack(ctx, id)
	}
	return nil
}

func openInboxStore(t *testing.T, path string) *sqlite.Store {
	t.Helper()
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return store
}

func TestNotificationIsDurableBeforeTransportAckAndSurvivesRestart(t *testing.T) {
	ctx, path := context.Background(), filepath.Join(t.TempDir(), "execution.db")
	store := openInboxStore(t, path)
	source := &receiptSource{next: func(context.Context) (string, error) { return "task-long", nil }}
	w := New(Config{RobotID: "robot-7", ExecutionStore: store, Source: source})
	source.ack = func(ctx context.Context, id string) error {
		_, saved, err := w.loadNotification(ctx)
		if err != nil || saved.TaskID != id {
			t.Fatalf("ack before durable admission: saved=%+v err=%v", saved, err)
		}
		return errors.New("ack response lost")
	}
	if _, err := w.nextDurableTask(ctx); err == nil {
		t.Fatal("expected lost acknowledgement")
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store = openInboxStore(t, path)
	restarted := New(Config{RobotID: "robot-7", ExecutionStore: store, Source: source})
	id, err := restarted.nextDurableTask(ctx)
	if err != nil || id != "task-long" || source.reads != 1 || source.acks != 1 {
		t.Fatalf("restart: id=%s err=%v reads=%d acks=%d", id, err, source.reads, source.acks)
	}
	if err := restarted.finishDurableTask(ctx, "another-task"); err == nil {
		t.Fatal("foreign notification completion accepted")
	}
	if err := restarted.finishDurableTask(ctx, id); err != nil {
		t.Fatal(err)
	}
	_, saved, err := restarted.loadNotification(ctx)
	if err != nil || saved.TaskID != "" {
		t.Fatalf("finished notification retained: %+v %v", saved, err)
	}
}

func TestNotificationStoreFailureDoesNotAcknowledgeDelivery(t *testing.T) {
	ctx := context.Background()
	store := openInboxStore(t, filepath.Join(t.TempDir(), "execution.db"))
	source := &receiptSource{next: func(context.Context) (string, error) {
		_ = store.Close()
		return "task-not-accepted", nil
	}}
	w := New(Config{RobotID: "robot-7", ExecutionStore: store, Source: source})
	if _, err := w.nextDurableTask(ctx); err == nil || source.acks != 0 {
		t.Fatalf("err=%v acks=%d", err, source.acks)
	}
}

type unavailableCloud struct {
	activityCloud
	attempts int
}

func (c *unavailableCloud) GetTask(context.Context, string) (*tasks.Task, error) {
	c.attempts++
	if c.attempts <= 65 {
		return nil, errors.New("cloud disconnected")
	}
	return &tasks.Task{ID: "task-long", State: taskgraph.StateSucceeded}, nil
}

func TestControlPlaneOutageDoesNotDiscardNotificationAfterSixtyRetries(t *testing.T) {
	store := openInboxStore(t, filepath.Join(t.TempDir(), "execution.db"))
	source := &receiptSource{}
	source.next = func(context.Context) (string, error) {
		if source.reads == 1 {
			return "task-long", nil
		}
		return "", ErrSourceStopped
	}
	cloud := &unavailableCloud{}
	w := New(Config{RobotID: "robot-7", ExecutionStore: store, Source: source, Cloud: cloud,
		Runtime: activityRuntime{}, TaskRetryDelay: time.Millisecond, TelemetryInterval: time.Hour})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := w.Run(ctx); !errors.Is(err, ErrSourceStopped) {
		t.Fatalf("Run: %v", err)
	}
	if cloud.attempts != 66 || source.reads != 2 || source.acks != 1 {
		t.Fatalf("attempts=%d reads=%d acks=%d", cloud.attempts, source.reads, source.acks)
	}
	_, saved, err := w.loadNotification(ctx)
	if err != nil || saved.TaskID != "" {
		t.Fatalf("saved=%+v err=%v", saved, err)
	}
}
