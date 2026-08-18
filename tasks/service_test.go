package tasks_test

import (
	"context"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestSafetyStopCannotBeAutomaticallyResumed(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, _ := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateSafetyStopped, "safety"); err != nil {
		t.Fatal(err)
	}
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateExecuting, "automatic retry"); err == nil {
		t.Fatal("automatic execution after safety stop must be rejected")
	}
}
