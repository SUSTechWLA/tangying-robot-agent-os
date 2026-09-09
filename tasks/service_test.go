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

func TestHomeRouteTaskPersistsOrderedRoomsAndRecoveryPostcondition(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "巡检卧室和卫生间，最后回到客厅", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if task.Intent.Action != "home_route" || len(task.Intent.RouteRooms) != 3 || task.Intent.RouteRooms[2] != "living_room" {
		t.Fatalf("intent = %+v", task.Intent)
	}
	revisions, err := service.ListRevisions(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(revisions) != 1 || len(revisions[0].Revision.Steps) != 1 || revisions[0].Revision.Steps[0].RequiredPostcondition == "" {
		t.Fatalf("revision = %+v", revisions)
	}
}
