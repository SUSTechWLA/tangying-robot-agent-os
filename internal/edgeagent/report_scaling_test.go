package edgeagent

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type countedChildLoads struct {
	*sqlite.Store
	loads map[string]int
	fail  bool
}

func (s *countedChildLoads) LoadState(ctx context.Context, key string) (eventstore.AggregateState, bool, error) {
	if strings.HasPrefix(key, "edge-child/") {
		s.loads[key]++
		if s.fail {
			return eventstore.AggregateState{}, false, fmt.Errorf("injected journal unavailable")
		}
	}
	return s.Store.LoadState(ctx, key)
}

func TestGoalReportLoadsEachChildOncePerInvocationAndReloadsOnRetry(t *testing.T) {
	ctx := context.Background()
	robot := &missionRobot{}
	contract, decider := objectiveFixture(t, robot, 6)
	host, task, store := hostFixture(t, robot, contract, decider)
	if _, err := host.Run(ctx, task, nil, nil); err != nil {
		t.Fatal(err)
	}
	cp := campaignCheckpoint(t, store, task)
	if !cp.Complete || len(cp.Completed) != 6 {
		t.Fatalf("incomplete fixture: %+v", cp)
	}
	counter := &countedChildLoads{Store: store, loads: map[string]int{}}
	host.Store = counter
	for invocation := 1; invocation <= 2; invocation++ {
		if _, err := host.reportGoal(ctx, task, contract, cp.Proof); err != nil {
			t.Fatal(err)
		}
		if len(counter.loads) != 1 {
			t.Fatalf("unexpected children: %+v", counter.loads)
		}
		for child, count := range counter.loads {
			if count != invocation {
				t.Fatalf("child %s loaded %d times in %d reports", child, count, invocation)
			}
		}
	}
	counter.fail = true
	if _, err := host.finishGoal(ctx, task, contract, cp.Proof); err == nil {
		t.Fatal("retry reused stale journal after durable reader failed")
	}
	if len(robot.writes) != 6 || decider.rounds != 1 {
		t.Fatalf("report replay executed work: writes=%d plans=%d", len(robot.writes), decider.rounds)
	}
	parent, err := host.Tasks.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	verified := 0
	for _, event := range parent.Events {
		if event.Type == "CAPABILITY_VERIFIED" {
			verified++
		}
	}
	if verified != 3 {
		t.Fatalf("completion evidence lost or falsely committed: %d", verified)
	}
}

type legacyEventPort struct {
	capabilityEvents
	appends int
}

func (p *legacyEventPort) AppendEvent(ctx context.Context, id string, event tasks.TaskEvent) (*tasks.Task, error) {
	p.appends++
	return p.capabilityEvents.AppendEvent(ctx, id, event)
}
func TestParentEventRecorderKeepsLegacyPortCompatible(t *testing.T) {
	robot := &missionRobot{}
	contract, decider := objectiveFixture(t, robot, 1)
	host, task, _ := hostFixture(t, robot, contract, decider)
	legacy := &legacyEventPort{capabilityEvents: host.Tasks}
	if err := recordParentEvent(context.Background(), legacy, task.ID, tasks.TaskEvent{Type: "TEST"}); err != nil {
		t.Fatal(err)
	}
	if legacy.appends != 1 {
		t.Fatalf("legacy appends=%d", legacy.appends)
	}
}
