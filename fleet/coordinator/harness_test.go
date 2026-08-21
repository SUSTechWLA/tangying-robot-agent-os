package coordinator

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
)

func TestCompletionPersistsHarnessVerdictAndEvidence(t *testing.T) {
	ctx := context.Background()
	coordinator, hub, store, taskID := newHandoffCoordinator(
		t,
		func(context.Context, string) (string, error) { return "catalog-v1", nil },
	)
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil {
		t.Fatal(err)
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

	snapshot, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	completed := snapshot.Intents[0]
	if completed.HarnessStatus != "SATISFIED" || completed.HarnessReason != "PHYSICAL_POSTCONDITIONS_SATISFIED" {
		t.Fatalf("completed intent=%#v", completed)
	}
	if len(completed.HarnessEvidence) != 2 {
		t.Fatalf("harness evidence=%v", completed.HarnessEvidence)
	}

	events, err := store.ListEvents(ctx, "fleet_task", taskID, 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, event := range events {
		if event.EventType != "BLOCK_AVAILABLE" {
			continue
		}
		verdict, ok := event.Payload["harness"].(map[string]any)
		if !ok || verdict["status"] != "SATISFIED" {
			t.Fatalf("persisted harness verdict=%#v", event.Payload["harness"])
		}
		if verdict["worldRevision"] != float64(completed.WorldRevision) {
			t.Fatalf("persisted harness revision=%#v, want %d", verdict["worldRevision"], completed.WorldRevision)
		}
		evidence, ok := verdict["observations"].([]any)
		if !ok || len(evidence) != 2 {
			t.Fatalf("persisted harness observations=%#v", verdict["observations"])
		}
		robotEvidence, ok := evidence[1].(map[string]any)
		if !ok || robotEvidence["sourceSequence"] != "1" {
			t.Fatalf("persisted evidence sequence must be exact decimal text: %#v", evidence[1])
		}
		transition, ok := event.Payload["resourceTransition"].(map[string]any)
		if !ok || transition["fromOwner"] != "robot-1" || transition["fromFencingToken"] != float64(node.FencingToken) ||
			transition["toOwner"] != "robot-2" || transition["toFencingToken"] != float64(snapshot.Intents[1].FencingToken) {
			t.Fatalf("persisted resource transition=%#v", event.Payload["resourceTransition"])
		}
		found = true
	}
	if !found {
		t.Fatal("BLOCK_AVAILABLE event did not persist Harness verdict")
	}
}
