package sqlite

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestRecordEventAppendOnlyPreservesAuthorityAndHistory(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "append.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	task := &tasks.Task{ID: "parent", State: taskgraph.StateCancelled, Approved: false, CurrentRevision: 3, AggregateVersion: 7,
		Events: []tasks.TaskEvent{{Sequence: 1, Type: "TASK_CREATED", Payload: map[string]any{"original": true}}}}
	if err := store.Create(ctx, task); err != nil {
		t.Fatal(err)
	}
	// Rewriting even one historical event is forbidden. The legacy full-task
	// Update path would trip this trigger, regardless of timing or machine speed.
	if _, err := store.db.Exec(`CREATE TRIGGER immutable_history BEFORE UPDATE ON task_events BEGIN SELECT RAISE(ABORT, 'old event rewritten'); END`); err != nil {
		t.Fatal(err)
	}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	var notified int
	service.ObserveEvents(func(ctx context.Context, id string, event tasks.TaskEvent) {
		stored, err := service.Get(ctx, id)
		if err != nil || len(stored.Events) != int(event.Sequence) {
			t.Fatalf("observer before commit: task=%+v err=%v", stored, err)
		}
		// Reentering a mutation proves notification occurs outside Service.mu.
		if _, err := service.RecordEvent(ctx, id, tasks.TaskEvent{Type: "OBSERVER_RECEIPT"}); err != nil {
			t.Fatal(err)
		}
		notified++
	})
	payload := map[string]any{"tool": "navigation.goto"}
	event, err := service.RecordEvent(ctx, task.ID, tasks.TaskEvent{Sequence: 999, Type: "TOOL_ACTIVITY", Payload: payload})
	if err != nil {
		t.Fatal(err)
	}
	if event.Sequence != 2 || event.Payload["eventId"] != "parent#2" || event.OccurredAt.IsZero() || notified != 1 {
		t.Fatalf("event=%+v notified=%d", event, notified)
	}
	if _, mutated := payload["eventId"]; mutated {
		t.Fatal("caller payload mutated")
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	actual, err := reopened.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if actual.State != task.State || actual.Approved || actual.CurrentRevision != 3 || actual.AggregateVersion != 7 {
		t.Fatalf("authority overwritten: %+v", actual)
	}
	if len(actual.Events) != 3 || actual.Events[0].Payload["original"] != true || actual.Events[1].Payload["eventId"] != "parent#2" {
		t.Fatalf("history changed: %+v", actual.Events)
	}
}

