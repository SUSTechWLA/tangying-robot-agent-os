package coordinator

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/worldhub"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestIntentCompletionWaitsForFreshStableWorldEvidence(t *testing.T) {
	ctx := context.Background()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	hub := worldhub.New("fleet-default", time.Minute, 16)
	resourceLeases := lease.NewMemoryManager()
	coordinator := NewWithStore(service, time.Minute, eventlog.NewMemoryStore()).
		WithWorld(hub, time.Minute).
		WithResourceLeases(resourceLeases, time.Minute).
		WithCatalogLookup(func(context.Context, string) (string, error) { return "catalog-v1", nil })
	node, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || node == nil {
		t.Fatalf("claim=%#v err=%v", node, err)
	}
	if node.ResourceID != "block:red-block" || node.FencingToken == 0 || node.CatalogRevision != "catalog-v1" {
		t.Fatalf("claim identity=%#v", node)
	}
	claimedWorld, _ := hub.Snapshot(ctx)
	if claimedWorld.Resources[node.ResourceID].Owner != "robot-1" {
		t.Fatalf("claimed resource world state=%#v", claimedWorld.Resources[node.ResourceID])
	}
	if _, err := coordinator.CompleteIntent(ctx, task.ID, node.Index, "robot-1"); !errors.Is(err, ErrWorldNotReady) {
		t.Fatalf("completion without world evidence err=%v", err)
	}

	now := time.Now().UTC()
	for _, event := range []observation.Envelope{
		handoffEntityObservation(now, 1),
		robotHeldObservation(now, "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := coordinator.CompleteIntent(ctx, task.ID, node.Index, "robot-1"); !errors.Is(err, ErrWorldNotReady) {
		t.Fatalf("unstable completion err=%v", err)
	}
	if _, err := hub.Ingest(ctx, handoffEntityObservation(now.Add(time.Millisecond), 2)); err != nil {
		t.Fatal(err)
	}
	snapshot, err := coordinator.CompleteIntent(ctx, task.ID, node.Index, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Intents[1].FencingToken <= node.FencingToken || snapshot.Intents[1].PredicateState != "BLOCK_AVAILABLE" {
		t.Fatalf("handoff did not transfer fencing: %#v", snapshot.Intents[1])
	}
	transferredWorld, _ := hub.Snapshot(ctx)
	if resource := transferredWorld.Resources[node.ResourceID]; resource.Owner != "robot-2" || resource.FencingToken != snapshot.Intents[1].FencingToken {
		t.Fatalf("transferred resource world state=%#v", resource)
	}
}

func TestSimulationPreparationClaimsNoBlockLeaseAndGatesManipulation(t *testing.T) {
	ctx := context.Background()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.CreateCommand(ctx, tasks.CreateCommand{
		Request:          "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块放到右侧目标区",
		Adapter:          "robocasa",
		ExecutionContext: tasks.ExecutionContext{Mode: "simulation_demo", NewEpisode: true},
	})
	if err != nil {
		t.Fatal(err)
	}
	resources := lease.NewMemoryManager()
	coordinator := NewWithStore(service, time.Minute, eventlog.NewMemoryStore()).
		WithResourceLeases(resources, time.Minute)
	preparation, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || preparation == nil {
		t.Fatalf("preparation=%#v err=%v", preparation, err)
	}
	if preparation.Action != "prepare_simulation" || preparation.ResourceID != "" || preparation.FencingToken != 0 {
		t.Fatalf("preparation claim=%#v", preparation)
	}
	if blocked, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil || blocked != nil {
		t.Fatalf("manipulation became ready before preparation: %#v err=%v", blocked, err)
	}
	if _, err := coordinator.CompleteIntent(ctx, task.ID, preparation.Index, "robot-1"); err != nil {
		t.Fatal(err)
	}
	basis, err := coordinator.RevisionBasis(ctx, task.ID)
	if err != nil || !basis.EvidenceValidity[preparation.StepID] {
		t.Fatalf("completed one-shot preparation was not retained: basis=%#v err=%v", basis, err)
	}
	manipulationNode, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || manipulationNode == nil {
		t.Fatalf("manipulation=%#v err=%v", manipulationNode, err)
	}
	if manipulationNode.ResourceID != sharedBlockResourceID || manipulationNode.FencingToken == 0 {
		t.Fatalf("manipulation lease=%#v", manipulationNode)
	}
}

type failNthCommitStore struct {
	eventlog.Store
	commits int
	failAt  int
}

func (s *failNthCommitStore) Commit(ctx context.Context, request eventlog.CommitRequest) error {
	s.commits++
	if s.commits == s.failAt {
		return errors.New("injected commit failure")
	}
	return s.Store.Commit(ctx, request)
}

func TestResourceOwnershipIsNotPublishedWhenClaimCommitFails(t *testing.T) {
	ctx := context.Background()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	hub := worldhub.New("fleet-default", time.Minute, 16)
	resourceLeases := lease.NewMemoryManager()
	store := &failNthCommitStore{Store: eventlog.NewMemoryStore(), failAt: 2}
	coordinator := NewWithStore(service, time.Minute, store).
		WithWorld(hub, time.Minute).
		WithResourceLeases(resourceLeases, time.Minute).
		WithCatalogLookup(func(context.Context, string) (string, error) { return "catalog-v1", nil })

	if node, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err == nil || node != nil {
		t.Fatalf("claim with failed commit = %#v, %v", node, err)
	}
	world, err := hub.Snapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := world.Resources[sharedBlockResourceID]; ok {
		t.Fatalf("uncommitted ownership leaked into world: %#v", world.Resources[sharedBlockResourceID])
	}
	if _, err := resourceLeases.Acquire(ctx, sharedBlockResourceID, "robot-2", time.Minute); err != nil {
		t.Fatalf("failed claim left an active lease behind: %v", err)
	}
}

func TestFailedIntentReleasesItsResourceLease(t *testing.T) {
	ctx := context.Background()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	resources := lease.NewMemoryManager()
	coordinator := NewWithStore(service, time.Minute, eventlog.NewMemoryStore()).
		WithResourceLeases(resources, time.Minute)
	node, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || node == nil {
		t.Fatalf("claim=%#v err=%v", node, err)
	}
	if _, err := coordinator.FailIntent(ctx, task.ID, node.Index, "robot-1", "grounding failed"); err != nil {
		t.Fatal(err)
	}
	if _, err := resources.Acquire(ctx, sharedBlockResourceID, "recovery-worker", time.Minute); err != nil {
		t.Fatalf("failed intent leaked its resource lease: %v", err)
	}
}

func TestTransferredOwnershipIsNotPublishedWhenCompletionCommitFails(t *testing.T) {
	ctx := context.Background()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	hub := worldhub.New("fleet-default", time.Minute, 16)
	resourceLeases := lease.NewMemoryManager()
	store := &failNthCommitStore{Store: eventlog.NewMemoryStore(), failAt: 3}
	coordinator := NewWithStore(service, time.Minute, store).
		WithWorld(hub, time.Minute).
		WithResourceLeases(resourceLeases, time.Minute).
		WithCatalogLookup(func(context.Context, string) (string, error) { return "catalog-v1", nil })
	node, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || node == nil {
		t.Fatalf("claim = %#v, %v", node, err)
	}
	now := time.Now().UTC().Add(time.Millisecond)
	for _, event := range []observation.Envelope{
		handoffEntityObservation(now, 1),
		handoffEntityObservation(now.Add(time.Millisecond), 2),
		robotHeldObservation(now, "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	if snapshot, err := coordinator.CompleteIntent(ctx, task.ID, node.Index, "robot-1"); err == nil || snapshot != nil {
		t.Fatalf("completion with failed commit = %#v, %v", snapshot, err)
	}
	world, err := hub.Snapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if resource := world.Resources[sharedBlockResourceID]; resource.Owner != "robot-1" || resource.FencingToken != node.FencingToken {
		t.Fatalf("uncommitted transfer leaked into world: %#v", resource)
	}
	if _, err := resourceLeases.Acquire(ctx, sharedBlockResourceID, "recovery-worker", time.Minute); err != nil {
		t.Fatalf("failed completion left a transferred lease active: %v", err)
	}
}

func TestPreexistingStableWorldCannotProveAClaimedPhysicalIntent(t *testing.T) {
	ctx := context.Background()
	coordinator, hub, _, taskID := newHandoffCoordinator(t, func(context.Context, string) (string, error) {
		return "catalog-v1", nil
	})
	now := time.Now().UTC().Add(-time.Second)
	for _, event := range []observation.Envelope{
		handoffEntityObservation(now, 1),
		handoffEntityObservation(now.Add(time.Millisecond), 2),
		robotHeldObservation(now.Add(2*time.Millisecond), "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil || node == nil {
		t.Fatalf("claim=%#v err=%v", node, err)
	}

	if _, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1"); !errors.Is(err, ErrWorldNotReady) {
		t.Fatalf("preexisting evidence proved a command that did not run: %v", err)
	}

	post := time.Now().UTC().Add(time.Millisecond)
	for _, event := range []observation.Envelope{
		handoffEntityObservationFrom(post, 3, "robot-1"),
		robotHeldObservation(post, "robot-1", 2, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1"); !errors.Is(err, ErrWorldNotReady) {
		t.Fatalf("one post-claim entity sample must not prove stability: %v", err)
	}
	if _, err := hub.Ingest(ctx, handoffEntityObservationFrom(post.Add(time.Millisecond), 4, "robot-1")); err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1"); err != nil {
		t.Fatalf("fresh post-claim evidence should complete: %v", err)
	}
}

func TestExternalBlockMovePreventsVerifiedCompletion(t *testing.T) {
	ctx := context.Background()
	coordinator, hub, store, taskID := newHandoffCoordinator(t, func(context.Context, string) (string, error) {
		return "catalog-v1", nil
	})
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	for _, event := range []observation.Envelope{
		handoffEntityObservation(now, 1),
		handoffEntityObservation(now.Add(time.Millisecond), 2),
		robotHeldObservation(now, "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	moved := handoffEntityObservation(now.Add(2*time.Millisecond), 3)
	moved.ObservationID = "external-move"
	moved.Payload = observation.EntityPayload{
		EntityID: "red-block", Category: "block", Pose: []float64{-0.4, -0.4, 0.82},
		Relations: map[string]string{"inside": "outside-workcell"},
	}
	if _, err := hub.Ingest(ctx, moved); err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1"); !errors.Is(err, ErrWorldNotReady) {
		t.Fatalf("external move did not block completion: %v", err)
	}
	events, _ := store.ListEvents(ctx, "fleet_task", taskID, 0, 100)
	for _, event := range events {
		if event.EventType == "BLOCK_AVAILABLE" || event.EventType == "BLOCK_DELIVERED" {
			t.Fatalf("false physical event after external move: %#v", event)
		}
	}
}

func TestReceiverOfflineAfterHandoffCannotClaimOrDeliver(t *testing.T) {
	ctx := context.Background()
	online := map[string]bool{"robot-1": true, "robot-2": true}
	coordinator, hub, store, taskID := newHandoffCoordinator(t, func(_ context.Context, robotID string) (string, error) {
		if !online[robotID] {
			return "", errors.New("robot is offline: " + robotID)
		}
		return "catalog-v1", nil
	})
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	for _, event := range []observation.Envelope{
		handoffEntityObservation(now, 1), handoffEntityObservation(now.Add(time.Millisecond), 2),
		robotHeldObservation(now, "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1"); err != nil {
		t.Fatal(err)
	}
	online["robot-2"] = false
	if claimed, err := coordinator.NextIntent(ctx, taskID, "robot-2"); err == nil || claimed != nil {
		t.Fatalf("offline receiver claim=%#v err=%v", claimed, err)
	}
	snapshot, _ := coordinator.Snapshot(ctx, taskID)
	if snapshot.Intents[1].Status != StatusReady || snapshot.Intents[1].PredicateState != "BLOCK_AVAILABLE" {
		t.Fatalf("handoff was not held safely for receiver: %#v", snapshot.Intents[1])
	}
	events, _ := store.ListEvents(ctx, "fleet_task", taskID, 0, 100)
	for _, event := range events {
		if event.EventType == "BLOCK_DELIVERED" {
			t.Fatal("offline receiver falsely delivered the block")
		}
	}
}

func TestQueueOutageLeavesCommittedHandoffOutboxForRecovery(t *testing.T) {
	ctx := context.Background()
	coordinator, hub, store, taskID := newHandoffCoordinator(t, func(context.Context, string) (string, error) {
		return "catalog-v1", nil
	})
	coordinator.WithEnqueue(func(context.Context, string, []string) error {
		return errors.New("redis unavailable")
	})
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	for _, event := range []observation.Envelope{
		handoffEntityObservation(now, 1), handoffEntityObservation(now.Add(time.Millisecond), 2),
		robotHeldObservation(now, "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1"); err != nil {
		t.Fatal(err)
	}
	outbox, err := store.ClaimOutbox(ctx, 10)
	if err != nil || len(outbox) != 1 || outbox[0].Topic != "robot/robot-2" || !outbox[0].AckedAt.IsZero() {
		t.Fatalf("recoverable outbox=%#v err=%v", outbox, err)
	}
	snapshot, _ := coordinator.Snapshot(ctx, taskID)
	if snapshot.Intents[1].Status != StatusReady {
		t.Fatalf("queue outage corrupted graph: %#v", snapshot.Intents[1])
	}
}

func TestOutboxDispatcherRecoversAfterPublisherReturns(t *testing.T) {
	ctx := context.Background()
	coordinator, hub, store, taskID := newHandoffCoordinator(t, func(context.Context, string) (string, error) {
		return "catalog-v1", nil
	})
	coordinator.WithEnqueue(func(context.Context, string, []string) error {
		return errors.New("queue unavailable")
	})
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	for _, event := range []observation.Envelope{
		handoffEntityObservation(now, 1), handoffEntityObservation(now.Add(time.Millisecond), 2),
		robotHeldObservation(now, "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1"); err != nil {
		t.Fatal(err)
	}

	var delivered []string
	coordinator.WithEnqueue(func(_ context.Context, recoveredTask string, robots []string) error {
		delivered = append(delivered, recoveredTask)
		delivered = append(delivered, robots...)
		return nil
	})
	count, err := coordinator.DispatchOutbox(ctx, 10)
	if err != nil || count != 1 {
		t.Fatalf("dispatch count=%d err=%v", count, err)
	}
	if len(delivered) != 2 || delivered[0] != taskID || delivered[1] != "robot-2" {
		t.Fatalf("delivered=%v", delivered)
	}
	pending, err := store.ClaimOutbox(ctx, 10)
	if err != nil || len(pending) != 0 {
		t.Fatalf("acked outbox returned as pending: %#v err=%v", pending, err)
	}
}

func TestCameraLossDoesNotBlockSemanticGroundTruthHandoff(t *testing.T) {
	ctx := context.Background()
	coordinator, hub, _, taskID := newHandoffCoordinator(t, func(context.Context, string) (string, error) {
		return "catalog-v1", nil
	})
	node, err := coordinator.NextIntent(ctx, taskID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	for _, event := range []observation.Envelope{
		handoffEntityObservation(now, 1), handoffEntityObservation(now.Add(time.Millisecond), 2),
		robotHeldObservation(now, "robot-1", 1, ""),
	} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	world, _ := hub.Snapshot(ctx)
	for sourceID := range world.Sources {
		if sourceID == "robot-1/camera" || sourceID == "robot-2/camera" {
			t.Fatal("test unexpectedly supplied camera evidence")
		}
	}
	if _, err := coordinator.CompleteIntent(ctx, taskID, node.Index, "robot-1"); err != nil {
		t.Fatalf("semantic handoff depended on camera bytes: %v", err)
	}
}

func newHandoffCoordinator(
	t *testing.T,
	lookup func(context.Context, string) (string, error),
) (*Coordinator, *worldhub.Hub, *eventlog.MemoryStore, string) {
	t.Helper()
	ctx := context.Background()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	hub := worldhub.New("fleet-default", time.Minute, 32)
	store := eventlog.NewMemoryStore()
	coordinator := NewWithStore(service, time.Minute, store).
		WithWorld(hub, time.Minute).
		WithResourceLeases(lease.NewMemoryManager(), time.Minute).
		WithCatalogLookup(lookup)
	return coordinator, hub, store, task.ID
}

func handoffEntityObservation(now time.Time, sequence uint64) observation.Envelope {
	return handoffEntityObservationFrom(now, sequence, "robot-2")
}

func handoffEntityObservationFrom(now time.Time, sequence uint64, robotID string) observation.Envelope {
	return observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "handoff-entity-" + time.Unix(int64(sequence), 0).Format("150405"),
		WorldID: "fleet-default", SourceID: robotID + "/scene", RobotID: robotID,
		SourceType: observation.SourceSimGroundTruth, SourceSequence: sequence,
		ObservedAt: now, ReceivedAt: now, FrameID: "world", TransformRevision: "mujoco-world-v1",
		Kind: observation.EntityUpsert, Payload: observation.EntityPayload{
			EntityID: "red-block", Category: "block", Pose: []float64{0.32, 0.34, 0.82},
			Relations: map[string]string{"inside": "handoff-zone"},
		},
		Confidence: 1, Provenance: observation.Provenance{Adapter: "mujoco", Version: "test"},
	}
}

func robotHeldObservation(now time.Time, robotID string, sequence uint64, held string) observation.Envelope {
	return observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: robotID + "-state-" + time.Unix(int64(sequence), 0).Format("150405"),
		WorldID: "fleet-default", SourceID: robotID + "/proprioception", RobotID: robotID,
		SourceType: observation.SourceProprioception, SourceSequence: sequence,
		ObservedAt: now, ReceivedAt: now, FrameID: "world", TransformRevision: "mujoco-world-v1",
		Kind: observation.RobotStateUpsert, Payload: observation.RobotPayload{RobotID: robotID, Held: held},
		Confidence: 1, Provenance: observation.Provenance{Adapter: "mujoco", Version: "test"},
	}
}

func newTestCoordinator(t *testing.T, claimLease time.Duration) (*Coordinator, *tasks.Service) {
	t.Helper()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	return NewWithLease(service, claimLease), service
}

func TestOnlyCurrentFencedLeaderCanAdvance(t *testing.T) {
	ctx := context.Background()
	now := time.Unix(100, 0).UTC()
	leases := lease.NewMemoryManagerWithClock(func() time.Time { return now })
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	store := eventlog.NewMemoryStore()
	first := NewWithStore(service, time.Minute, store)
	if err := first.WithLeadership(ctx, leases, "world-test", "coordinator-a", time.Second); err != nil {
		t.Fatal(err)
	}
	now = now.Add(2 * time.Second)
	second := NewWithStore(service, time.Minute, store)
	if err := second.WithLeadership(ctx, leases, "world-test", "coordinator-b", time.Second); err != nil {
		t.Fatal(err)
	}
	if _, err := first.NextIntent(ctx, task.ID, "robot-1"); !errors.Is(err, ErrLeadershipLost) {
		t.Fatalf("stale leader advanced: %v", err)
	}
	if node, err := second.NextIntent(ctx, task.ID, "robot-1"); err != nil || node == nil {
		t.Fatalf("current leader claim=%#v err=%v", node, err)
	}
}

func TestRenewLeadershipKeepsCoordinatorWritable(t *testing.T) {
	ctx := context.Background()
	now := time.Unix(100, 0).UTC()
	leases := lease.NewMemoryManagerWithClock(func() time.Time { return now })
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	coordinator := NewWithStore(service, time.Minute, eventlog.NewMemoryStore())
	if err := coordinator.WithLeadership(ctx, leases, "world-test", "coordinator-a", time.Second); err != nil {
		t.Fatal(err)
	}
	now = now.Add(500 * time.Millisecond)
	if err := coordinator.RenewLeadership(ctx, time.Second); err != nil {
		t.Fatal(err)
	}
	now = now.Add(700 * time.Millisecond)
	if node, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil || node == nil {
		t.Fatalf("renewed leader claim=%#v err=%v", node, err)
	}
}

func TestCoordinatorRestoresRunningIntentWithoutDoubleAdvance(t *testing.T) {
	ctx := context.Background()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	store := eventlog.NewMemoryStore()
	first := NewWithStore(service, time.Minute, store)
	node, err := first.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || node == nil {
		t.Fatalf("claim=%#v err=%v", node, err)
	}

	restarted := NewWithStore(service, time.Minute, store)
	snapshot, err := restarted.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Intents[0].Status != StatusRunning || snapshot.Intents[0].Claimed != node.Claimed {
		t.Fatalf("restart lost claim: %#v", snapshot.Intents[0])
	}
	if claimed, err := restarted.NextIntent(ctx, task.ID, "robot-1"); err != nil || claimed != nil {
		t.Fatalf("running intent was double-claimed: %#v err=%v", claimed, err)
	}
}

func TestSequentialMultiRobotClaiming(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, time.Minute)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒，然后让2号机器人把蓝色瓶子放进左侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}

	// robot-2 cannot claim intent 0 or 1 before robot-1 finishes.
	node, err := coordinator.NextIntent(ctx, task.ID, "robot-2")
	if err != nil || node != nil {
		t.Fatalf("robot-2 early claim = %+v (err=%v), want nil", node, err)
	}

	node, err = coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || node == nil || node.Index != 0 {
		t.Fatalf("robot-1 claim = %+v (err=%v), want intent 0", node, err)
	}
	if node.Status != StatusRunning || node.Claimed != "robot-1" {
		t.Fatalf("claimed node = %+v", node)
	}
	if _, err := coordinator.CompleteIntent(ctx, task.ID, 0, "robot-2"); err == nil {
		t.Fatal("robot-2 must not complete robot-1's intent")
	}

	if _, err := coordinator.CompleteIntent(ctx, task.ID, 0, "robot-1"); err != nil {
		t.Fatalf("complete intent 0: %v", err)
	}

	node, err = coordinator.NextIntent(ctx, task.ID, "robot-2")
	if err != nil || node == nil || node.Index != 1 {
		t.Fatalf("robot-2 claim after refresh = %+v (err=%v), want intent 1", node, err)
	}
	if _, err := coordinator.CompleteIntent(ctx, task.ID, 1, "robot-2"); err != nil {
		t.Fatalf("complete intent 1: %v", err)
	}

	final, err := coordinator.service.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if final.State != "SUCCEEDED" {
		t.Fatalf("final state = %q, want SUCCEEDED", final.State)
	}
}

func TestClaimLeaseReclaimsStaleRunningIntents(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, 100*time.Millisecond)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	node, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || node == nil {
		t.Fatalf("claim = %+v (err=%v)", node, err)
	}
	time.Sleep(150 * time.Millisecond)

	// The stale claim is reclaimed: a new worker can claim intent 0 again.
	reclaimed, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	if reclaimed == nil || reclaimed.Index != 0 {
		t.Fatalf("reclaimed = %+v, want intent 0 again", reclaimed)
	}
	snapshot, err := coordinator.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Intents[0].Status != StatusRunning {
		t.Fatalf("after reclaim+claim status = %q, want RUNNING", snapshot.Intents[0].Status)
	}
}

func TestUnboundIntentClaimableByAnyWorker(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, time.Minute)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	node, err := coordinator.NextIntent(ctx, task.ID, "robot-3")
	if err != nil || node == nil || node.RobotID != "" {
		t.Fatalf("any-worker claim = %+v (err=%v)", node, err)
	}
	if node.Claimed != "robot-3" {
		t.Fatalf("claim recorded on %q, want robot-3", node.Claimed)
	}
}

func TestEnqueueHookFiresOnCrossRobotRefresh(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, time.Minute)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒，然后让2号机器人把蓝色瓶子放进左侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	var handed []string
	coordinator.WithEnqueue(func(_ context.Context, taskID string, robotIDs []string) error {
		handed = append(handed, taskID)
		handed = append(handed, robotIDs...)
		return nil
	})
	if _, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.CompleteIntent(ctx, task.ID, 0, "robot-1"); err != nil {
		t.Fatal(err)
	}
	if len(handed) != 2 || handed[0] != task.ID || handed[1] != "robot-2" {
		t.Fatalf("handoff enqueue = %v, want [%s robot-2]", handed, task.ID)
	}
}
