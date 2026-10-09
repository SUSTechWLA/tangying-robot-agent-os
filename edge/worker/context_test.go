package worker

import (
	"context"
	"errors"
	"path/filepath"
	"sync"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
)

// Only synthetic transport fixtures use this helper. Production bases are
// created by the coordinator and stored with its claim event/checkpoint.
func contextFixture(t *testing.T, cloud *activityCloud) *sqlite.Store {
	return contextFixtureAt(t, cloud, filepath.Join(t.TempDir(), "execution.db"))
}

func contextFixtureAt(t *testing.T, cloud *activityCloud, path string) *sqlite.Store {
	t.Helper()
	cloud.task.Approved = true
	node := cloud.node
	node.Claimed, node.Status = "robot-1", coordinator.StatusRunning
	node.CommandID = contextcontract.CommandID(cloud.task.ID, node.TaskRevision, node.StepID)
	node.ResourceID, node.FencingToken, node.CatalogRevision = "robot:robot-1", 1, "fixture-catalog"
	var err error
	node.ContextBasis, err = coordinator.NewContextBasis(cloud.task, node, 10, "")
	if err != nil {
		t.Fatal(err)
	}
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return store
}

func (c *activityCloud) RenewIntentRevision(context.Context, string, *coordinator.IntentNode, string) error {
	return nil
}

func TestContextRejectsMixedMissingAndForeignBasisBeforeInvoking(t *testing.T) {
	for _, mutation := range []string{"missing", "schema", "robot", "scope", "digest", "aggregate", "command", "revision", "approval", "world", "plan"} {
		t.Run(mutation, func(t *testing.T) {
			_, _, cloud := policyTask(t)
			path := filepath.Join(t.TempDir(), "execution.db")
			store := contextFixtureAt(t, cloud, path)
			node := cloud.node
			switch mutation {
			case "missing":
				node.ContextBasis = nil
			case "schema":
				node.ContextBasis.SchemaVersion = "future"
			case "robot":
				node.Claimed = "robot-other"
			case "scope":
				node.ContextBasis.Scope = "other.intent"
			case "digest":
				node.ContextBasis.ExecutionDigest = "bad"
			case "aggregate":
				cloud.task.AggregateVersion = 0
			case "command":
				node.CommandID += "/another"
			case "revision":
				cloud.task.CurrentRevision++
			case "approval":
				cloud.task.Approved = false
			case "world":
				node.ContextBasis.World = contextcontract.WorldBasis{Mode: "coordinator_world", WorldID: "another-world"}
				basis, err := node.ContextBasis.Seal()
				if err != nil {
					t.Fatal(err)
				}
				node.ContextBasis = &basis
			case "plan":
				cloud.task.Intent.Action = "different-action"
			}
			r := &learnedRuntime{}
			w := New(Config{RobotID: "robot-1", Cloud: cloud, Runtime: r, ExecutionStore: store})
			if err := w.processTask(context.Background(), cloud.task.ID); err == nil || len(r.commands) != 0 || cloud.failed {
				t.Fatalf("err=%v invokes=%d failed=%v", err, len(r.commands), cloud.failed)
			}
		})
	}
}

func TestContextConcurrentWorkersOnlyOneAccepts(t *testing.T) {
	_, _, cloud := policyTask(t)
	store := contextFixture(t, cloud)
	node := cloud.node
	r := &learnedRuntime{}
	reads := &sync.WaitGroup{}
	reads.Add(2)
	releaseReads := make(chan struct{})
	barrier := barrierContextStore{Store: store, reads: reads, release: releaseReads}
	start := make(chan struct{})
	type result struct {
		phase string
		err   error
	}
	results := make(chan result, 2)
	var group sync.WaitGroup
	for range 2 {
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			w := New(Config{RobotID: "robot-1", Adapter: "mujoco", RobotModel: "xlerobot-sim", TransformRevision: "mujoco-world-v1", Cloud: cloud, ExecutionStore: barrier, Runtime: r, Policy: &recordingPolicy{manifest: policyTestManifest()}})
			phase, err := w.acceptContext(context.Background(), cloud.task, node)
			if err == nil && phase == contextAccepted {
				err = w.runIntent(context.Background(), cloud.task, node)
			}
			results <- result{phase, err}
		}()
	}
	close(start)
	// Force both contenders to read version zero before either commits, so
	// this exercises database CAS rather than merely the completed-state gate.
	reads.Wait()
	close(releaseReads)
	group.Wait()
	close(results)
	accepted := 0
	for result := range results {
		t.Logf("phase=%s err=%v", result.phase, result.err)
		if result.err == nil && result.phase == contextAccepted {
			accepted++
		}
	}
	if accepted != 1 {
		t.Fatalf("accepted=%d", accepted)
	}
	events, err := store.ListEvents(context.Background(), "edge_context", contextKey(cloud.task.ID, "robot-1"), 0, 10)
	if err != nil || len(events) != 3 || events[0].EventType != "CONTEXT_ACCEPTED" {
		t.Fatalf("events=%v err=%v", events, err)
	}
	picks := 0
	for _, command := range r.commands {
		if command.Capability == "manipulation.pick" {
			picks++
		}
	}
	if picks != 1 {
		t.Fatalf("concurrent physical picks=%d", picks)
	}
}

