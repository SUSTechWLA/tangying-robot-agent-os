package agent_test

import (
	"context"
	"errors"
	"path/filepath"
	"sync"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/localstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type recordingRobot struct {
	mu      sync.Mutex
	counts  map[string]int
	taskIDs []string
}

func (r *recordingRobot) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 0.98},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 0.99},
	}, nil
}

func (r *recordingRobot) Execute(_ context.Context, taskID string, step taskgraph.SkillStep) (agent.SkillResult, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.counts[step.Skill]++
	r.taskIDs = append(r.taskIDs, taskID)
	return agent.SkillResult{Success: true, VerificationConfidence: 0.98}, nil
}

func (r *recordingRobot) count(skill string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.counts[skill]
}

func TestRunnerRestartDoesNotRepeatCompletedPick(t *testing.T) {
	store, err := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-1", Intent: parsed, Approved: true}
	robot := &recordingRobot{counts: map[string]int{}}

	if _, err := agent.NewRunner(store, robot).Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if _, err := agent.NewRunner(store, robot).Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if got := robot.count("manipulation.pick"); got != 1 {
		t.Fatalf("pick count = %d, want 1", got)
	}
	for _, taskID := range robot.taskIDs {
		if taskID != task.ID {
			t.Fatalf("executed step with task ID %q, want %q", taskID, task.ID)
		}
	}
}

func TestRunnerRequiresApprovalBeforePhysicalSkill(t *testing.T) {
	store, _ := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-2", Intent: parsed, Approved: false}
	robot := &recordingRobot{counts: map[string]int{}}
	_, err := agent.NewRunner(store, robot).Run(context.Background(), task)
	if err == nil || robot.count("manipulation.pick") != 0 {
		t.Fatalf("err = %v, pick count = %d", err, robot.count("manipulation.pick"))
	}
}

func TestRunnerExecutesCompoundIntentInOrderAndResumesWholeSequence(t *testing.T) {
	store, err := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来")
	task := &tasks.Task{ID: "task-sequence", Intent: parsed, Approved: true}
	robot := &recordingRobot{counts: map[string]int{}}

	first, err := agent.NewRunner(store, robot).Run(context.Background(), task)
	if err != nil {
		t.Fatal(err)
	}
	if got := robot.count("manipulation.pick"); got != 2 {
		t.Fatalf("pick count = %d, want 2", got)
	}
	if len(first.CompletedSteps) != 14 {
		t.Fatalf("completed steps = %d, want 14", len(first.CompletedSteps))
	}

	second, err := agent.NewRunner(store, robot).Run(context.Background(), task)
	if err != nil {
		t.Fatal(err)
	}
	if got := robot.count("manipulation.pick"); got != 2 {
		t.Fatalf("pick count after rerun = %d, want 2", got)
	}
	if len(second.CompletedSteps) != 14 {
		t.Fatalf("resumed completed steps = %d, want 14", len(second.CompletedSteps))
	}
}

type snapshotRobot struct {
	recordingRobot
	snapshot runtime.Snapshot
	err      error
}

func (r *snapshotRobot) Snapshot(context.Context) (runtime.Snapshot, error) {
	return r.snapshot, r.err
}

func validSnapshot() runtime.Snapshot {
	names := []string{
		"observe_scene", "resolve_targets", "plan_grasp", "manipulation.pick",
		"verify_grasp", "manipulation.place", "verify_placement", "recover_to_safe_pose",
	}
	capabilities := make([]runtime.Capability, 0, len(names))
	for _, name := range names {
		capabilities = append(capabilities, runtime.Capability{Name: name, Available: true})
	}
	return runtime.Snapshot{RobotID: "test-robot", Ready: true, Capabilities: capabilities}
}

