package localapp

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestRecoveryReconciliationDoesNotInventPermissionOrCompletion(t *testing.T) {
	for _, scenario := range []struct {
		outcome middleware.StepOutcome
		reason  string
	}{
		{middleware.StepOutcomeAbandoned, "STEP_ABANDONED"},
		{middleware.StepOutcomeHappened, "RECOVERY_NOT_AUTHORIZED"},
		{middleware.StepOutcomeNeverActed, "RECOVERY_NOT_AUTHORIZED"},
	} {
		t.Run(string(scenario.outcome), func(t *testing.T) {
			ctx := context.Background()
			path := filepath.Join(t.TempDir(), "tasks.db")
			store, err := sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = store.Close() })
			service := tasks.NewService(store, intent.NewDeterministicParser())
			robot := &testRobot{}
			app := New(service, agent.NewRunner(store, robot, robot), memory.NewQueue[string](1))
			task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
			if err != nil {
				t.Fatal(err)
			}
			if _, err := service.Approve(ctx, task.ID, "fixture-operator"); err != nil {
				t.Fatal(err)
			}
			for _, state := range []taskgraph.TaskState{taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting, taskgraph.StateRecoverableFailure} {
				if err := service.Transition(ctx, task.ID, state, "fixture interrupted physical step"); err != nil {
					t.Fatal(err)
				}
			}
			record := middleware.StepRecord{TaskID: task.ID, StepID: "pre_position", Capability: "navigation.pre_position", IdempotencyKey: task.ID + "/pre_position", SafetyLevel: "physical_motion"}
			if err := store.MarkStepStarted(ctx, record); err != nil {
				t.Fatal(err)
			}
			before, err := app.Recovery(ctx, task.ID)
			if err != nil || before.CanResume || !before.RequiresReconciliation {
				t.Fatalf("unknown motion did not block recovery: %+v %v", before, err)
			}
			if err := app.ReconcileStep(ctx, record, middleware.StepReconciliation{
				Outcome: scenario.outcome, Actor: "fixture-operator", Note: "explicit fixture conclusion; no motion or success inferred", RecordedAt: time.Now().UTC(),
			}); err != nil {
				t.Fatal(err)
			}

			for _, restarted := range []bool{false, true} {
				if restarted {
					if err := store.Close(); err != nil {
						t.Fatal(err)
					}
					store, err = sqlite.Open(path)
					if err != nil {
						t.Fatal(err)
					}
					service = tasks.NewService(store, intent.NewDeterministicParser())
					app = New(service, agent.NewRunner(store, robot, robot), memory.NewQueue[string](1))
				}
				view, err := app.Recovery(ctx, task.ID)
				if err != nil || view.CanResume || view.RequiresReconciliation || view.ReasonCode != scenario.reason || len(view.CompletedStepIDs) != 0 {
					t.Fatalf("restart=%v reconciliation misrepresented as replay permission/completion: %+v %v", restarted, view, err)
				}
				if err := app.Resume(task.ID); err == nil {
					t.Fatal("executor accepted recovery without completed or retry-safe execution proof")
				}
				runs, err := app.runner.ExecutionHistory(ctx, task.ID)
				if err != nil || len(runs) != 1 || runs[0].Status != middleware.StepStarted || runs[0].Reconciled == nil || runs[0].Reconciled.Outcome != scenario.outcome {
					t.Fatalf("original execution evidence rewritten: %+v %v", runs, err)
				}
				if app.ExecutionActive(task.ID) || robot.calls() != 0 {
					t.Fatal("reconciliation dispatched physical work")
				}
			}
			if err := app.Cancel(task.ID); err != nil {
				t.Fatal(err)
			}
			current, err := service.Get(ctx, task.ID)
			if err != nil || current.State != taskgraph.StateCancelled {
				t.Fatalf("explicit cancellation after reconciliation failed: %+v %v", current, err)
			}
		})
	}
}
