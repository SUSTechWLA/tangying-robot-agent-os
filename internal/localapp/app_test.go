package localapp

import (
	"context"
	"errors"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/localstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

type testRobot struct {
	mu           sync.Mutex
	executeCalls int
}

func (r *testRobot) Ground(_ context.Context, parsed manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{
		Action:      parsed.Action,
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 0.99},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 0.99},
		KeepUpright: true,
	}, nil
}

func (r *testRobot) Execute(_ context.Context, _ string, _ taskgraph.SkillStep) (runtime.SkillResult, error) {
	r.mu.Lock()
	r.executeCalls++
	r.mu.Unlock()
	return runtime.SkillResult{Success: true, VerificationConfidence: 1}, nil
}

func (r *testRobot) calls() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.executeCalls
}

func newTestRuntime(t *testing.T) (*orchestrator.Service, *agent.Runner, *testRobot) {
	t.Helper()
	store, err := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	robot := &testRobot{}
	service := orchestrator.NewService(store, intent.NewDeterministicParser())
	return service, agent.NewRunner(store, robot), robot
}

func TestApprovedTaskRunsWithoutClaimOrLease(t *testing.T) {
	service, runner, robot := newTestRuntime(t)
	app := New(service, runner)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	app.Start(ctx)

	task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.Approve(ctx, task.ID); err != nil {
		t.Fatal(err)
	}
	if err := app.Enqueue(task.ID); err != nil {
		t.Fatal(err)
	}

	completed := waitForState(t, service, task.ID, taskgraph.StateSucceeded)
	if completed.LeaseID != "" || completed.LeasedTo != "" {
		t.Fatalf("local task retained distributed lease: %#v", completed)
	}
	if robot.calls() == 0 {
		t.Fatal("robot was not executed")
	}
}

func TestUnapprovedPhysicalTaskDoesNotRun(t *testing.T) {
	service, runner, robot := newTestRuntime(t)
	app := New(service, runner)
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if err := app.Enqueue(task.ID); !errors.Is(err, ErrApprovalRequired) {
		t.Fatalf("enqueue error = %v", err)
	}
	if robot.calls() != 0 {
		t.Fatal("unapproved task reached robot")
	}
}

func TestStartMarksInterruptedTaskRecoverableWithoutReplaying(t *testing.T) {
	service, runner, robot := newTestRuntime(t)
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateObserving, "previous process started"); err != nil {
		t.Fatal(err)
	}
	app := New(service, runner)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	app.Start(ctx)

	recovered := waitForState(t, service, task.ID, taskgraph.StateRecoverableFailure)
	if robot.calls() != 0 {
		t.Fatalf("restart replayed %d robot calls", robot.calls())
	}
	if got := recovered.Events[len(recovered.Events)-1].Message; got != "Local Agent restarted during execution" {
		t.Fatalf("recovery event = %q", got)
	}
}

func TestCancelReadyTaskPersistsTerminalState(t *testing.T) {
	service, runner, _ := newTestRuntime(t)
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	app := New(service, runner)
	if err := app.Cancel(task.ID); err != nil {
		t.Fatal(err)
	}
	cancelled, err := service.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if cancelled.State != taskgraph.StateCancelled {
		t.Fatalf("state = %s", cancelled.State)
	}
}

func waitForState(t *testing.T, service *orchestrator.Service, taskID string, expected taskgraph.TaskState) *orchestrator.Task {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		task, err := service.Get(context.Background(), taskID)
		if err != nil {
			t.Fatal(err)
		}
		if task.State == expected {
			return task
		}
		if task.State == taskgraph.StateRecoverableFailure || task.State == taskgraph.StateFailed {
			t.Fatalf("task reached %s: %#v", task.State, task.Events)
		}
		time.Sleep(10 * time.Millisecond)
	}
	task, _ := service.Get(context.Background(), taskID)
	t.Fatalf("task did not reach %s: %#v", expected, task)
	return nil
}
