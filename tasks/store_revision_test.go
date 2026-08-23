package tasks_test

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestMemoryRevisionCommitIsIdempotentAndCompareAndSwapProtected(t *testing.T) {
	store := tasks.NewMemoryStore()
	task, revision := revisionSeed("task-memory")
	if err := store.CreateWithRevision(context.Background(), task, revision); err != nil {
		t.Fatal(err)
	}
	commit := nextRevisionCommit(task, "update-2", "move to blue zone")
	if err := store.CommitRevision(context.Background(), commit); err != nil {
		t.Fatal(err)
	}
	if err := store.CommitRevision(context.Background(), commit); err != nil {
		t.Fatalf("exact replay must return the original result: %v", err)
	}

	conflicting := nextRevisionCommit(task, "update-3", "move to green zone")
	if err := store.CommitRevision(context.Background(), conflicting); !errors.Is(err, tasks.ErrRevisionConflict) {
		t.Fatalf("stale aggregate version error = %v", err)
	}

	reusedKey := nextRevisionCommit(task, "update-2", "different request")
	if err := store.CommitRevision(context.Background(), reusedKey); !errors.Is(err, tasks.ErrIdempotencyConflict) {
		t.Fatalf("reused idempotency key error = %v", err)
	}

	stored, err := store.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.AggregateVersion != 2 || stored.RevisionState != tasks.RevisionProposed {
		t.Fatalf("stored task = %#v", stored)
	}
	history, err := store.ListRevisions(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(history) != 2 || history[0].Status != tasks.RevisionActive || history[1].Status != tasks.RevisionProposed {
		t.Fatalf("history = %#v", history)
	}
	history[1].Revision.Steps[0].HarnessEvidenceIDs[0] = "caller-mutated"
	again, err := store.Revision(context.Background(), task.ID, 2)
	if err != nil {
		t.Fatal(err)
	}
	if again.Revision.Steps[0].HarnessEvidenceIDs[0] != "evidence-2" {
		t.Fatalf("revision clone leaked: %#v", again)
	}
}

func TestMemoryRevisionCommitAllowsExactlyOneConcurrentWriter(t *testing.T) {
	store := tasks.NewMemoryStore()
	task, revision := revisionSeed("task-race")
	if err := store.CreateWithRevision(context.Background(), task, revision); err != nil {
		t.Fatal(err)
	}

	commits := []tasks.RevisionCommit{
		nextRevisionCommit(task, "update-a", "blue zone"),
		nextRevisionCommit(task, "update-b", "green zone"),
	}
	start := make(chan struct{})
	results := make(chan error, len(commits))
	var wait sync.WaitGroup
	for _, commit := range commits {
		commit := commit
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			results <- store.CommitRevision(context.Background(), commit)
		}()
	}
	close(start)
	wait.Wait()
	close(results)
	var success, conflicts int
	for err := range results {
		switch {
		case err == nil:
			success++
		case errors.Is(err, tasks.ErrRevisionConflict):
			conflicts++
		default:
			t.Fatalf("unexpected error: %v", err)
		}
	}
	if success != 1 || conflicts != 1 {
		t.Fatalf("success=%d conflicts=%d", success, conflicts)
	}
}

func revisionSeed(taskID string) (*tasks.Task, *tasks.TaskRevision) {
	now := time.Unix(1_700_000_000, 0).UTC()
	task := &tasks.Task{
		ID: taskID, Request: "handoff block", Adapter: "mujoco", State: taskgraph.StateReady,
		CurrentRevision: 1, AggregateVersion: 1, RevisionState: tasks.RevisionActive,
		CreatedAt: now, UpdatedAt: now,
	}
	revision := &tasks.TaskRevision{
		TaskID: taskID, Revision: 1, Request: task.Request, Understanding: "transfer the block",
		IdempotencyKey: "create-1", CreatedAt: now,
		Steps: []tasks.RevisionStep{{
			StepID: "handoff/sender", SemanticFingerprint: "sender-v1", Status: tasks.StepSatisfied,
			HarnessEvidenceIDs: []string{"evidence-1"},
		}},
	}
	return task, revision
}

func nextRevisionCommit(base *tasks.Task, idempotencyKey, request string) tasks.RevisionCommit {
	nextTask := *base
	nextTask.AggregateVersion = 2
	nextTask.RevisionState = tasks.RevisionProposed
	nextTask.UpdatedAt = time.Unix(1_700_000_100, 0).UTC()
	revision := &tasks.TaskRevision{
		TaskID: base.ID, Revision: 2, BaseRevision: 1, ExpectedAggregateVersion: 1,
		Request: request, Understanding: request, IdempotencyKey: idempotencyKey,
		CreatedAt: nextTask.UpdatedAt,
		Steps: []tasks.RevisionStep{{
			StepID: "handoff/receiver", SemanticFingerprint: request, Status: tasks.StepPending,
			HarnessEvidenceIDs: []string{"evidence-2"},
		}},
	}
	return tasks.RevisionCommit{
		TaskID: base.ID, ExpectedAggregateVersion: 1, Task: &nextTask, NewRevision: revision,
		LifecycleEvent: tasks.RevisionLifecycleEvent{Revision: 2, Status: tasks.RevisionProposed, OccurredAt: nextTask.UpdatedAt},
	}
}
