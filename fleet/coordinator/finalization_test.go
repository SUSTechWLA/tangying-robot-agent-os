package coordinator

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

const finalizationCrash = "injected coordinator process crash"

type crashFinalizationStore struct {
	eventlog.Store
	event string
	after bool
}

func (s crashFinalizationStore) Commit(ctx context.Context, req eventlog.CommitRequest) error {
	matched := len(req.Events) == 1 && req.Events[0].EventType == s.event
	if matched && !s.after {
		panic(finalizationCrash)
	}
	err := s.Store.Commit(ctx, req)
	if err == nil && matched {
		panic(finalizationCrash)
	}
	return err
}

type crashReleaseLease struct{ lease.Manager }

func (m crashReleaseLease) Release(ctx context.Context, resource, owner string, token uint64) error {
	if err := m.Manager.Release(ctx, resource, owner, token); err != nil {
		return err
	}
	panic(finalizationCrash)
}
func expectFinalizationCrash(t *testing.T, f func()) {
	t.Helper()
	defer func() {
		if recovered := recover(); recovered != finalizationCrash {
			t.Fatalf("crash=%v", recovered)
		}
	}()
	f()
}

// The panic occurs inside the real write path, then both the task repository
// and coordinator database are closed/reopened. No Runtime exists in this test.
func TestDurableFinalizationRecoversEveryCoordinatorCrashBoundary(t *testing.T) {
	for _, phase := range []string{"success-commit", "release", "activation", "finalized-commit", "terminal-transition"} {
		t.Run(phase, func(t *testing.T) {
			ctx := context.Background()
			path := filepath.Join(t.TempDir(), "cloud.db")
			c, node := claimedCompletionFixtureAt(t, path)
			db := c.store.(*sqlite.Store)
			manager := c.resources
			id := node.ContextBasis.TaskID
			if phase != "terminal-transition" {
				proposal, err := c.ProposeRevision(ctx, tasks.ProposeRevisionCommand{TaskID: id, ExpectedRevision: 1, Request: "让1号机器人把蓝色杯子放进左侧收纳盒", IdempotencyKey: "finalization-next", Creator: "operator"})
				if err != nil {
					t.Fatal(err)
				}
				if _, err := c.ConfirmRevision(ctx, id, proposal.Revision.Revision, 1, "finalization-confirm"); err != nil {
					t.Fatal(err)
				}
			}
			switch phase {
			case "success-commit":
				c.store = crashFinalizationStore{db, "INTENT_SUCCEEDED", true}
			case "release":
				c.resources = crashReleaseLease{manager}
			case "activation":
				c.store = crashFinalizationStore{db, "REVISION_RECONCILED", true}
			case "finalized-commit":
				c.store = crashFinalizationStore{db, "COMPLETION_FINALIZED", true}
			case "terminal-transition":
				c.store = crashFinalizationStore{db, "COMPLETION_FINALIZED", false}
			}
			expectFinalizationCrash(t, func() {
				_, _ = c.CompleteIntentRevision(ctx, id, node.TaskRevision, node.AggregateVersion, node.Index, node.StepID, node.Claimed, node.CommandID, node.FencingToken, node.ContextBasis)
			})
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			reopened, err := sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer reopened.Close()
			service := tasks.NewService(reopened, intent.NewDeterministicParser())
			restarted := NewWithStore(service, time.Minute, reopened).WithResourceLeases(manager, time.Minute)
			// Replace the spent robot lease before recovery. Finalization must
			// not release this newer owner's grant.
			_ = manager.Release(ctx, node.ResourceID, node.LeaseOwner, node.FencingToken)
			later, err := manager.Acquire(ctx, node.ResourceID, "later-owner", time.Minute)
			if err != nil {
				t.Fatal(err)
			}
			var deliveries int
			restarted.WithEnqueue(func(context.Context, string, []string) error { deliveries++; return nil })
			for range 2 {
				if _, err := restarted.DispatchOutbox(ctx, 10); err != nil {
					t.Fatal(err)
				}
				if _, err := restarted.CompleteIntentRevision(ctx, id, node.TaskRevision, node.AggregateVersion, node.Index, node.StepID, node.Claimed, node.CommandID, node.FencingToken, node.ContextBasis); err != nil {
					t.Fatal(err)
				}
			}
			if err := manager.Validate(ctx, later.ResourceID, later.Owner, later.Token); err != nil {
				t.Fatalf("newer lease was released: %v", err)
			}
			snapshot, err := restarted.Snapshot(ctx, id)
			if err != nil {
				t.Fatal(err)
			}
			if phase == "terminal-transition" {
				if snapshot.State != "SUCCEEDED" || snapshot.TaskRevision != 1 || deliveries != 0 {
					t.Fatalf("terminal=%+v deliveries=%d", snapshot, deliveries)
				}
			} else if snapshot.TaskRevision != 2 || snapshot.Intents[0].Status != StatusReady || deliveries != 1 {
				t.Fatalf("revision=%+v deliveries=%d", snapshot, deliveries)
			}
			events, err := restarted.DomainEvents(ctx, id, 0, 1000)
			if err != nil {
				t.Fatal(err)
			}
			successes, finalized := 0, 0
			for _, event := range events {
				if event.EventType == "INTENT_SUCCEEDED" {
					successes++
				}
				if event.EventType == "COMPLETION_FINALIZED" {
					finalized++
				}
			}
			if successes != 1 || finalized != 1 {
				t.Fatalf("success events=%d finalization events=%d", successes, finalized)
			}
		})
	}
}

