package sqlite

import (
	"context"
	"path/filepath"
	"testing"
	"time"

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
		ID:               "task-1",
		Request:          "把红色杯子放进右侧收纳盒",
		Adapter:          "mujoco",
		ExecutionContext: tasks.ExecutionContext{Mode: "simulation_demo", NewEpisode: true},
		State:            taskgraph.StateReady,
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
	if actual.ExecutionContext != task.ExecutionContext {
		t.Fatalf("reopened execution context = %#v, want %#v", actual.ExecutionContext, task.ExecutionContext)
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

func TestTaskRevisionHistoryPersistsAcrossReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	task, revision := sqliteRevisionSeed("task-revision")
	if err := store.CreateWithRevision(context.Background(), task, revision); err != nil {
		t.Fatal(err)
	}
	commit := sqliteRevisionCommit(task)
	if err := store.CommitRevision(context.Background(), commit); err != nil {
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
	stored, err := reopened.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	history, err := reopened.ListRevisions(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.AggregateVersion != 2 || len(history) != 2 || history[1].Status != tasks.RevisionProposed {
		t.Fatalf("stored=%#v history=%#v", stored, history)
	}
}

func sqliteRevisionSeed(taskID string) (*tasks.Task, *tasks.TaskRevision) {
	now := time.Unix(1_700_000_000, 0).UTC()
	task := &tasks.Task{ID: taskID, Request: "handoff", Adapter: "mujoco", State: taskgraph.StateReady,
		CurrentRevision: 1, AggregateVersion: 1, RevisionState: tasks.RevisionActive, CreatedAt: now, UpdatedAt: now}
	revision := &tasks.TaskRevision{TaskID: taskID, Revision: 1, Request: task.Request,
		IdempotencyKey: "create-1", CreatedAt: now, Steps: []tasks.RevisionStep{{StepID: "sender", Status: tasks.StepPending}}}
	return task, revision
}

func sqliteRevisionCommit(base *tasks.Task) tasks.RevisionCommit {
	next := *base
	next.AggregateVersion = 2
	next.RevisionState = tasks.RevisionProposed
	next.UpdatedAt = base.UpdatedAt.Add(time.Second)
	revision := &tasks.TaskRevision{TaskID: base.ID, Revision: 2, BaseRevision: 1, ExpectedAggregateVersion: 1,
		Request: "handoff to blue zone", IdempotencyKey: "update-2", CreatedAt: next.UpdatedAt,
		Steps: []tasks.RevisionStep{{StepID: "receiver", Status: tasks.StepPending}}}
	return tasks.RevisionCommit{TaskID: base.ID, ExpectedAggregateVersion: 1, Task: &next,
		NewRevision: revision, LifecycleEvent: tasks.RevisionLifecycleEvent{Revision: 2, Status: tasks.RevisionProposed, OccurredAt: next.UpdatedAt}}
}
