package coordinator

import (
	"context"
	"errors"
	"strings"
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

// TestClaimLeaseLapseLeavesTheOutcomeUnknownInsteadOfReclaiming replaces a test
// that asserted the opposite.
//
// The old test was called TestClaimLeaseReclaimsStaleRunningIntents and it
// required that "a new worker can claim intent 0 again" once the lease lapsed.
// That expectation encoded a real defect: a lapsed lease means the worker
// stopped reporting, not that the robot stood still. Handing the intent back out
// re-arms a physical action whose first attempt may already have happened, which
// is the one thing core/closedloop forbids for an unknown outcome.
//
// The requirement the old test was protecting — a dead worker must not block the
// task forever — is still met, by reconciliation rather than by silent reuse.
func TestClaimLeaseLapseLeavesTheOutcomeUnknownInsteadOfReclaiming(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, 100*time.Millisecond)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(150 * time.Millisecond)

	reclaimed, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	if reclaimed != nil {
		t.Fatalf("reclaimed = %+v, want nothing claimable once the outcome is unknown", reclaimed)
	}

	snapshot, err := coordinator.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	node := snapshot.Intents[0]
	if node.Status != StatusUnknownOutcome {
		t.Fatalf("after lease lapse status = %q, want %q", node.Status, StatusUnknownOutcome)
	}
	// Who held it is the first question reconciliation asks, so it must survive.
	if node.Claimed != "robot-1" {
		t.Fatalf("claimed = %q, want robot-1 kept for the reconciliation record", node.Claimed)
	}
	if node.Error == "" {
		t.Fatal("a lapsed claim must record why the intent stopped being executable")
	}
}

func TestLateResultAfterLeaseLapseIsRefusedByNameNotByAccident(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, 100*time.Millisecond)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(150 * time.Millisecond)

	_, err = coordinator.CompleteIntent(ctx, task.ID, 0, "robot-1")
	if !errors.Is(err, ErrClaimExpired) {
		t.Fatalf("late completion err = %v, want ErrClaimExpired", err)
	}
	// The refusal must be the sentinel, not the generic not-running message: an
	// operator has to be able to tell "you were too slow" from "someone else has
	// it now", and only one of those means the robot may have moved.
	if err != nil && strings.Contains(err.Error(), "is not running on") {
		t.Fatalf("late completion reported the generic refusal: %v", err)
	}

	snapshot, err := coordinator.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Intents[0].Status != StatusUnknownOutcome {
		t.Fatalf("status after refused late completion = %q, want %q", snapshot.Intents[0].Status, StatusUnknownOutcome)
	}
}

func TestReconcileNeverActedIsTheOnlyWayBackToTheClaimablePool(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, 100*time.Millisecond)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(150 * time.Millisecond)

	snapshot, err := coordinator.ReconcileIntent(ctx, task.ID, 0, ReconcileNeverActed, "operator-li", "回放显示机械臂未启动，夹爪仍为空")
	if err != nil {
		t.Fatal(err)
	}
	node := snapshot.Intents[0]
	if node.Status != StatusReady {
		t.Fatalf("after NEVER_ACTED status = %q, want READY", node.Status)
	}
	if node.ReconciledBy != "operator-li" || node.ReconcileDecision != string(ReconcileNeverActed) || node.ReconcileNote == "" {
		t.Fatalf("reconciliation record incomplete: %+v", node)
	}

	reclaimed, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	if reclaimed == nil || reclaimed.Index != 0 {
		t.Fatalf("reclaimed = %+v, want intent 0 claimable after reconciliation", reclaimed)
	}
}

func TestReconcileAbandonFailsTheIntentRatherThanRetryingIt(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, 100*time.Millisecond)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(150 * time.Millisecond)

	snapshot, err := coordinator.ReconcileIntent(ctx, task.ID, 0, ReconcileAbandon, "operator-li", "杯子不在桌上也不在夹爪里，需要现场确认")
	if err != nil {
		t.Fatal(err)
	}
	if got := snapshot.Intents[0].Status; got != StatusFailed {
		t.Fatalf("after ABANDON status = %q, want FAILED", got)
	}
	reclaimed, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	if reclaimed != nil {
		t.Fatalf("reclaimed = %+v, want an abandoned intent to stay unclaimable", reclaimed)
	}
}