func TestRunnerFailsClosedWhenRuntimeCapabilityIsUnavailable(t *testing.T) {
	store, _ := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-3", Intent: parsed, Approved: true}
	robot := &snapshotRobot{recordingRobot: recordingRobot{counts: map[string]int{}}, snapshot: func() runtime.Snapshot {
		snapshot := validSnapshot()
		for index := range snapshot.Capabilities {
			if snapshot.Capabilities[index].Name == "manipulation.pick" {
				snapshot.Capabilities[index].Available = false
				snapshot.Capabilities[index].Blockers = []string{"CALIBRATION_REQUIRED"}
			}
		}
		return snapshot
	}()}

	_, err := agent.NewRunner(store, robot).Run(context.Background(), task)
	if !errors.Is(err, runtime.ErrCapabilityUnavailable) {
		t.Fatalf("Run() error = %v, want ErrCapabilityUnavailable", err)
	}
	if got := robot.count("manipulation.pick"); got != 0 {
		t.Fatalf("pick count = %d, want 0", got)
	}
}

func TestRunnerFailsClosedWhenRuntimeReportsNotReady(t *testing.T) {
	store, _ := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-4", Intent: parsed, Approved: true}
	snapshot := validSnapshot()
	snapshot.Ready = false
	snapshot.Blockers = []string{"SERIAL_PORTS_UNAVAILABLE"}
	robot := &snapshotRobot{recordingRobot: recordingRobot{counts: map[string]int{}}, snapshot: snapshot}

	_, err := agent.NewRunner(store, robot).Run(context.Background(), task)
	if !errors.Is(err, runtime.ErrRobotNotReady) {
		t.Fatalf("Run() error = %v, want ErrRobotNotReady", err)
	}
}

type planInspectingRobot struct {
	pick  taskgraph.SkillStep
	place taskgraph.SkillStep
}

func (r *planInspectingRobot) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 0.98},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 0.99},
	}, nil
}

func (r *planInspectingRobot) Execute(_ context.Context, _ string, step taskgraph.SkillStep) (agent.SkillResult, error) {
	switch step.Skill {
	case "manipulation.pick":
		r.pick = step
	case "manipulation.place":
		r.place = step
	}
	return agent.SkillResult{Success: true, VerificationConfidence: 0.98}, nil
}

func TestRunnerExecutesLLMOrchestratedPlanWithDeterministicSafetyEnvelope(t *testing.T) {
	store, _ := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-llm-plan", Intent: parsed, Approved: true}
	task.Plan = &orchestration.Bundle{
		Source: orchestration.SourceLLM,
		Plans: []taskgraph.TaskPlan{{
			ID:   "task-llm-plan",
			Goal: "place the object",
			Steps: []taskgraph.SkillStep{
				{ID: "observe", Skill: "observe_scene", Arguments: map[string]any{}},
				{ID: "resolve", Skill: "resolve_targets", Arguments: map[string]any{"objectId": "@object", "destinationId": "@destination"}, DependsOn: []string{"observe"}},
				{ID: "plan_grasp", Skill: "plan_grasp", Arguments: map[string]any{"objectId": "@object"}, DependsOn: []string{"resolve"}},
				{ID: "pick", Skill: "manipulation.pick", Arguments: map[string]any{"targetRef": "@object"}, DependsOn: []string{"plan_grasp"}},
				{ID: "verify_grasp", Skill: "verify_grasp", Arguments: map[string]any{"objectId": "@object"}, DependsOn: []string{"pick"}},
				{ID: "place", Skill: "manipulation.place", Arguments: map[string]any{"targetRef": "@destination"}, DependsOn: []string{"verify_grasp"}},
				{ID: "verify_place", Skill: "verify_placement", Arguments: map[string]any{"objectId": "@object", "destinationId": "@destination"}, DependsOn: []string{"place"}},
			},
		}},
	}
	robot := &planInspectingRobot{}
	if _, err := agent.NewRunner(store, robot).Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if robot.pick.Arguments["targetRef"] != "red-cup" {
		t.Fatalf("pick arguments = %#v", robot.pick.Arguments)
	}
	if robot.place.Arguments["targetRef"] != "right-bin" {
		t.Fatalf("place arguments = %#v", robot.place.Arguments)
	}
	if robot.pick.ApprovalID == "" || robot.pick.LeaseMS == 0 || robot.pick.IdempotencyKey == "" {
		t.Fatalf("physical safety envelope was not filled: %+v", robot.pick)
	}
}
