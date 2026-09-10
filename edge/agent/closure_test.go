package agent_test

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// A tool that reports success but cannot say what the world looks like
// afterwards must not be able to complete a physical write. This is the
// contract that makes "return code is a trigger, not proof" enforceable for
// adapter tools the Agent has never seen, instead of only for the reference
// verify_* steps.
func TestWorldMutationCannotCompleteWithoutFreshEvidence(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "closure-evidence", Intent: parsed, Approved: true, CurrentRevision: 1}
	robot := &evidenceFreeRobot{recordingRobot: recordingRobot{counts: map[string]int{}}}
	runner := agent.NewRunner(store, robot, robot)

	var confirmed []string
	runner.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
		if event.Payload["activityStatus"] == "CONFIRMED" {
			if skill, ok := event.Payload["toolName"].(string); ok {
				confirmed = append(confirmed, skill)
			}
		}
		return nil
	}

	_, err = runner.Run(context.Background(), task)
	if err == nil {
		t.Fatal("a physical write completed on a return code alone")
	}
	if !errors.Is(err, agent.ErrUnverifiedWorldMutation) {
		t.Fatalf("run error = %v, want ErrUnverifiedWorldMutation", err)
	}
	for _, write := range []string{"manipulation.pick", "manipulation.place"} {
		for _, skill := range confirmed {
			if skill == write {
				t.Fatalf("write %s reported CONFIRMED without evidence", write)
			}
		}
	}

	// The step must stay STARTED, not FAILED and not COMPLETED: the runtime
	// reported success, so the hardware may well have moved. Recovery has to
	// reconcile the real world before anything else touches it.
	runs, err := store.ListStepRuns(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) == 0 {
		t.Fatal("no execution records were written")
	}
	var pick *middleware.StepRun
	for index := range runs {
		if runs[index].Capability == "manipulation.pick" {
			pick = &runs[index]
		}
	}
	if pick == nil {
		t.Fatalf("no record for the physical write: %+v", runs)
	}
	if pick.Status != middleware.StepStarted {
		t.Fatalf("physical step status = %s, want STARTED (unverified outcome)", pick.Status)
	}
	if err := agent.NewRunner(store, nil, nil).CheckRecovery(context.Background(), task.ID); !errors.Is(err, agent.ErrPhysicalOutcomeUnknown) {
		t.Fatalf("CheckRecovery = %v, want ErrPhysicalOutcomeUnknown", err)
	}
}

// The gate must not touch read-only tools: there is no world change to confirm.
func TestReadOnlyToolsStillCompleteWithoutEvidence(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "closure-read-only", Intent: parsed, Approved: true, CurrentRevision: 1}
	robot := &evidenceFreeRobot{recordingRobot: recordingRobot{counts: map[string]int{}}}
	if _, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task); err == nil {
		t.Fatal("the physical write unexpectedly completed")
	}
	// The read-only steps before the first write must have completed normally.
	runs, err := store.ListStepRuns(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	completed := map[string]bool{}
	for _, run := range runs {
		if run.Status == middleware.StepCompleted {
			completed[run.Capability] = true
		}
	}
	for _, readOnly := range []string{"observe_scene", "resolve_targets", "plan_grasp"} {
		if !completed[readOnly] {
			t.Fatalf("read-only tool %s was gated; completed = %v", readOnly, completed)
		}
	}
	if completed["manipulation.pick"] {
		t.Fatal("the unverified write was recorded as completed")
	}
}

// A runtime that does confirm its action works exactly as before: the gate is
// not a blanket refusal of physical tools.
func TestWorldMutationCompletesWithEvidenceFromTheRuntime(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "closure-happy", Intent: parsed, Approved: true, CurrentRevision: 1}
	robot := &recordingRobot{counts: map[string]int{}}
	var confirmed []string
	runner := agent.NewRunner(store, robot, robot)
	runner.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
		if event.Payload["activityStatus"] == "CONFIRMED" {
			if skill, ok := event.Payload["toolName"].(string); ok {
				confirmed = append(confirmed, skill)
			}
		}
		return nil
	}
	if _, err := runner.Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	for _, write := range []string{"manipulation.pick", "manipulation.place"} {
		found := false
		for _, skill := range confirmed {
			if skill == write {
				found = true
			}
		}
		if !found {
			t.Fatalf("verified write %s was not confirmed; confirmed = %v", write, confirmed)
		}
	}
}

// An adapter that declares a write tool the local catalog has never heard of
// still gets the gate, because the runtime's own declaration is consulted.
func TestAdapterDeclaredWriteToolIsAlsoGated(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "closure-adapter", Intent: parsed, Approved: true, CurrentRevision: 1}
	robot := &evidenceFreeRobot{recordingRobot: recordingRobot{counts: map[string]int{}}}
	remote := &remoteDeclarationGrounder{evidenceFreeRobot: robot}
	runner := agent.NewRunner(store, remote, remote)
	if _, err := runner.Run(context.Background(), task); !errors.Is(err, agent.ErrUnverifiedWorldMutation) {
		t.Fatalf("run error = %v, want ErrUnverifiedWorldMutation", err)
	}
}

// remoteDeclarationGrounder reports the runtime capability view, which is how
// the Agent learns about adapter-specific write tools.
type remoteDeclarationGrounder struct {
	*evidenceFreeRobot
}

func (g *remoteDeclarationGrounder) Info(context.Context) (runtime.Snapshot, error) {
	// Only the pick tool is declared as mutating the world, and only the tools
	// this task needs are advertised at all.
	return runtime.Snapshot{
		RobotID: "test-robot",
		Ready:   true,
		Capabilities: []runtime.Capability{
			{Name: "observe_scene", Available: true},
			{Name: "resolve_targets", Available: true},
			{Name: "plan_grasp", Available: true},
			{Name: "verify_grasp", Available: true},
			{Name: "verify_placement", Available: true},
			{Name: "manipulation.pick", Available: true, MutatesWorld: true, SafetyLevel: "physical_motion"},
			{Name: "manipulation.place", Available: true, SafetyLevel: "physical_motion"},
		},
	}, nil
}
