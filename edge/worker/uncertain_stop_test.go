package worker

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

type lostPhysicalResult struct {
	activityRuntime
	physical, stops int
}

type unknownTerminalResult struct {
	lostPhysicalResult
	code string
}

func (r *unknownTerminalResult) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	if !agent.IsReadOnlyCapability(string(command.Capability)) {
		r.physical++
		return runtime.Result{Success: false, Code: r.code}, nil
	}
	return runtime.Result{Success: true, ObservationID: "fresh"}, nil
}

func TestTerminalRuntimeUnknownResultDoesNotBecomeKnownCloudFailure(t *testing.T) {
	for _, code := range []string{"EXECUTION_OUTCOME_UNKNOWN", "EXECUTION_OUTCOME_UNKNOWN; BACKEND_STOP_FAILED: controller offline", "POSTCONDITION_OBSERVATION_TIMEOUT", "RUNTIME_JOURNAL_UNAVAILABLE", "BACKEND_ERROR"} {
		t.Run(code, func(t *testing.T) {
			_, _, cloud := policyTask(t)
			store := contextFixture(t, cloud)
			robot := &unknownTerminalResult{code: code}
			w := New(Config{RobotID: "robot-1", Adapter: "mujoco", Cloud: cloud, Runtime: robot, ExecutionStore: store})
			err := w.processTask(context.Background(), cloud.task.ID)
			if !errors.Is(err, ErrHandoffUncertain) || cloud.failed || robot.physical != 1 {
				t.Fatalf("err=%v failed=%v physical=%d", err, cloud.failed, robot.physical)
			}
		})
	}
}

func (r *lostPhysicalResult) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	if !agent.IsReadOnlyCapability(string(command.Capability)) {
		r.physical++
		return runtime.Result{}, errors.New("execution stream lost after dispatch")
	}
	return runtime.Result{Success: true, ObservationID: "fresh"}, nil
}
func (r *lostPhysicalResult) Cancel(ctx context.Context, _, _ string) (bool, error) {
	r.stops++
	return true, ctx.Err()
}

func TestPhysicalResultLossCannotBecomeCloudFailureOrReplayAfterRestart(t *testing.T) {
	ctx := context.Background()
	_, node, cloud := policyTask(t)
	path := filepath.Join(t.TempDir(), "execution.db")
	store := contextFixtureAt(t, cloud, path)
	robot := &lostPhysicalResult{}
	w := New(Config{RobotID: "robot-1", Adapter: "mujoco", Cloud: cloud, Runtime: robot, ExecutionStore: store})
	err := w.processTask(ctx, cloud.task.ID)
	if !errors.Is(err, ErrHandoffUncertain) || cloud.failed || robot.physical != 1 || robot.stops != 1 {
		t.Fatalf("err=%v failed=%v physical=%d controlled stops=%d", err, cloud.failed, robot.physical, robot.stops)
	}
	found := false
	for _, event := range cloud.events {
		if event.Payload["outcomeUnknown"] == true {
			found = event.Payload["automaticRetryForbidden"] == true
		}
	}
	if !found {
		t.Fatal("unknown physical outcome missing from trace")
	}
	_ = store.Close()
	store = openInboxStore(t, path)
	cloud.node = node // Coordinator redelivers the exact same still-active claim.
	restarted := New(Config{RobotID: "robot-1", Adapter: "mujoco", Cloud: cloud, Runtime: robot, ExecutionStore: store})
	if err := restarted.processTask(ctx, cloud.task.ID); !errors.Is(err, ErrHandoffUncertain) {
		t.Fatalf("replay: %v", err)
	}
	if robot.physical != 1 || cloud.failed {
		t.Fatalf("uncertain action replayed/failed: physical=%d cloud.failed=%v", robot.physical, cloud.failed)
	}
	_, saved, foundCheckpoint, err := restarted.loadContext(ctx, cloud.task.ID)
	if err != nil || !foundCheckpoint || saved.Phase != contextAccepted {
		t.Fatalf("checkpoint=%+v found=%v err=%v", saved, foundCheckpoint, err)
	}
}
