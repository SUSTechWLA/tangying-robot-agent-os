package coordinator

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestConfirmRevisionWaitsForRunningIntentThenActivatesAtHarnessSafePoint(t *testing.T) {
	ctx := context.Background()
	coordinator, hub, _, taskID := newHandoffCoordinator(
		t, func(context.Context, string) (string, error) { return "catalog-v1", nil },
	)
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	basis, err := coordinator.RevisionBasis(ctx, taskID)
	if err != nil {
		t.Fatal(err)
	}
	proposal, err := coordinator.service.ProposeRevision(ctx, tasks.ProposeRevisionCommand{
		TaskID: taskID, ExpectedRevision: 1, Request: "最后放到右侧蓝色垫子上",
		IdempotencyKey: "proposal-safe-point", Creator: "owner",
	}, basis)
	if err != nil {
		t.Fatal(err)
	}
	confirmed, err := coordinator.ConfirmRevision(ctx, taskID, proposal.Revision.Revision, 1, "confirm-safe-point")
	if err != nil {
		t.Fatal(err)
	}
	if confirmed.Status != tasks.RevisionWaitingSafePoint {
		t.Fatalf("confirmed status=%s", confirmed.Status)
	}

	now := time.Now().UTC()
	for _, event := range []observation.Envelope{
		handoffEntityObservationFrom(now, 1, "robot-1"),
		handoffEntityObservationFrom(now.Add(time.Millisecond), 2, "robot-1"),
		robotHeldObservation(now.Add(2*time.Millisecond), "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	snapshot, err := coordinator.CompleteIntentRevision(ctx, taskID, node.TaskRevision, node.AggregateVersion,
		node.Index, node.StepID, "robot-1", node.CommandID, node.FencingToken)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.TaskRevision != 2 {
		t.Fatalf("active revision=%d, want 2", snapshot.TaskRevision)
	}
	if snapshot.Intents[0].Status != StatusSucceeded || snapshot.Intents[0].HarnessStatus != "SATISFIED" {
		t.Fatalf("satisfied evidence was not retained: %#v", snapshot.Intents[0])
	}
	if snapshot.Intents[1].Status != StatusReady {
		t.Fatalf("updated receiver step=%#v, want READY", snapshot.Intents[1])
	}
	history, err := coordinator.service.ListRevisions(ctx, taskID)
	if err != nil {
		t.Fatal(err)
	}
	if history[0].Status != tasks.RevisionSuperseded || history[1].Status != tasks.RevisionActive {
		t.Fatalf("revision lifecycle=%#v", history)
	}
}

func TestOlderRevisionAndWrongCommandCannotAdvanceNewGraph(t *testing.T) {
	ctx := context.Background()
	coordinator, service := newTestCoordinator(t, time.Minute)
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	proposal, err := service.ProposeRevision(ctx, tasks.ProposeRevisionCommand{
		TaskID: task.ID, ExpectedRevision: 1, Request: "最后放到左侧目标区",
		IdempotencyKey: "proposal-immediate", Creator: "owner",
	}, tasks.RevisionBasis{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.ConfirmRevision(ctx, task.ID, proposal.Revision.Revision, 1, "confirm-immediate"); err != nil {
		t.Fatal(err)
	}
	if replayed, err := coordinator.ConfirmRevision(ctx, task.ID, proposal.Revision.Revision, 2, "confirm-immediate"); err != nil || replayed.Status != tasks.RevisionActive {
		t.Fatalf("confirmation replay=%#v err=%v", replayed, err)
	}
	node, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	if node.TaskRevision != 2 {
		t.Fatalf("claimed revision=%d", node.TaskRevision)
	}
	if _, err := coordinator.CompleteIntent(ctx, task.ID, node.Index, "robot-1"); !errors.Is(err, ErrIntentIdentityConflict) {
		t.Fatalf("legacy completion advanced revisioned graph: %v", err)
	}
	before, err := coordinator.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = coordinator.CompleteIntentRevision(ctx, task.ID, 1, node.AggregateVersion,
		node.Index, node.StepID, "robot-1", node.CommandID, node.FencingToken)
	if !errors.Is(err, ErrStaleTaskRevision) {
		t.Fatalf("old revision err=%v", err)
	}
	_, err = coordinator.CompleteIntentRevision(ctx, task.ID, node.TaskRevision, node.AggregateVersion,
		node.Index, node.StepID, "robot-1", "wrong-command", node.FencingToken)
	if !errors.Is(err, ErrIntentIdentityConflict) {
		t.Fatalf("wrong command err=%v", err)
	}
	after, err := coordinator.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if after.Intents[0].Status != before.Intents[0].Status || after.Intents[0].CommandID != before.Intents[0].CommandID {
		t.Fatalf("rejected completion mutated graph: before=%#v after=%#v", before.Intents[0], after.Intents[0])
	}
}

func TestRevisionIdentitySurvivesCoordinatorFailover(t *testing.T) {
	ctx := context.Background()
	first, configuredService := newTestCoordinator(t, time.Minute)
	service := configuredService
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	store := first.store
	node, err := first.NextIntent(ctx, task.ID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	restarted := NewWithStore(service, time.Minute, store)
	snapshot, err := restarted.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	restored := snapshot.Intents[0]
	if restored.TaskRevision != node.TaskRevision || restored.AggregateVersion != node.AggregateVersion ||
		restored.StepID != node.StepID || restored.CommandID != node.CommandID {
		t.Fatalf("identity lost across failover: before=%#v after=%#v", node, restored)
	}
}

func TestLegacyCheckpointMigrationPreservesRunningClaim(t *testing.T) {
	ctx := context.Background()
	_, service := newTestCoordinator(t, time.Minute)
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	wire, err := json.Marshal(map[string]any{
		"taskId": task.ID,
		"intents": []map[string]any{{
			"index": 0, "action": "pick_and_place", "robotId": "robot-1",
			"claimed": "robot-1", "status": "RUNNING",
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	store := eventlog.NewMemoryStore()
	if err := store.Commit(ctx, eventlog.CommitRequest{ExpectedVersion: 0, State: eventlog.AggregateState{
		AggregateID: task.ID, Version: 1, Data: wire,
	}}); err != nil {
		t.Fatal(err)
	}
	coordinator := NewWithStore(service, time.Minute, store)
	snapshot, err := coordinator.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	node := snapshot.Intents[0]
	if node.Status != StatusRunning || node.Claimed != "robot-1" || node.TaskRevision != 1 ||
		node.StepID == "" || node.CommandID == "" {
		t.Fatalf("legacy claim was not migrated in place: %#v", node)
	}
}

func TestCompletionRejectsLowerFenceAndExactDuplicateIsIdempotent(t *testing.T) {
	ctx := context.Background()
	coordinator, hub, store, taskID := newHandoffCoordinator(
		t, func(context.Context, string) (string, error) { return "catalog-v1", nil },
	)
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	beforeEvents, err := store.ListEvents(ctx, "fleet_task", taskID, 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	_, err = coordinator.CompleteIntentRevision(ctx, taskID, node.TaskRevision, node.AggregateVersion,
		node.Index, node.StepID, "robot-1", node.CommandID, node.FencingToken-1)
	if !errors.Is(err, ErrIntentIdentityConflict) {
		t.Fatalf("lower fence err=%v", err)
	}
	afterRejected, err := store.ListEvents(ctx, "fleet_task", taskID, 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	if len(afterRejected) != len(beforeEvents) {
		t.Fatalf("rejected completion wrote events: before=%d after=%d", len(beforeEvents), len(afterRejected))
	}
	now := time.Now().UTC()
	for _, event := range []observation.Envelope{
		handoffEntityObservationFrom(now, 1, "robot-1"),
		handoffEntityObservationFrom(now.Add(time.Millisecond), 2, "robot-1"),
		robotHeldObservation(now.Add(2*time.Millisecond), "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	first, err := coordinator.CompleteIntentRevision(ctx, taskID, node.TaskRevision, node.AggregateVersion,
		node.Index, node.StepID, "robot-1", node.CommandID, node.FencingToken)
	if err != nil {
		t.Fatal(err)
	}
	firstEventCount, _ := store.ListEvents(ctx, "fleet_task", taskID, 0, 100)
	replayed, err := coordinator.CompleteIntentRevision(ctx, taskID, node.TaskRevision, node.AggregateVersion,
		node.Index, node.StepID, "robot-1", node.CommandID, node.FencingToken)
	if err != nil {
		t.Fatal(err)
	}
	secondEventCount, _ := store.ListEvents(ctx, "fleet_task", taskID, 0, 100)
	if len(secondEventCount) != len(firstEventCount) || replayed.Intents[0].Finished != first.Intents[0].Finished {
		t.Fatalf("duplicate completion was not idempotent: events %d -> %d", len(firstEventCount), len(secondEventCount))
	}
}
