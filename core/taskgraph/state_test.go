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