func TestReconcileRequiresAPersonAndAReasonAndChangesNothingWithoutThem(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, 100*time.Millisecond)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(150 * time.Millisecond)

	for name, call := range map[string]func() error{
		"no person": func() error {
			_, err := coordinator.ReconcileIntent(ctx, task.ID, 0, ReconcileNeverActed, "  ", "回放显示未启动")
			return err
		},
		"no reason": func() error {
			_, err := coordinator.ReconcileIntent(ctx, task.ID, 0, ReconcileNeverActed, "operator-li", "   ")
			return err
		},
		"unknown decision": func() error {
			_, err := coordinator.ReconcileIntent(ctx, task.ID, 0, ReconcileDecision("MAYBE"), "operator-li", "不确定")
			return err
		},
	} {
		if err := call(); err == nil {
			t.Fatalf("%s: expected a refusal", name)
		}
		snapshot, snapErr := coordinator.Snapshot(ctx, task.ID)
		if snapErr != nil {
			t.Fatal(snapErr)
		}
		if snapshot.Intents[0].Status != StatusUnknownOutcome {
			t.Fatalf("%s: a refused reconciliation mutated the intent to %q", name, snapshot.Intents[0].Status)
		}
	}
}

func TestReconcileRefusesAnIntentThatIsNotAwaitingReconciliation(t *testing.T) {
	coordinator, _ := newTestCoordinator(t, time.Minute)
	ctx := context.Background()
	task, err := coordinator.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil {
		t.Fatal(err)
	}
	// Still RUNNING and inside its lease: this is an ordinary in-flight step and
	// reconciliation must not be usable to shortcut it back to READY.
	if _, err := coordinator.ReconcileIntent(ctx, task.ID, 0, ReconcileNeverActed, "operator-li", "想直接重跑"); !errors.Is(err, ErrReconcileRequired) {
		t.Fatalf("err = %v, want ErrReconcileRequired", err)
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

// --- what confirmed a completion is part of the record -------------------------

// A coordinator with a world used it only for the shared-block handoff, and
// recorded every other intent as SUCCEEDED without consulting the world at all.
// So an object moved and an object confirmed moved produced the same record.
//
// This is the refusal that replaced the silent skip: a scene-changing intent the
// coordinator has no predicate for is not accepted on the worker's word while a
// world sits unused.
func TestASceneChangingIntentIsNotAcceptedUnverified(t *testing.T) {
	ctx := context.Background()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	hub := worldhub.New("fleet-default", time.Minute, 32)
	coordinator := NewWithStore(service, time.Minute, eventlog.NewMemoryStore()).
		WithWorld(hub, time.Minute).
		WithResourceLeases(lease.NewMemoryManager(), time.Minute)

	node, err := coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || node == nil {
		t.Fatalf("claim = %+v (err=%v)", node, err)
	}
	_, err = coordinator.CompleteIntent(ctx, task.ID, 0, "robot-1")
	if !errors.Is(err, ErrIntentUnverifiable) {
		t.Fatalf("err = %v, want ErrIntentUnverifiable", err)
	}
	snapshot, snapErr := coordinator.Snapshot(ctx, task.ID)
	if snapErr != nil {
		t.Fatal(snapErr)
	}
	if snapshot.Intents[0].Status == StatusSucceeded {
		t.Fatal("an unverifiable intent was recorded as SUCCEEDED anyway")
	}
}

// The other half of the same rule: with no world configured the coordinator
// cannot check anything, and it says so in the record instead of looking like one
// that did.
func TestACompletionWithNoWorldSaysItRestsOnTheWorkerReport(t *testing.T) {
	coordinator, service := newTestCoordinator(t, time.Minute)
	ctx := context.Background()
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.NextIntent(ctx, task.ID, "robot-1"); err != nil {
		t.Fatal(err)
	}
	if _, err := coordinator.CompleteIntent(ctx, task.ID, 0, "robot-1"); err != nil {
		t.Fatalf("complete: %v", err)
	}
	snapshot, err := coordinator.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got := snapshot.Intents[0].VerificationBasis; got != "WORKER_REPORT_NO_WORLD" {
		t.Fatalf("verificationBasis = %q, want WORKER_REPORT_NO_WORLD", got)
	}
}
