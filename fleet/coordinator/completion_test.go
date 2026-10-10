package coordinator

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func claimedCompletionFixture(t *testing.T) (*Coordinator, *IntentNode) {
	return claimedCompletionFixtureAt(t, filepath.Join(t.TempDir(), "cloud.db"))
}

func claimedCompletionFixtureAt(t *testing.T, path string) (*Coordinator, *IntentNode) {
	t.Helper()
	ctx := context.Background()
	db, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	service := tasks.NewService(db, intent.NewDeterministicParser())
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err == nil {
		task, err = service.Approve(ctx, task.ID, "test-operator")
	}
	if err != nil {
		t.Fatal(err)
	}
	c := NewWithStore(service, time.Minute, db).WithResourceLeases(lease.NewMemoryManager(), time.Minute)
	node, err := c.NextIntent(ctx, task.ID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	return c, node
}

func completionFixture(t *testing.T) (*Coordinator, *IntentNode) {
	t.Helper()
	ctx := context.Background()
	c, node := claimedCompletionFixture(t)
	id := node.ContextBasis.TaskID
	// Cross a pagination boundary between the original claim and completion.
	for i := 0; i < 101; i++ {
		if _, err := c.AppendTaskEvent(ctx, id, tasks.TaskEvent{Type: "TEST_TRANSPORT_PROGRESS"}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := c.CompleteIntentRevision(ctx, id, node.TaskRevision, node.AggregateVersion,
		node.Index, node.StepID, node.Claimed, node.CommandID, node.FencingToken, node.ContextBasis); err != nil {
		t.Fatal(err)
	}
	return c, node
}

func TestUncommittedContextCompletionStillRequiresApprovedUnchangedTask(t *testing.T) {
	for _, change := range []string{"approval", "execution"} {
		t.Run(change, func(t *testing.T) {
			ctx := context.Background()
			c, node := claimedCompletionFixture(t)
			task, err := c.service.Get(ctx, node.ContextBasis.TaskID)
			if err != nil {
				t.Fatal(err)
			}
			if change == "approval" {
				task.Approved = false
			} else {
				task.Intent.Action = "changed-without-revision"
			}
			if err := c.store.(*sqlite.Store).Update(ctx, task); err != nil {
				t.Fatal(err)
			}
			if _, err := c.CompleteIntentRevision(ctx, task.ID, node.TaskRevision, node.AggregateVersion,
				node.Index, node.StepID, node.Claimed, node.CommandID, node.FencingToken, node.ContextBasis); err == nil {
				t.Fatal("uncommitted completion accepted a changed task")
			}
			if found, err := c.completedContext(ctx, node.ContextBasis); err != nil || found {
				t.Fatalf("invalid task produced receipt: found=%v err=%v", found, err)
			}
		})
	}
}

func TestCompletionReceiptRequiresExactContextAfterDurableRoundTrip(t *testing.T) {
	c, node := completionFixture(t)
	ctx := context.Background()
	state, found, err := c.store.LoadState(ctx, node.ContextBasis.TaskID)
	if err != nil || !found {
		t.Fatalf("state found=%v err=%v", found, err)
	}
	for _, field := range []string{"exact", "robot", "scope", "revision", "step", "command", "fence", "claim", "execution", "world", "catalog"} {
		t.Run(field, func(t *testing.T) {
			b := *node.ContextBasis
			switch field {
			case "robot":
				b.RobotID = "robot-2"
			case "scope":
				b.Scope = "another.scope"
			case "revision":
				b.TaskRevision++
				b.CommandID = contextcontract.CommandID(b.TaskID, b.TaskRevision, b.StepID)
			case "step":
				b.StepID += "-changed"
				b.CommandID = contextcontract.CommandID(b.TaskID, b.TaskRevision, b.StepID)
			case "command":
				b.CommandID += "-changed"
			case "fence":
				b.FencingToken++
			case "claim":
				b.ClaimVersion++
			case "execution":
				b.ExecutionDigest, _ = contextcontract.Fingerprint("different plan")
			case "world":
				b.World = contextcontract.WorldBasis{Mode: "coordinator_world", WorldID: "another-world"}
			case "catalog":
				b.CatalogRevision = "another-catalog"
			}
			if sealed, err := b.Seal(); err == nil {
				b = sealed
			}
			_, err := c.CompleteIntentRevision(ctx, b.TaskID, b.TaskRevision, b.AggregateVersion,
				b.IntentIndex, b.StepID, b.RobotID, b.CommandID, b.FencingToken, &b)
			if field == "exact" && err != nil {
				t.Fatal(err)
			}
			if field != "exact" && !errors.Is(err, ErrIntentIdentityConflict) && !errors.Is(err, ErrStaleTaskRevision) {
				t.Fatalf("changed %s completion err=%v", field, err)
			}
		})
	}
	after, _, err := c.store.LoadState(ctx, node.ContextBasis.TaskID)
	if err != nil || after.Version != state.Version {
		t.Fatalf("receipt replay wrote graph: before=%d after=%d err=%v", state.Version, after.Version, err)
	}
}

type damagedCompletionStore struct {
	eventlog.Store
	mode string
}

func (s damagedCompletionStore) ListEvents(ctx context.Context, kind, id string, cursor uint64, limit int) ([]eventlog.DomainEvent, error) {
	if s.mode == "read-error" {
		return nil, errors.New("injected durable event read failure")
	}
	events, err := s.Store.ListEvents(ctx, kind, id, cursor, limit)
	if err != nil {
		return nil, err
	}
	for i := range events {
		e := &events[i]
		wire, ok := e.Payload["completionReceiptJSON"].(string)
		if !ok {
			continue
		}
		if s.mode == "wrong-type" {
			e.EventType = "INTENT_FAILED"
			continue
		}
		if s.mode == "wrong-aggregate" {
			e.AggregateID = "another-task"
			continue
		}
		var receipt completionReceipt
		_ = json.Unmarshal([]byte(wire), &receipt)
		switch s.mode {
		case "corrupt-digest":
			receipt.Basis.FencingToken++
		case "wrong-schema":
			receipt.SchemaVersion = "unknown"
		case "missing-verification":
			receipt.VerificationBasis = ""
		case "missing-completion-time":
			receipt.CompletedAt = time.Time{}
		}
		data, _ := json.Marshal(receipt)
		e.Payload["completionReceiptJSON"] = string(data)
	}
	return events, nil
}

func TestCompletionReceiptCorruptOrUnreadableEvidenceFailsClosed(t *testing.T) {
	for _, mode := range []string{"read-error", "wrong-type", "wrong-aggregate", "corrupt-digest", "wrong-schema", "missing-verification", "missing-completion-time"} {
		t.Run(mode, func(t *testing.T) {
			c, node := completionFixture(t)
			c.store = damagedCompletionStore{Store: c.store, mode: mode}
			if _, err := c.CompleteIntentRevision(context.Background(), node.ContextBasis.TaskID,
				node.TaskRevision, node.AggregateVersion, node.Index, node.StepID, node.Claimed,
				node.CommandID, node.FencingToken, node.ContextBasis); err == nil {
				t.Fatal("receipt corruption fell through to successful active-node projection")
			}
		})
	}
}
