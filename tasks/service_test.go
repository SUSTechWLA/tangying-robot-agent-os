package tasks_test

import (
	"context"
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestCreateCommandPrependsSimulationEpisodePreparation(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.CreateCommand(context.Background(), tasks.CreateCommand{
		Request:          "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块放到右侧目标区",
		Adapter:          "robocasa",
		ExecutionContext: tasks.ExecutionContext{Mode: "simulation_demo", NewEpisode: true},
	})
	if err != nil {
		t.Fatal(err)
	}
	intents := task.Intent.Tasks()
	if len(intents) != 3 || intents[0].Action != manipulation.ActionPrepareSimulation || intents[0].RobotID != "robot-1" {
		t.Fatalf("intents=%#v", intents)
	}
	if task.ExecutionContext.Mode != "simulation_demo" || !task.ExecutionContext.NewEpisode {
		t.Fatalf("execution context=%#v", task.ExecutionContext)
	}
}

func TestCreateCommandRejectsSimulationResetForPhysicalAdapter(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	_, err := service.CreateCommand(context.Background(), tasks.CreateCommand{
		Request:          "让1号机器人把红色方块放到交接区",
		Adapter:          "xlerobot_direct",
		ExecutionContext: tasks.ExecutionContext{Mode: "simulation_demo", NewEpisode: true},
	})
	if !errors.Is(err, tasks.ErrExecutionContextUnsupported) {
		t.Fatalf("err=%v", err)
	}
}

func TestLegacyCreateDoesNotInjectSimulationPreparation(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "让1号机器人把红色方块放到交接区", "robocasa")
	if err != nil {
		t.Fatal(err)
	}
	intents := task.Intent.Tasks()
	if len(intents) != 1 || intents[0].Action == manipulation.ActionPrepareSimulation {
		t.Fatalf("legacy intents=%#v", intents)
	}
}

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

func TestNormalizeAdapterAcceptsSimulationAndXLeRobotAliases(t *testing.T) {
	cases := map[string]string{
		"": "mujoco", "auto": "mujoco", "sim": "mujoco", "simulation": "mujoco",
		"direct": "xlerobot_direct", "xlerobot": "xlerobot_direct", "XLeRobot_Direct": "xlerobot_direct",
		"ros2": "xlerobot_ros2",
	}
	for input, want := range cases {
		if got := tasks.NormalizeAdapter(input); got != want {
			t.Fatalf("NormalizeAdapter(%q) = %q, want %q", input, got, want)
		}
	}
}
