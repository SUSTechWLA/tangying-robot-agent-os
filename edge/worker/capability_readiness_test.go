package worker

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

type mixedProfileRuntime struct {
	activityRuntime
	snapshot runtime.Snapshot
	invokes  int
}

func (r *mixedProfileRuntime) Info(context.Context) (runtime.Snapshot, error) { return r.snapshot, nil }
func (r *mixedProfileRuntime) Invoke(context.Context, runtime.Command) (runtime.Result, error) {
	r.invokes++
	return runtime.Result{Success: true}, nil
}

func mixedProfileSnapshot() runtime.Snapshot {
	return runtime.Snapshot{RobotID: "robot-1", Adapter: "mujoco", CatalogRevision: "fixture-catalog",
		RobotProfile: &robotcontract.Profile{RobotID: "robot-1", AdapterID: "mujoco", Tools: []string{"navigation.navigate", "manipulation.pick"}},
		Capabilities: []runtime.Capability{
			{Name: "navigation.navigate", Available: true, SafetyLevel: "physical_motion"},
			{Name: "manipulation.pick", Available: true, SafetyLevel: "physical_motion"},
		}}
}

func TestFreshToolReadinessCannotBeBorrowedFromNavigation(t *testing.T) {
	for _, mode := range []string{"unavailable", "available-with-blockers", "missing"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			task, node, cloud := policyTask(t)
			store := contextFixture(t, cloud)
			fresh := mixedProfileSnapshot()
			want := runtime.ErrCapabilityUnavailable
			switch mode {
			case "unavailable":
				fresh.Capabilities[1].Available = false
			case "available-with-blockers":
				fresh.Capabilities[1].Blockers = []string{"GRIPPER_NOT_READY"}
			case "missing":
				fresh.Capabilities = fresh.Capabilities[:1]
				want = runtime.ErrCapabilityUnknown
			}
			if !fresh.PhysicalReady() {
				t.Fatal("navigation should keep shared physical readiness true")
			}
			robot := &mixedProfileRuntime{snapshot: fresh}
			w := New(Config{RobotID: "robot-1", Adapter: "mujoco", Cloud: cloud, Runtime: robot, ExecutionStore: store})
			if _, err := w.acceptContext(ctx, task, node); err != nil {
				t.Fatal(err)
			}
			invoker := capabilityInvoker{w: w, node: node, snapshot: mixedProfileSnapshot(),
				before: func(ctx context.Context) error { return w.beforeOwnedContextStep(ctx, task.ID, node) }}
			_, err := invoker.Invoke(ctx, runtime.Command{TaskID: task.ID, CommandID: "must-not-pick", Capability: runtime.CapabilityPick, Deadline: time.Now().Add(time.Second)})
			if !errors.Is(err, want) || robot.invokes != 0 {
				t.Fatalf("err=%v invokes=%d", err, robot.invokes)
			}
			_, saved, found, err := w.loadContext(ctx, task.ID)
			if err != nil || !found || saved.Phase != contextAccepted {
				t.Fatalf("availability rejection lost checkpoint: %+v found=%v err=%v", saved, found, err)
			}
		})
	}
}

func TestNavigationOnlyReadinessAllowsItsAvailablePhysicalTool(t *testing.T) {
	ctx := context.Background()
	task, node, cloud := policyTask(t)
	store := contextFixture(t, cloud)
	fresh := mixedProfileSnapshot()
	fresh.Capabilities = fresh.Capabilities[:1]
	fresh.RobotProfile.Tools = []string{"navigation.navigate"}
	robot := &mixedProfileRuntime{snapshot: fresh}
	w := New(Config{RobotID: "robot-1", Adapter: "mujoco", Cloud: cloud, Runtime: robot, ExecutionStore: store})
	if _, err := w.acceptContext(ctx, task, node); err != nil {
		t.Fatal(err)
	}
	invoker := capabilityInvoker{w: w, node: node, snapshot: fresh,
		before: func(ctx context.Context) error { return w.beforeOwnedContextStep(ctx, task.ID, node) }}
	_, err := invoker.Invoke(ctx, runtime.Command{TaskID: task.ID, CommandID: "available-navigation", Capability: runtime.CapabilityNavigate, Deadline: time.Now().Add(time.Second)})
	if err != nil || robot.invokes != 1 {
		t.Fatalf("err=%v invokes=%d", err, robot.invokes)
	}
}
