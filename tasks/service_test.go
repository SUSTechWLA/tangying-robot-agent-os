package tasks_test

import (
	"context"
	"strings"
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

// An approval exists to make "who let this run" answerable. A boolean answers
// only "was it let run", which is the question nobody has to ask afterwards.
func TestAnApprovalRecordsWhoGaveItAndWhen(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if task.ApprovedBy != "" || !task.ApprovedAt.IsZero() {
		t.Fatalf("a fresh task already carries an approval: %+v", task)
	}

	approved, err := service.Approve(context.Background(), task.ID, "console-session:abc123")
	if err != nil {
		t.Fatal(err)
	}
	if approved.ApprovedBy != "console-session:abc123" {
		t.Fatalf("approvedBy = %q", approved.ApprovedBy)
	}
	if approved.ApprovedAt.IsZero() {
		t.Fatal("the approval recorded no time, so a later reader cannot order it against anything")
	}
	if approved.ApprovedAt.Before(task.CreatedAt) {
		t.Fatalf("approvedAt = %s predates the task it approves", approved.ApprovedAt)
	}
	// The event is what a replay reads, so the surface has to be in it too.
	var found bool
	for _, event := range approved.Events {
		if event.Type == "TASK_APPROVED" && strings.Contains(event.Message, "console-session:abc123") {
			found = true
		}
	}
	if !found {
		t.Fatalf("the approval event does not name the surface: %+v", approved.Events)
	}

	// An approval that cannot name its surface is the same statement as no
	// approval, and the call is what a physical action proceeds on.
	other, err := service.Create(context.Background(), "把蓝色瓶子放进左侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.Approve(context.Background(), other.ID, "   "); err == nil {
		t.Fatal("an anonymous approval was accepted")
	}
	reloaded, err := service.Get(context.Background(), other.ID)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.Approved {
		t.Fatal("a refused approval still set the flag")
	}
}
