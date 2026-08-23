package taskgraph_test

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

func TestTaskStateRejectsAutomaticRecoveryFromSafetyStop(t *testing.T) {
	if taskgraph.CanTransition(taskgraph.StateSafetyStopped, taskgraph.StateExecuting) {
		t.Fatal("SAFETY_STOPPED must require manual clearance")
	}
}

func TestTaskStateAllowsManualClearanceToReady(t *testing.T) {
	if !taskgraph.CanTransition(taskgraph.StateSafetyStopped, taskgraph.StateReady) {
		t.Fatal("SAFETY_STOPPED should allow explicit clearance to READY")
	}
}

func TestObservationRecoveryPath(t *testing.T) {
	path := []taskgraph.TaskState{
		taskgraph.StateExecuting,
		taskgraph.StateWaitingForObservation,
		taskgraph.StateRecovering,
		taskgraph.StateExecuting,
	}
	for index := 0; index < len(path)-1; index++ {
		if !taskgraph.CanTransition(path[index], path[index+1]) {
			t.Fatalf("transition %s -> %s rejected", path[index], path[index+1])
		}
	}
	if !taskgraph.CanTransition(taskgraph.StateWaitingForObservation, taskgraph.StateBlocked) ||
		!taskgraph.CanTransition(taskgraph.StateRecovering, taskgraph.StateFailedSafe) {
		t.Fatal("safe terminal recovery transitions are missing")
	}
}
