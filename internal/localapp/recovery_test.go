package localapp

import (
	"context"
	"errors"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type pauseRobot struct {
	testRobot
	block             string
	started, release  chan struct{}
	once              sync.Once
	commands          []runtime.Command
	lostGrasp         bool
	replacementTarget string
}

func (r *pauseRobot) Ground(ctx context.Context, parsed manipulation.Intent) (manipulation.GroundedTask, error) {
	grounded, err := r.testRobot.Ground(ctx, parsed)
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.replacementTarget != "" {
		grounded.Object.ID = r.replacementTarget
	}
	return grounded, err
}

func TestResumeRefusesToAttachCompletedPickToDifferentGroundedObject(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	robot := &pauseRobot{block: "manipulation.pick", started: make(chan struct{}), release: make(chan struct{})}
	app, service, taskID, _ := startPauseTask(t, store, robot)
	robot.mu.Lock()
	robot.replacementTarget = "another-red-cup"
	robot.mu.Unlock()
	if err := app.Resume(taskID); err != nil {
		t.Fatal(err)
	}
	waitForState(t, service, taskID, taskgraph.StateRecoverableFailure)
	if robot.count("manipulation.pick") != 1 || robot.count("manipulation.place") != 0 {
		t.Fatal("completed action rebound to another object")
	}
}

func (r *pauseRobot) Invoke(ctx context.Context, command runtime.Command) (runtime.Result, error) {
	r.mu.Lock()
	r.commands = append(r.commands, command)
	lost := r.lostGrasp
	r.mu.Unlock()
	if string(command.Capability) == r.block {
		r.once.Do(func() {
			close(r.started)
			select {
			case <-r.release:
			case <-ctx.Done():
			}
		})
	}
	if err := ctx.Err(); err != nil {
		return runtime.Result{}, err
	}
	confidence := 1.0
	if lost && command.Capability == "verify_grasp" {
		confidence = 0.1
	}
	result := evidenceResult(command.StepID)
	result.VerificationConfidence = confidence
	return result, nil
}

func (r *pauseRobot) count(capability string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	count := 0
	for _, command := range r.commands {
		if string(command.Capability) == capability {
			count++
		}
	}
	return count
}

func startPauseTask(t *testing.T, store *sqlite.Store, robot *pauseRobot) (*App, *tasks.Service, string, func()) {
	t.Helper()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	app := New(service, agent.NewRunner(store, robot, robot), memory.NewQueue[string](64))
	ctx, cancel := context.WithCancel(context.Background())
	app.Start(ctx)
	stop := func() {
		cancel()
		waitCtx, stopWait := context.WithTimeout(context.Background(), 3*time.Second)
		defer stopWait()
		if err := app.Wait(waitCtx); err != nil {
			t.Error(err)
		}
	}
	t.Cleanup(stop)
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
		t.Fatal("tool did not start")
	}
	if err := app.Pause(task.ID); err != nil {
		t.Fatal(err)
	}
	view, err := app.Recovery(ctx, task.ID)
	if err != nil || !view.PauseRequested || view.State != taskgraph.StateExecuting || view.CanResume {
		t.Fatalf("pause prematurely reported completion: %+v %v", view, err)
	}
	close(robot.release)
	waitForState(t, service, task.ID, taskgraph.StatePaused)
	return app, service, task.ID, stop
}

func TestPauseReopenAndResumeDoesNotReplayCompletedPhysicalSteps(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	robot := &pauseRobot{block: "manipulation.pick", started: make(chan struct{}), release: make(chan struct{})}
	_, _, taskID, stop := startPauseTask(t, store, robot)
	stop()
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	service := tasks.NewService(reopened, intent.NewDeterministicParser())
	app := New(service, agent.NewRunner(reopened, robot, robot), memory.NewQueue[string](64))
	ctx, cancel := context.WithCancel(context.Background())
	app.Start(ctx)
	defer func() { cancel(); _ = app.Wait(context.Background()) }()
	view, err := app.Recovery(ctx, taskID)
	if err != nil || !view.CanResume || view.State != taskgraph.StatePaused {
		t.Fatalf("view=%+v err=%v", view, err)
	}
	if robot.count("manipulation.place") != 0 {
		t.Fatal("restart automatically replayed execution")
	}
	if err := app.Resume(taskID); err != nil {
		t.Fatal(err)
	}
	waitForState(t, service, taskID, taskgraph.StateSucceeded)
	if robot.count("manipulation.pick") != 1 || robot.count("manipulation.place") != 1 {
		t.Fatalf("pick=%d place=%d", robot.count("manipulation.pick"), robot.count("manipulation.place"))
	}
	if robot.count("observe_scene") != 2 {
		t.Fatal("resume did not refresh observation")
	}
}

