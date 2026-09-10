package localapp

import (
	"context"
	"errors"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type testRobot struct {
	mu           sync.Mutex
	executeCalls int
}

type blockingRevisionRobot struct {
	testRobot
	started chan struct{}
	release chan struct{}
	once    sync.Once
}

func (r *blockingRevisionRobot) Invoke(ctx context.Context, command runtime.Command) (runtime.Result, error) {
	r.once.Do(func() {
		close(r.started)
		select {
		case <-ctx.Done():
		case <-r.release:
		}
	})
	return r.testRobot.Invoke(ctx, command)
}

func (r *testRobot) Ground(_ context.Context, parsed manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{
		Action:      parsed.Action,
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 0.99},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 0.99},
		KeepUpright: true,
	}, nil
}

func (r *testRobot) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	r.mu.Lock()
	r.executeCalls++
	r.mu.Unlock()
	return evidenceResult(command.StepID), nil
}

func (r *testRobot) calls() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.executeCalls
}

func newTestRuntime(t *testing.T) (*tasks.Service, *agent.Runner, *testRobot) {
	t.Helper()
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	robot := &testRobot{}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	return service, agent.NewRunner(store, robot, robot), robot
}

func TestApprovedTaskRunsWithoutClaimOrLease(t *testing.T) {
	service, runner, robot := newTestRuntime(t)
	app := New(service, runner, memory.NewQueue[string](64))
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

	waitForState(t, service, task.ID, taskgraph.StateSucceeded)
	if robot.calls() == 0 {
		t.Fatal("robot was not executed")
	}
}

func TestRunningLocalTaskActivatesConfirmedRevisionAtSafePoint(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	robot := &blockingRevisionRobot{started: make(chan struct{}), release: make(chan struct{})}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	app := New(service, agent.NewRunner(store, robot, robot), memory.NewQueue[string](64))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	app.Start(ctx)
	task, _ := service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
	_, _ = service.Approve(ctx, task.ID)
	if err := app.Enqueue(task.ID); err != nil {
		t.Fatal(err)
	}
	select {
	case <-robot.started:
	case <-time.After(2 * time.Second):
		t.Fatal("local robot did not start")
	}
	basis, err := app.RevisionBasis(ctx, task.ID)
	if err != nil || len(basis.RunningStepIDs) == 0 {
		t.Fatalf("basis=%#v err=%v", basis, err)
	}
	proposal, err := service.ProposeRevision(ctx, tasks.ProposeRevisionCommand{
		TaskID: task.ID, ExpectedRevision: 1, Request: "最后放到左侧目标区",
		IdempotencyKey: "2e8cc3dd-43f9-4e59-a930-070c73bca444", Creator: "local-owner",
	}, basis)
	if err != nil {
		t.Fatal(err)
	}
	confirmed, err := app.ConfirmRevision(ctx, task.ID, proposal.Revision.Revision, 1,
		"2e8cc3dd-43f9-4e59-a930-070c73bca445")
	if err != nil || confirmed.Status != tasks.RevisionWaitingSafePoint {
		t.Fatalf("confirmed=%#v err=%v", confirmed, err)
	}
	close(robot.release)
	completed := waitForState(t, service, task.ID, taskgraph.StateSucceeded)
	if completed.CurrentRevision != 2 || robot.calls() < 14 {
		t.Fatalf("task=%#v calls=%d", completed, robot.calls())
	}
}

func TestEnqueueReturnsWhenLocalQueueIsFull(t *testing.T) {
	service, runner, _ := newTestRuntime(t)
	app := New(service, runner, memory.NewQueue[string](1))
	for index := 0; index < 1; index++ {
		task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := service.Approve(context.Background(), task.ID); err != nil {
			t.Fatal(err)
		}
		if err := app.Enqueue(task.ID); err != nil {
			t.Fatalf("enqueue %d: %v", index, err)
		}
	}
	task, err := service.Create(context.Background(), "把蓝色杯子放进左侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.Approve(context.Background(), task.ID); err != nil {
		t.Fatal(err)
	}
	if err := app.Enqueue(task.ID); !errors.Is(err, ErrQueueFull) {
		t.Fatalf("full queue error = %v", err)
	}
}

func TestUnapprovedPhysicalTaskDoesNotRun(t *testing.T) {
	service, runner, robot := newTestRuntime(t)
	app := New(service, runner, memory.NewQueue[string](64))
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
	app := New(service, runner, memory.NewQueue[string](64))
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
	app := New(service, runner, memory.NewQueue[string](64))
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

type shutdownRobot struct {
	testRobot
	started chan struct{}
}

func (r *shutdownRobot) Invoke(ctx context.Context, _ runtime.Command) (runtime.Result, error) {
	close(r.started)
	<-ctx.Done()
	return runtime.Result{}, ctx.Err()
}

func TestProcessShutdownRetainsRecoverableTaskInsteadOfUserCancellation(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	robot := &shutdownRobot{started: make(chan struct{})}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	app := New(service, agent.NewRunner(store, robot, robot), memory.NewQueue[string](64))
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
	select {
	case <-robot.started:
	case <-time.After(3 * time.Second):
		t.Fatal("robot did not start")
	}
	cancel()
	waitForState(t, service, task.ID, taskgraph.StateRecoverableFailure)
}

func TestEnqueueDoesNotPretendRecoverableTaskWillRun(t *testing.T) {
	service, runner, _ := newTestRuntime(t)
	app := New(service, runner, memory.NewQueue[string](64))
	task, _ := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	_, _ = service.Approve(context.Background(), task.ID)
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateObserving, "start"); err != nil {
		t.Fatal(err)
	}
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateRecoverableFailure, "interrupted"); err != nil {
		t.Fatal(err)
	}
	if err := app.Enqueue(task.ID); err == nil {
		t.Fatal("approval reported successful enqueue for an unexecutable state")
	}
}

func waitForState(t *testing.T, service *tasks.Service, taskID string, expected taskgraph.TaskState) *tasks.Task {
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

// evidenceResult is what a runtime that confirms its own action returns: a
// success code plus the post-command observation proving the world changed.
// The closed-loop gate refuses a physical write on a return code alone, so
// fixtures model a real runtime instead of a bare success.
func evidenceResult(stepID string) runtime.Result {
	observedAt := time.Now().UTC()
	return runtime.Result{
		Success:                true,
		VerificationConfidence: 1,
		ObservationID:          "observation/" + stepID,
		Evidence: &telemetry.Snapshot{
			ObservedAt: observedAt,
			Reconstruction: &robotcontract.Reconstruction{
				ObservationID:    "observation/" + stepID,
				SourceID:         "test-robot/scene",
				ObservedAtUnixMS: observedAt.UnixMilli(),
			},
		},
	}
}
