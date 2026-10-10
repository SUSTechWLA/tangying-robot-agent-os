package coordinator

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestClaimContextSurvivesCoordinatorRestartInEventAndCheckpoint(t *testing.T) {
	ctx := context.Background()
	c, hub, _, id := newHandoffCoordinator(t, func(context.Context, string) (string, error) { return "catalog-v1", nil })
	path := filepath.Join(t.TempDir(), "coordinator.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	c.store = store
	node, err := c.NextIntent(ctx, id, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	task, _ := c.service.Get(ctx, id)
	if err := ValidateContextBasis(task, node); err != nil {
		t.Fatal(err)
	}
	if node.ContextBasis.World.WorldID != "fleet-default" || node.ContextBasis.ResourceID != "block:red-block" {
		t.Fatalf("basis=%+v", node.ContextBasis)
	}
	checkpoint, ok, err := store.LatestCheckpoint(ctx, id)
	if err != nil || !ok {
		t.Fatalf("checkpoint=%v err=%v", ok, err)
	}
	var saved persistedState
	if err := json.Unmarshal(checkpoint.Data, &saved); err != nil {
		t.Fatal(err)
	}
	if saved.Intents[0].ContextBasis.Digest != node.ContextBasis.Digest || node.ContextBasis.ClaimVersion != checkpoint.Version {
		t.Fatal("claim basis not atomic with checkpoint")
	}
	events, err := store.ListEvents(ctx, "fleet_task", id, 0, 10)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, event := range events {
		if event.EventType == "INTENT_CLAIMED" {
			found = event.Payload["contextBasis"] != nil
			wire, ok := event.Payload["contextBasisJSON"].(string)
			var audited contextcontract.Basis
			if !ok || json.Unmarshal([]byte(wire), &audited) != nil || audited.Validate() != nil || audited.Digest != node.ContextBasis.Digest {
				t.Fatal("event context cannot be verified without numeric loss")
			}
		}
	}
	if !found {
		t.Fatal("claim event lost context")
	}
	digest := node.ContextBasis.Digest
	node.ContextBasis.Scope = "caller-mutation"
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	restarted := NewWithStore(c.service, time.Minute, reopened).WithWorld(hub, time.Minute).WithResourceLeases(c.resources, time.Minute)
	snapshot, err := restarted.Snapshot(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Intents[0].ContextBasis.Digest != digest || snapshot.Intents[0].ContextBasis.Scope != contextcontract.IntentScope {
		t.Fatal("caller changed durable basis")
	}
	if next, err := restarted.NextIntent(ctx, id, "robot-1"); err != nil || next != nil {
		t.Fatalf("replayed claim=%v err=%v", next, err)
	}
}

func TestPendingProposalAdvancesAggregateWithoutMixingActiveExecution(t *testing.T) {
	ctx := context.Background()
	c, _, _, id := newHandoffCoordinator(t, func(context.Context, string) (string, error) { return "catalog-v1", nil })
	node, err := c.NextIntent(ctx, id, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	_, err = c.ProposeRevision(ctx, tasks.ProposeRevisionCommand{TaskID: id, ExpectedRevision: 1, Request: "最后放到右侧蓝色垫子上", IdempotencyKey: "context-proposal", Creator: "owner"})
	if err != nil {
		t.Fatal(err)
	}
	task, _ := c.service.Get(ctx, id)
	if task.AggregateVersion <= node.AggregateVersion {
		t.Fatal("fixture did not advance aggregate")
	}
	if err := ValidateContextBasis(task, node); err != nil {
		t.Fatal(err)
	}
	if err := c.RenewIntent(ctx, id, node, "robot-1"); err != nil {
		t.Fatal(err)
	}
	task.Intent.Action = "mutated-without-revision"
	if err := ValidateContextBasis(task, node); err == nil {
		t.Fatal("mixed task contents accepted")
	}
}

func TestLegacyRunningClaimDoesNotGetInventedContext(t *testing.T) {
	ctx := context.Background()
	c, hub, store, id := newHandoffCoordinator(t, func(context.Context, string) (string, error) { return "catalog-v1", nil })
	node, err := c.NextIntent(ctx, id, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	state := c.graphs[id]
	state.intents[0].ContextBasis = nil
	if err := c.persistLocked(ctx, state, "LEGACY_FIXTURE", "legacy-fixture", nil, nil); err != nil {
		t.Fatal(err)
	}
	restarted := NewWithStore(c.service, time.Minute, store).WithWorld(hub, time.Minute).WithResourceLeases(c.resources, time.Minute)
	if err := restarted.RenewIntent(ctx, id, node, "robot-1"); err == nil {
		t.Fatal("legacy running claim silently renewed")
	}
	if next, err := restarted.NextIntent(ctx, id, "robot-1"); err != nil || next != nil {
		t.Fatalf("legacy claim redispatched: node=%v err=%v", next, err)
	}
	snapshot, err := restarted.Snapshot(ctx, id)
	if err != nil || snapshot.Intents[0].ContextBasis != nil {
		t.Fatalf("fabricated basis: snapshot=%v err=%v", snapshot, err)
	}
}