func TestRecordEventFailureRollsBackTimestampAndDoesNotNotify(t *testing.T) {
	ctx := context.Background()
	store, err := Open(filepath.Join(t.TempDir(), "append.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	old := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	if err := store.Create(ctx, &tasks.Task{ID: "parent", State: taskgraph.StateReady, UpdatedAt: old}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.db.Exec(`CREATE TRIGGER reject_receipt BEFORE INSERT ON task_events WHEN NEW.type = 'TOOL_ACTIVITY' BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END`); err != nil {
		t.Fatal(err)
	}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	notified := false
	service.ObserveEvents(func(context.Context, string, tasks.TaskEvent) { notified = true })
	for _, event := range []tasks.TaskEvent{{Type: "TOOL_ACTIVITY"}, {Type: "AUDIT", Payload: map[string]any{"invalid": func() {}}}} {
		if _, err := service.RecordEvent(ctx, "parent", event); err == nil {
			t.Fatal("expected failed append")
		}
		actual, err := store.Get(ctx, "parent")
		if err != nil {
			t.Fatal(err)
		}
		if len(actual.Events) != 0 || !actual.UpdatedAt.Equal(old) || notified {
			t.Fatalf("partial append: %+v notified=%v", actual, notified)
		}
	}
	if _, err := service.RecordEvent(ctx, "missing", tasks.TaskEvent{Type: "AUDIT"}); !errors.Is(err, tasks.ErrTaskNotFound) {
		t.Fatalf("missing: %v", err)
	}
	committed, err := service.RecordEvent(ctx, "parent", tasks.TaskEvent{Type: "AUDIT"})
	if err != nil || committed.Sequence != 1 {
		t.Fatalf("sequence consumed by rollback: %+v err=%v", committed, err)
	}
}

func TestRecordEventConcurrentServicesNeverLoseFacts(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "append.db")
	first, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	second, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	if err := first.Create(ctx, &tasks.Task{ID: "parent", State: taskgraph.StateReady}); err != nil {
		t.Fatal(err)
	}
	const count = 32
	start := make(chan struct{})
	failures := make(chan error, count)
	var group sync.WaitGroup
	for i := 0; i < count; i++ {
		store := first
		if i%2 != 0 {
			store = second
		}
		// Independent Service locks and independent connection pools deliberately
		// leave all sequencing to the database transaction.
		service := tasks.NewService(store, intent.NewDeterministicParser())
		group.Add(1)
		go func(i int) {
			defer group.Done()
			<-start
			_, err := service.RecordEvent(ctx, "parent", tasks.TaskEvent{Type: "TOOL_ACTIVITY", Message: fmt.Sprint(i)})
			failures <- err
		}(i)
	}
	close(start)
	group.Wait()
	close(failures)
	for err := range failures {
		if err != nil {
			t.Fatal(err)
		}
	}
	actual, err := first.Get(ctx, "parent")
	if err != nil {
		t.Fatal(err)
	}
	if len(actual.Events) != count {
		t.Fatalf("lost events: got %d want %d", len(actual.Events), count)
	}
	seen := map[string]bool{}
	for i, event := range actual.Events {
		if event.Sequence != uint64(i+1) || event.Payload["eventId"] != fmt.Sprintf("parent#%d", i+1) {
			t.Fatalf("sequence/identity: %+v", event)
		}
		seen[event.Message] = true
	}
	if len(seen) != count {
		t.Fatalf("duplicate overwrite: %+v", seen)
	}
}

// Fixed-size histories and untimed resets keep the benchmark input comparable
// across iterations. Results are measurements, never pass/fail thresholds.
func BenchmarkTaskEventAppend(b *testing.B) {
	for _, history := range []int{32, 128, 512} {
		for _, fast := range []bool{false, true} {
			name := "full_task"
			if fast {
				name = "append_only"
			}
			b.Run(fmt.Sprintf("%s/history_%d", name, history), func(b *testing.B) {
				ctx := context.Background()
				store, err := Open(filepath.Join(b.TempDir(), "bench.db"))
				if err != nil {
					b.Fatal(err)
				}
				defer store.Close()
				task := &tasks.Task{ID: "parent", State: taskgraph.StateReady}
				for i := 1; i <= history; i++ {
					task.Events = append(task.Events, tasks.TaskEvent{Sequence: uint64(i), Type: "EDGE_CHILD_EVENT", Payload: map[string]any{"evidence": strings.Repeat("x", 1024)}})
				}
				if err := store.Create(ctx, task); err != nil {
					b.Fatal(err)
				}
				service := tasks.NewService(store, intent.NewDeterministicParser())
				event := tasks.TaskEvent{Type: "EDGE_CHILD_EVENT", Payload: map[string]any{"evidence": "verified"}}
				b.ResetTimer()
				for i := 0; i < b.N; i++ {
					if fast {
						_, err = service.RecordEvent(ctx, task.ID, event)
					} else {
						_, err = service.AppendEvent(ctx, task.ID, event)
					}
					if err != nil {
						b.Fatal(err)
					}
					b.StopTimer()
					if _, err := store.db.Exec("DELETE FROM task_events WHERE task_id = ? AND sequence > ?", task.ID, history); err != nil {
						b.Fatal(err)
					}
					b.StartTimer()
				}
			})
		}
	}
}