type barrierContextStore struct {
	*sqlite.Store
	reads   *sync.WaitGroup
	release <-chan struct{}
}

func (s barrierContextStore) LoadState(ctx context.Context, key string) (eventlog.AggregateState, bool, error) {
	state, found, err := s.Store.LoadState(ctx, key)
	if err == nil && !found {
		s.reads.Done()
		<-s.release
	}
	return state, found, err
}

func TestContextRestartNeverReplaysAcceptedButReplaysExecutedCompletion(t *testing.T) {
	for _, phase := range []string{contextAccepted, contextExecuted, contextCompleted} {
		t.Run(phase, func(t *testing.T) {
			_, _, cloud := policyTask(t)
			path := filepath.Join(t.TempDir(), "execution.db")
			store := contextFixtureAt(t, cloud, path)
			node := cloud.node
			w := New(Config{RobotID: "robot-1", Cloud: cloud, ExecutionStore: store})
			if _, err := w.acceptContext(context.Background(), cloud.task, node); err != nil {
				t.Fatal(err)
			}
			if phase != contextAccepted {
				if err := w.markContextExecuted(context.Background(), cloud.task.ID, node); err != nil {
					t.Fatal(err)
				}
			}
			if phase == contextCompleted {
				if err := w.completeContext(context.Background(), cloud.task.ID, node); err != nil {
					t.Fatal(err)
				}
			}
			if err := store.Close(); err != nil {
				t.Fatal(err)
			}
			reopened, err := sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer reopened.Close()
			r := &learnedRuntime{}
			restarted := New(Config{RobotID: "robot-1", Cloud: cloud, Runtime: r, ExecutionStore: reopened})
			err = restarted.processTask(context.Background(), cloud.task.ID)
			if phase == contextAccepted && !errors.Is(err, ErrHandoffUncertain) {
				t.Fatalf("err=%v", err)
			}
			if phase != contextAccepted && err != nil {
				t.Fatal(err)
			}
			if len(r.commands) != 0 {
				t.Fatal("restart repeated physical work")
			}
			_, saved, found, err := restarted.loadContext(context.Background(), cloud.task.ID)
			if err != nil || !found || (phase == contextAccepted && saved.Phase != contextAccepted) || (phase != contextAccepted && saved.Phase != contextCompleted) {
				t.Fatalf("saved=%v err=%v", saved, err)
			}
		})
	}
}

type failingContextStore struct {
	*sqlite.Store
	failRead  bool
	failPhase string
}

func (s failingContextStore) LoadState(ctx context.Context, key string) (eventlog.AggregateState, bool, error) {
	if s.failRead {
		return eventlog.AggregateState{}, false, errors.New("injected database read failure")
	}
	return s.Store.LoadState(ctx, key)
}
func (s failingContextStore) Commit(ctx context.Context, req eventlog.CommitRequest) error {
	if len(req.Events) > 0 && req.Events[0].EventType == "CONTEXT_"+s.failPhase {
		return errors.New("injected durable commit failure")
	}
	return s.Store.Commit(ctx, req)
}