func TestSharedCompletionNeverRollsBackNewWorldFenceAndStopsForUnpublishedExpiredGrant(t *testing.T) {
	for _, scenario := range []string{"publish", "newer-world", "expired-unpublished"} {
		t.Run(scenario, func(t *testing.T) {
			ctx := context.Background()
			c, hub, _, id := newHandoffCoordinator(t, func(context.Context, string) (string, error) { return "catalog-v1", nil })
			path := filepath.Join(t.TempDir(), "coordinator.db")
			db, err := sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			c.store = db
			node, err := c.NextIntent(ctx, id, "robot-1")
			if err != nil {
				t.Fatal(err)
			}
			now := time.Now().UTC()
			for _, event := range []observation.Envelope{handoffEntityObservationFrom(now, 1, "robot-1"), handoffEntityObservationFrom(now.Add(time.Millisecond), 2, "robot-1"), robotHeldObservation(now.Add(2*time.Millisecond), "robot-1", 1, "")} {
				if _, err := hub.Ingest(ctx, event); err != nil {
					t.Fatal(err)
				}
			}
			c.store = crashFinalizationStore{db, "BLOCK_AVAILABLE", true}
			expectFinalizationCrash(t, func() {
				_, _ = c.CompleteIntentRevision(ctx, id, node.TaskRevision, node.AggregateVersion, node.Index, node.StepID, node.Claimed, node.CommandID, node.FencingToken, node.ContextBasis)
			})
			c.store = db
			receipt, err := c.completionRecord(ctx, node.ContextBasis)
			if err != nil {
				t.Fatal(err)
			}
			grant := *receipt.Effects.Transition
			if scenario == "newer-world" {
				grant, err = c.resources.Transfer(ctx, grant.ResourceID, grant.Owner, "newer-owner", grant.Token, time.Minute)
				if err != nil {
					t.Fatal(err)
				}
				if err := c.publishResource(ctx, grant); err != nil {
					t.Fatal(err)
				}
			}
			if scenario == "expired-unpublished" {
				if err := c.resources.Release(ctx, grant.ResourceID, grant.Owner, grant.Token); err != nil {
					t.Fatal(err)
				}
			}
			if err := db.Close(); err != nil {
				t.Fatal(err)
			}
			reopened, err := sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer reopened.Close()
			restarted := NewWithStore(c.service, time.Minute, reopened).WithWorld(hub, time.Minute).WithResourceLeases(c.resources, time.Minute).WithEnqueue(func(context.Context, string, []string) error { return nil })
			_, err = restarted.DispatchOutbox(ctx, 10)
			if scenario == "expired-unpublished" {
				if !errors.Is(err, lease.ErrLeaseNotFound) {
					t.Fatalf("unpublished expired transfer err=%v", err)
				}
				if next, err := restarted.NextIntent(ctx, id, "robot-2"); err == nil || next != nil {
					t.Fatalf("unsafe next=%v err=%v", next, err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			world, err := hub.Snapshot(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if got := world.Resources[grant.ResourceID]; got.FencingToken != grant.Token || got.Owner != grant.Owner {
				t.Fatalf("resource rollback: got=%+v want=%+v", got, grant)
			}
			if err := c.resources.Validate(ctx, grant.ResourceID, grant.Owner, grant.Token); err != nil {
				t.Fatal(err)
			}
		})
	}
}