func TestResumeRechecksGraspInsteadOfTrustingPrePauseVerification(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	robot := &pauseRobot{block: "verify_grasp", started: make(chan struct{}), release: make(chan struct{})}
	app, service, taskID, _ := startPauseTask(t, store, robot)
	robot.mu.Lock()
	robot.lostGrasp = true
	robot.mu.Unlock()
	if err := app.Resume(taskID); err != nil {
		t.Fatal(err)
	}
	waitForState(t, service, taskID, taskgraph.StateRecoverableFailure)
	if robot.count("manipulation.pick") != 1 || robot.count("manipulation.place") != 0 || robot.count("verify_grasp") != 2 {
		t.Fatal("resume reused obsolete physical verification")
	}
	robot.mu.Lock()
	defer robot.mu.Unlock()
	ids := map[string]bool{}
	for _, command := range robot.commands {
		if command.Capability == "verify_grasp" {
			if ids[command.IdempotencyKey] {
				t.Fatal("resume could reuse cached verification receipt")
			}
			ids[command.IdempotencyKey] = true
		}
	}
}

func TestUnknownPhysicalReceiptBlocksResumeAndRevisionAfterReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task, _ := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	_, _ = service.Approve(context.Background(), task.ID)
	for _, state := range []taskgraph.TaskState{taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting} {
		if err := service.Transition(context.Background(), task.ID, state, "old process"); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.MarkStepStarted(context.Background(), middleware.StepRecord{TaskID: task.ID, StepID: "pick", IdempotencyKey: "uncertain-pick", Capability: "manipulation.pick", SafetyLevel: string(skills.SafetyPhysical)}); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	service = tasks.NewService(store, intent.NewDeterministicParser())
	robot := &testRobot{}
	app := New(service, agent.NewRunner(store, robot, robot), memory.NewQueue[string](64))
	ctx, cancel := context.WithCancel(context.Background())
	app.Start(ctx)
	defer func() { cancel(); _ = app.Wait(context.Background()) }()
	view, err := app.Recovery(ctx, task.ID)
	if err != nil || view.CanResume || !view.RequiresReconciliation || len(view.UncertainStepIDs) != 1 {
		t.Fatalf("view=%+v err=%v", view, err)
	}
	if err := app.Resume(task.ID); !errors.Is(err, agent.ErrPhysicalOutcomeUnknown) {
		t.Fatalf("resume=%v", err)
	}
	_, err = app.ProposeRevision(ctx, tasks.ProposeRevisionCommand{TaskID: task.ID, Request: "最后放到左侧目标区", ExpectedRevision: 1, IdempotencyKey: "revision-bypass"})
	if !errors.Is(err, agent.ErrPhysicalOutcomeUnknown) {
		t.Fatalf("revision=%v", err)
	}
	if robot.calls() != 0 {
		t.Fatal("uncertain action reached runtime")
	}
}

func TestLegacyKnownReadOnlyFailureCanResumeWithoutClearingPhysicalGuards(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.Approve(context.Background(), task.ID); err != nil {
		t.Fatal(err)
	}
	for _, state := range []taskgraph.TaskState{taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting, taskgraph.StateRecoverableFailure} {
		if err := service.Transition(context.Background(), task.ID, state, "read-only observe rejected by runtime profile"); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.MarkStepStarted(context.Background(), middleware.StepRecord{TaskID: task.ID, StepID: "observe", IdempotencyKey: "old-observe", Capability: "observe_scene"}); err != nil {
		t.Fatal(err)
	}
	robot := &testRobot{}
	app := New(service, agent.NewRunner(store, robot, robot), memory.NewQueue[string](64))
	ctx, cancel := context.WithCancel(context.Background())
	app.Start(ctx)
	defer func() { cancel(); _ = app.Wait(context.Background()) }()
	view, err := app.Recovery(ctx, task.ID)
	if err != nil || !view.CanResume || view.RequiresReconciliation || len(view.UncertainStepIDs) != 0 {
		t.Fatalf("known read-only failure misclassified: %+v %v", view, err)
	}
	before, err := service.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := app.Resume(task.ID); err != nil {
		t.Fatal(err)
	}
	// Resume is queued asynchronously; the pre-existing recoverable state is
	// not a new execution failure until a new lifecycle event is written.
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		current, err := service.Get(ctx, task.ID)
		if err != nil {
			t.Fatal(err)
		}
		if current.State != taskgraph.StateRecoverableFailure || len(current.Events) > len(before.Events) {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	waitForState(t, service, task.ID, taskgraph.StateSucceeded)
	runs, err := store.ListStepRuns(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) != 7 || runs[0].SafetyLevel != string(skills.SafetyReadOnly) || runs[0].Status != middleware.StepCompleted {
		t.Fatalf("read-only receipt not normalized: %+v", runs)
	}
}
