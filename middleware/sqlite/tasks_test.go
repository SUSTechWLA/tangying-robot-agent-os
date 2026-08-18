package sqlite

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestTaskPersistsAcrossReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{
		ID:      "task-1",
		Request: "把红色杯子放进右侧收纳盒",
		Adapter: "mujoco",
		State:   taskgraph.StateReady,
		Events: []tasks.TaskEvent{
			{Sequence: 1, Type: "TASK_CREATED"},
		},
	}
	if err := store.Create(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	actual, err := reopened.Get(context.Background(), "task-1")
	if err != nil {
		t.Fatal(err)
	}
	if actual.Request != task.Request || actual.State != task.State {
		t.Fatalf("reopened task = %#v", actual)
	}
	if len(actual.Events) != 1 || actual.Events[0].Type != "TASK_CREATED" {
		t.Fatalf("reopened events = %#v", actual.Events)
	}
}

func TestTaskUpdateAndEventAreAtomic(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	ctx := context.Background()
	task := &tasks.Task{ID: "task-1", State: taskgraph.StateReady}
	if err := store.Create(ctx, task); err != nil {
		t.Fatal(err)
	}
	task.State = taskgraph.StateObserving
	event := tasks.TaskEvent{Type: "STATE_CHANGED", Message: "local execution started"}
	if err := store.UpdateWithEvent(ctx, task, event); err != nil {
		t.Fatal(err)
	}
	actual, err := store.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if actual.State != taskgraph.StateObserving {
		t.Fatalf("state = %s", actual.State)
	}
	if len(actual.Events) != 1 || actual.Events[0].Sequence != 1 || actual.Events[0].Message != event.Message {
		t.Fatalf("events = %#v", actual.Events)
	}
}
