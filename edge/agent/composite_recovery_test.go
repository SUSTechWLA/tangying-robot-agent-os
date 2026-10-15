package agent_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestAuthorizedCompositeChildChecksPhysicalJournalWithoutReenteringParentRecovery(t *testing.T) {
	for _, mode := range []string{"fresh", "started-physical", "started-read", "unapproved", "authority-revoked"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			store, err := sqlite.Open(filepath.Join(t.TempDir(), "edge.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			parent := executeTask(t, "delegated-parent")
			parent.Plan = &orchestration.Bundle{Capabilities: &capability.Plan{RobotID: "edge-test", Calls: []capability.Call{{Tool: "edge.goal"}}}}
			robot := &recordingRobot{counts: map[string]int{}}
			runner := agent.NewRunner(store, robot, robot)
			runner.Tasks = taskReaderStub{tasks: map[string]*tasks.Task{parent.ID: parent}}
			recoveryCalls := 0
			runner.CapabilityRecovery = func(_ context.Context, got *tasks.Task) error {
				recoveryCalls++
				if got != parent {
					t.Fatal("public recovery lost the persisted parent")
				}
				return fmt.Errorf("%w: delegated parent has a live child reservation", agent.ErrPhysicalOutcomeUnknown)
			}
			if err := runner.CheckRecovery(ctx, parent.ID); !errors.Is(err, agent.ErrPhysicalOutcomeUnknown) || recoveryCalls != 1 {
				t.Fatalf("external recovery admitted the pending parent: %v calls=%d", err, recoveryCalls)
			}
			if mode == "started-physical" || mode == "started-read" {
				tool := "manipulation.pick"
				if mode == "started-read" {
					tool = "observe_scene"
				}
				if err := store.MarkStepStarted(ctx, middleware.StepRecord{TaskID: parent.ID, StepID: "prior-revision-step", IdempotencyKey: "prior-revision-step", Capability: tool, SafetyLevel: "read_only"}); err != nil {
					t.Fatal(err)
				}
			}
			// This is the trusted composition boundary: the approved host passes
			// an already parsed legacy child while the service keeps the parent.
			child := *parent
			child.Plan = nil
			if mode == "unapproved" {
				child.Approved = false
			}
			authorityChecks := 0
			result, runErr := runner.RunControlled(ctx, &child, agent.RunControl{StepPrefix: "goal-child-", BeforeStep: func(context.Context) error {
				authorityChecks++
				if mode == "authority-revoked" {
					return context.Canceled
				}
				return nil
			}})
			if recoveryCalls != 1 {
				t.Fatalf("trusted child recursively reentered pending parent recovery: error=%v calls=%d", runErr, recoveryCalls)
			}
			switch mode {
			case "fresh", "started-read":
				if runErr != nil || robot.count("manipulation.pick") != 1 || robot.count("manipulation.place") != 1 || authorityChecks == 0 {
					t.Fatalf("authorized child failed: error=%v picks=%d places=%d checks=%d", runErr, robot.count("manipulation.pick"), robot.count("manipulation.place"), authorityChecks)
				}
			case "started-physical":
				if !errors.Is(runErr, agent.ErrPhysicalOutcomeUnknown) || len(robot.taskIDs) != 0 {
					t.Fatalf("started physical journal bypassed: error=%v calls=%v", runErr, robot.taskIDs)
				}
			case "unapproved":
				if !errors.Is(runErr, agent.ErrApprovalRequired) || robot.count("manipulation.pick") != 0 {
					t.Fatalf("approval bypassed: error=%v picks=%d", runErr, robot.count("manipulation.pick"))
				}
			case "authority-revoked":
				if !errors.Is(runErr, context.Canceled) || len(robot.taskIDs) != 0 {
					t.Fatalf("revoked authority bypassed: error=%v calls=%v", runErr, robot.taskIDs)
				}
			}
			if err := runner.CheckRecovery(ctx, parent.ID); !errors.Is(err, agent.ErrPhysicalOutcomeUnknown) || recoveryCalls != 2 {
				t.Fatalf("child execution weakened public parent recovery: %v calls=%d", err, recoveryCalls)
			}
			history, _ := store.ListStepRuns(ctx, parent.ID)
			trace, _ := json.Marshal(map[string]any{"mode": mode, "taskId": parent.ID, "completed": result.CompletedSteps, "picks": robot.count("manipulation.pick"), "places": robot.count("manipulation.place"), "authorityChecks": authorityChecks, "externalRecoveryCalls": recoveryCalls, "history": history})
			t.Logf("trace=%s", trace)
		})
	}
}
