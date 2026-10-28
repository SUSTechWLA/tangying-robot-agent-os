package edgeagent

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

// Hold the catalogue RPC in the final scoped service guard, after the executor
// has reserved the step but before any robot action has been dispatched. The
// caller can revoke authority while this read is in flight, without sleeps or
// assumptions about the number of earlier planning/catalogue reads.
type dispatchCatalogueBarrier struct {
	*missionRobot
	store   middleware.ExecutionStore
	childID string
	stepID  string
	entered chan struct{}
	release chan struct{}
	once    sync.Once
}

func (p *dispatchCatalogueBarrier) ListServices(ctx context.Context) (*robotv1.ServiceCatalog, error) {
	state, err := p.store.StepStatus(ctx, p.childID, p.stepID)
	if err != nil {
		return nil, err
	}
	if state == middleware.StepStarted {
		p.once.Do(func() {
			close(p.entered)
			select {
			case <-p.release:
			case <-ctx.Done():
			}
		})
	}
	return p.missionRobot.ListServices(ctx)
}

func TestDelegatedDispatchRechecksAuthorityAfterSlowCatalogue(t *testing.T) {
	for _, change := range []string{"cancelled", "revision", "approval", "control"} {
		t.Run(change, func(t *testing.T) {
			robot := &missionRobot{}
			contract, decider := objectiveFixture(t, robot, 1)
			host, task, store := hostFixture(t, robot, contract, decider)
			provider := &dispatchCatalogueBarrier{
				missionRobot: robot, store: store,
				childID: fmt.Sprintf("%s-edge-r%d-p1", task.ID, task.CurrentRevision),
				stepID:  fmt.Sprintf("rev-%d-cap-01", task.CurrentRevision),
				entered: make(chan struct{}), release: make(chan struct{}),
			}
			host.Provider = provider
			var revoked atomic.Bool
			before := func(context.Context) error {
				if change == "control" && revoked.Load() {
					return errors.New("execution control revoked during catalogue read")
				}
				return nil
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			done := make(chan error, 1)
			go func() {
				_, err := host.Run(ctx, task, before, nil)
				done <- err
			}()
			select {
			case <-provider.entered:
			case err := <-done:
				t.Fatalf("execution did not reach the dispatch guard: %v", err)
			case <-ctx.Done():
				t.Fatal("dispatch guard was not reached")
			}
			// Change the authoritative store, not the immutable input to Run.
			current, err := host.Tasks.Get(ctx, task.ID)
			if err != nil {
				t.Fatal(err)
			}
			switch change {
			case "cancelled":
				current.State = taskgraph.StateCancelled
			case "revision":
				current.CurrentRevision++
			case "approval":
				current.Approved = false
			}
			if change != "control" {
				if err := store.Update(ctx, current); err != nil {
					t.Fatal(err)
				}
			}
			revoked.Store(true)
			close(provider.release)
			select {
			case err = <-done:
			case <-ctx.Done():
				t.Fatal("execution did not stop after authority changed")
			}
			if err == nil || len(robot.writes) != 0 {
				t.Fatalf("stale authority dispatched movement: change=%s writes=%v error=%v", change, robot.writes, err)
			}
			t.Logf("authority_change=%s reserved_steps=1 physical_dispatches=0 error=%v", change, err)
		})
	}
}
