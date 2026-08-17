package orchestrator_test

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

func TestClaimIssuesOneActiveLease(t *testing.T) {
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	first, err := service.Claim(context.Background(), "agent-1", time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	second, err := service.Claim(context.Background(), "agent-2", time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if first.Task.ID != task.ID || second.Task != nil {
		t.Fatalf("claims = %#v %#v", first, second)
	}
}

func TestSafetyStopCannotBeAutomaticallyResumed(t *testing.T) {
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
	task, _ := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateSafetyStopped, "safety"); err != nil {
		t.Fatal(err)
	}
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateExecuting, "automatic retry"); err == nil {
		t.Fatal("automatic execution after safety stop must be rejected")
	}
}
