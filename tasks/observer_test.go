package tasks_test

import (
	"context"
	"sync"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type eventRecorder struct {
	mu     sync.Mutex
	taskID []string
	events []tasks.TaskEvent
}

func (r *eventRecorder) observe(_ context.Context, taskID string, event tasks.TaskEvent) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.taskID = append(r.taskID, taskID)
	r.events = append(r.events, event)
}

func (r *eventRecorder) snapshot() ([]string, []tasks.TaskEvent) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.taskID...), append([]tasks.TaskEvent(nil), r.events...)
}

func newTaskService(t *testing.T) (*tasks.Service, *tasks.Task) {
	t.Helper()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{
		ID: "task-observer", Intent: parsed, State: taskgraph.StateReady,
		Approved: true, CurrentRevision: 1,
	}
	if err := store.Create(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	return service, task
}

// A state transition is the moment an observer learns that the task moved. The
// Agent runtime derives task.started / task.completed / task.failed from this
// hook instead of every caller having to remember to announce itself, so the
// hook has to carry the real previous state. It is not reconstructable later:
// once the transition is committed the old state is gone.
func TestEventObserverReportsBothSidesOfAStateChange(t *testing.T) {
	service, task := newTaskService(t)
	recorder := &eventRecorder{}
	service.ObserveEvents(recorder.observe)

	// The real execution path walks READY -> OBSERVING -> PLANNING -> EXECUTING.
	// Using the real path rather than an invented shortcut keeps this test
	// honest about what the state machine actually permits.
	for _, transition := range []struct {
		to     taskgraph.TaskState
		reason string
	}{
		{taskgraph.StateObserving, "local execution started"},
		{taskgraph.StatePlanning, "grounding and local planning started"},
		{taskgraph.StateExecuting, "local physical execution started"},
	} {
		if err := service.Transition(context.Background(), task.ID, transition.to, transition.reason); err != nil {
			t.Fatalf("transition to %s: %v", transition.to, err)
		}
	}

	identifiers, events := recorder.snapshot()
	if len(events) != 3 {
		t.Fatalf("observed %d events, want 3: %#v", len(events), events)
	}
	want := []struct {
		from taskgraph.TaskState
		to   taskgraph.TaskState
	}{
		{taskgraph.StateReady, taskgraph.StateObserving},
		{taskgraph.StateObserving, taskgraph.StatePlanning},
		{taskgraph.StatePlanning, taskgraph.StateExecuting},
	}
	for index, event := range events {
		if event.Type != "STATE_CHANGED" {
			t.Fatalf("event %d type = %q, want STATE_CHANGED", index, event.Type)
		}
		if identifiers[index] != task.ID {
			t.Fatalf("event %d attributed to %q, want %q", index, identifiers[index], task.ID)
		}
		if event.Payload["previousState"] != string(want[index].from) || event.Payload["state"] != string(want[index].to) {
			t.Fatalf("event %d payload = %#v, want %s -> %s",
				index, event.Payload, want[index].from, want[index].to)
		}
		if event.Message == "" {
			t.Fatalf("event %d lost the reason that explains it", index)
		}
		if event.Sequence == 0 {
			t.Fatalf("event %d has no sequence number, so it cannot be ordered", index)
		}
		if index > 0 && event.Sequence <= events[index-1].Sequence {
			t.Fatalf("event %d sequence = %d, want greater than the previous %d",
				index, event.Sequence, events[index-1].Sequence)
		}
	}
}

// An observer must not be able to change what happened to a task. It is told
// after the change is durable, and a refusal by the state machine is never
// announced as if it had happened.
func TestEventObserverIsNotToldAboutRefusedTransitions(t *testing.T) {
	service, task := newTaskService(t)
	recorder := &eventRecorder{}
	service.ObserveEvents(recorder.observe)

	// SUCCEEDED is not reachable directly from READY.
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateSucceeded, "impossible"); err == nil {
		t.Fatal("an illegal transition was accepted")
	}
	if _, events := recorder.snapshot(); len(events) != 0 {
		t.Fatalf("a refused transition was announced: %#v", events)
	}

	// A transition for an unknown task fails and is likewise not announced.
	if err := service.Transition(context.Background(), "task-missing", taskgraph.StateObserving, "unknown"); err == nil {
		t.Fatal("a transition for an unknown task was accepted")
	}
	if _, events := recorder.snapshot(); len(events) != 0 {
		t.Fatalf("a transition for an unknown task was announced: %#v", events)
	}
}

func TestObserveEventsCanBeRemoved(t *testing.T) {
	service, task := newTaskService(t)
	recorder := &eventRecorder{}
	service.ObserveEvents(recorder.observe)
	service.ObserveEvents(nil)

	if err := service.Transition(context.Background(), task.ID, taskgraph.StateObserving, "started"); err != nil {
		t.Fatal(err)
	}
	if _, events := recorder.snapshot(); len(events) != 0 {
		t.Fatalf("a removed observer was still called: %#v", events)
	}
}

// The observer runs without the service lock held, so it may read back the task
// it was just told about. If it ran while the lock was held, the read below
// would deadlock rather than fail — which is exactly the kind of bug a test
// suite reports as a timeout with no named cause.
func TestEventObserverMayReadBackTheTask(t *testing.T) {
	service, task := newTaskService(t)
	states := make([]taskgraph.TaskState, 0, 1)
	service.ObserveEvents(func(ctx context.Context, taskID string, _ tasks.TaskEvent) {
		current, err := service.Get(ctx, taskID)
		if err != nil {
			t.Errorf("observer could not read the task back: %v", err)
			return
		}
		states = append(states, current.State)
	})

	if err := service.Transition(context.Background(), task.ID, taskgraph.StateObserving, "started"); err != nil {
		t.Fatal(err)
	}
	if len(states) != 1 {
		t.Fatalf("observer ran %d times, want 1", len(states))
	}
	// The change is committed before the observer is told, so a read sees it.
	if states[0] != taskgraph.StateObserving {
		t.Fatalf("observer read state %q, want OBSERVING", states[0])
	}
}

// Appending a state change directly is a supported path, so it must be
// announced too: an observer that misses a transition it did not cause would
// report a lifecycle gap that never happened.
func TestEventObserverSeesAppendedStateChanges(t *testing.T) {
	service, task := newTaskService(t)
	recorder := &eventRecorder{}
	service.ObserveEvents(recorder.observe)

	if _, err := service.AppendEvent(context.Background(), task.ID, tasks.TaskEvent{Type: "STATE_CHANGED"}); err != nil {
		t.Fatal(err)
	}
	if _, err := service.AppendEvent(context.Background(), task.ID, tasks.TaskEvent{Type: "TOOL_ACTIVITY"}); err != nil {
		t.Fatal(err)
	}

	_, events := recorder.snapshot()
	if len(events) != 1 {
		t.Fatalf("observed %d events, want only the state change: %#v", len(events), events)
	}
	if events[0].Type != "STATE_CHANGED" {
		t.Fatalf("observed event type = %q, want STATE_CHANGED", events[0].Type)
	}
}