func TestContextDatabaseFailuresFailClosed(t *testing.T) {
	for _, fault := range []string{"read", contextAccepted, contextExecuted, contextCompleted} {
		t.Run(fault, func(t *testing.T) {
			_, _, cloud := policyTask(t)
			store := contextFixture(t, cloud)
			node := cloud.node
			broken := failingContextStore{Store: store, failRead: fault == "read", failPhase: fault}
			w := New(Config{RobotID: "robot-1", Cloud: cloud, ExecutionStore: broken})
			_, err := w.acceptContext(context.Background(), cloud.task, node)
			if fault == "read" || fault == contextAccepted {
				if err == nil {
					t.Fatal("accepted without durable context")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			err = w.markContextExecuted(context.Background(), cloud.task.ID, node)
			if fault == contextExecuted {
				if !errors.Is(err, ErrHandoffUncertain) {
					t.Fatalf("err=%v", err)
				}
			} else {
				if err != nil {
					t.Fatal(err)
				}
				if err = w.completeContext(context.Background(), cloud.task.ID, node); !errors.Is(err, ErrCompletionPending) {
					t.Fatalf("err=%v", err)
				}
			}
			_, saved, _, err := New(Config{RobotID: "robot-1", ExecutionStore: store}).loadContext(context.Background(), cloud.task.ID)
			want := contextAccepted
			if fault == contextCompleted {
				want = contextExecuted
			}
			if err != nil || saved.Phase != want {
				t.Fatalf("saved=%v err=%v", saved, err)
			}
		})
	}
}

type failingAckStore struct {
	*sqlite.Store
	after bool
}

func (s failingAckStore) AckOutbox(ctx context.Context, id string) error {
	if s.after {
		if err := s.Store.AckOutbox(ctx, id); err != nil {
			return err
		}
	}
	return errors.New("injected lost ack")
}

func TestCompletedCheckpointSurvivesAckFailureAndReopen(t *testing.T) {
	for _, after := range []bool{false, true} {
		t.Run(map[bool]string{false: "before-ack", true: "after-ack"}[after], func(t *testing.T) {
			_, _, cloud := policyTask(t)
			path := filepath.Join(t.TempDir(), "execution.db")
			store := contextFixtureAt(t, cloud, path)
			node := cloud.node
			w := New(Config{RobotID: "robot-1", Cloud: cloud, ExecutionStore: failingAckStore{store, after}})
			if _, err := w.acceptContext(context.Background(), cloud.task, node); err != nil {
				t.Fatal(err)
			}
			if err := w.markContextExecuted(context.Background(), cloud.task.ID, node); err != nil {
				t.Fatal(err)
			}
			if err := w.completeContext(context.Background(), cloud.task.ID, node); err == nil {
				t.Fatal("ack failure hidden")
			}
			if err := store.Close(); err != nil {
				t.Fatal(err)
			}
			reopened, err := sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer reopened.Close()
			r := &learnedRuntime{}
			restarted := New(Config{RobotID: "robot-1", Cloud: cloud, Runtime: r, ExecutionStore: reopened})
			if err := restarted.processTask(context.Background(), cloud.task.ID); err != nil {
				t.Fatal(err)
			}
			entries, err := reopened.ClaimOutbox(context.Background(), 10)
			if err != nil || len(entries) != 0 || len(r.commands) != 0 {
				t.Fatalf("pending=%v calls=%d err=%v", entries, len(r.commands), err)
			}
		})
	}
}

func TestOutOfOrderAndConflictingContextNeverExecutes(t *testing.T) {
	_, _, cloud := policyTask(t)
	store := contextFixture(t, cloud)
	node := cloud.node
	w := New(Config{RobotID: "robot-1", Cloud: cloud, ExecutionStore: store})
	if _, err := w.acceptContext(context.Background(), cloud.task, node); err != nil {
		t.Fatal(err)
	}
	if err := w.markContextExecuted(context.Background(), cloud.task.ID, node); err != nil {
		t.Fatal(err)
	}
	if err := w.completeContext(context.Background(), cloud.task.ID, node); err != nil {
		t.Fatal(err)
	}
	for _, version := range []uint64{9, 10} {
		copy := *node
		basis := *node.ContextBasis
		basis.ClaimVersion = version
		if version == 10 {
			copy.CatalogRevision = "other-catalog"
			basis.CatalogRevision = copy.CatalogRevision
		}
		sealed, err := basis.Seal()
		if err != nil {
			t.Fatal(err)
		}
		copy.ContextBasis = &sealed
		if _, err := w.acceptContext(context.Background(), cloud.task, &copy); !errors.Is(err, contextcontract.ErrInvalid) {
			t.Fatalf("version=%d err=%v", version, err)
		}
	}
}

func TestChangedRevisionDuringPolicyCannotDispatchPhysicalCommand(t *testing.T) {
	_, _, cloud := policyTask(t)
	store := contextFixture(t, cloud)
	r := &learnedRuntime{}
	changed := false
	provider := &delayedPolicy{recordingPolicy: recordingPolicy{manifest: policyTestManifest()}, after: func() { changed = true; cloud.task.CurrentRevision++ }}
	w := New(Config{RobotID: "robot-1", Adapter: "mujoco", RobotModel: "xlerobot-sim", TransformRevision: "mujoco-world-v1", Cloud: cloud, Runtime: r, Policy: provider, ExecutionStore: store})
	if err := w.processTask(context.Background(), cloud.task.ID); err == nil {
		t.Fatal("changed revision was dispatched")
	}
	if !changed {
		t.Fatal("test never reached the policy/context boundary")
	}
	for _, command := range r.commands {
		if command.Capability == "manipulation.pick" || command.Capability == "manipulation.place" {
			t.Fatal("physical command dispatched after context changed")
		}
	}
}
