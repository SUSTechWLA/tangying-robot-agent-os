package agent_test

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/latency"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type recordingRobot struct {
	mu      sync.Mutex
	counts  map[string]int
	taskIDs []string
}

type homeRouteGrounder struct{}

func (homeRouteGrounder) Ground(_ context.Context, parsed manipulation.Intent) (manipulation.GroundedTask, error) {
	goals := make([][]float64, len(parsed.RouteRooms))
	for index := range goals {
		goals[index] = []float64{float64(index), 1, 0, 1, 0, 0, 0}
	}
	return manipulation.GroundedTask{Action: parsed.Action, RouteRooms: parsed.RouteRooms,
		RouteGoals: goals, ReturnToStart: parsed.ReturnToStart, RobotID: "home-test"}, nil
}

type preflightFailureRobot struct{}

func (preflightFailureRobot) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	if command.Capability == runtime.CapabilityNavigate {
		return runtime.Result{Code: "NAV_MAP_NOT_READY", Message: "mapping has not reached visual readiness"}, nil
	}
	return evidenceResult(command.StepID), nil
}

func TestRunnerKeepsNeverDispatchedNavigationRetryable(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("从客厅出发，去厨房确认一下环境")
	task := &tasks.Task{ID: "home-preflight", Intent: parsed, Approved: true}
	_, err = agent.NewRunner(store, homeRouteGrounder{}, preflightFailureRobot{}).Run(context.Background(), task)
	if err == nil || !strings.Contains(err.Error(), "NAV_MAP_NOT_READY") {
		t.Fatalf("run error = %v", err)
	}
	runs, err := store.ListStepRuns(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) < 2 || runs[1].Status != middleware.StepFailed {
		t.Fatalf("execution history = %+v, want retryable failed navigation", runs)
	}
	if err := agent.NewRunner(store, nil, nil).CheckRecovery(context.Background(), task.ID); err != nil {
		t.Fatalf("preflight failure became unknown physical outcome: %v", err)
	}
}

func (r *recordingRobot) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 0.98},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 0.99},
	}, nil
}

// evidenceResult is what a runtime that can confirm its own action returns: a
// success code plus the post-command observation that proves the world changed.
// Test fixtures use it so they model a real runtime instead of a bare code,
// which the closed-loop gate deliberately refuses.
func evidenceResult(stepID string) runtime.Result {
	observedAt := time.Now().UTC()
	return runtime.Result{
		Success:                true,
		VerificationConfidence: 0.98,
		ObservationID:          "receipt/" + stepID,
		Evidence: &telemetry.Snapshot{
			ObservedAt: observedAt,
			Reconstruction: &robotcontract.Reconstruction{
				ObservationID:    "receipt/" + stepID,
				SourceID:         "test-robot/scene",
				ObservedAtUnixMS: observedAt.UnixMilli(),
			},
		},
	}
}

func (r *recordingRobot) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.counts[string(command.Capability)]++
	r.taskIDs = append(r.taskIDs, command.TaskID)
	return evidenceResult(command.StepID), nil
}

// evidenceFreeRobot models a runtime whose tool reports success but never says
// what the world looks like afterwards. It must not be able to complete a
// physical write, however successful its return code is.
type evidenceFreeRobot struct{ recordingRobot }

func (r *evidenceFreeRobot) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.counts[string(command.Capability)]++
	return runtime.Result{Success: true, VerificationConfidence: 0.98}, nil
}

func (r *recordingRobot) count(skill string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.counts[skill]
}

func TestRunnerRestartDoesNotRepeatCompletedPick(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-1", Intent: parsed, Approved: true}
	robot := &recordingRobot{counts: map[string]int{}}

	if _, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if _, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task); err != nil {
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

type revisionRecordingRobot struct {
	recordingRobot
	commands []runtime.Command
}

func (r *revisionRecordingRobot) Invoke(ctx context.Context, command runtime.Command) (runtime.Result, error) {
	r.commands = append(r.commands, command)
	return r.recordingRobot.Invoke(ctx, command)
}

func TestRunnerCommandsCarryTaskRevisionIdentity(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-revision", Intent: parsed, Approved: true, CurrentRevision: 3, AggregateVersion: 9}
	robot := &revisionRecordingRobot{recordingRobot: recordingRobot{counts: map[string]int{}}}
	if _, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if len(robot.commands) == 0 {
		t.Fatal("runner emitted no commands")
	}
	for _, command := range robot.commands {
		if command.TaskRevision != 3 || command.AggregateVersion != 9 || command.StepID == "" ||
			command.CommandID == "" || command.IdempotencyKey == "" {
			t.Fatalf("revision identity missing: %#v", command)
		}
	}
}

func TestRunnerDispatchDeadlineUsesStepBudgetAndParentDeadline(t *testing.T) {
	for _, parentBudget := range []time.Duration{2 * time.Second, 2 * time.Minute} {
		store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
		if err != nil {
			t.Fatal(err)
		}
		parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
		robot := &revisionRecordingRobot{recordingRobot: recordingRobot{counts: map[string]int{}}}
		ctx, cancel := context.WithTimeout(context.Background(), parentBudget)
		parentDeadline, _ := ctx.Deadline()
		_, err = agent.NewRunner(store, robot, robot).Run(ctx, &tasks.Task{ID: "bounded-dispatch", Intent: parsed, Approved: true})
		cancel()
		store.Close()
		if err != nil {
			t.Fatal(err)
		}
		for _, command := range robot.commands {
			if command.Deadline.After(parentDeadline) || command.Deadline.After(time.Now().Add(command.Lease)) {
				t.Fatalf("%s deadline %s exceeds parent or per-step lease %s", command.Capability, command.Deadline, command.Lease)
			}
		}
	}
}

func TestRunnerRequiresApprovalBeforePhysicalSkill(t *testing.T) {
	store, _ := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-2", Intent: parsed, Approved: false}
	robot := &recordingRobot{counts: map[string]int{}}
	_, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task)
	if err == nil || robot.count("manipulation.pick") != 0 {
		t.Fatalf("err = %v, pick count = %d", err, robot.count("manipulation.pick"))
	}
}

func TestRunnerExecutesCompoundIntentInOrderAndResumesWholeSequence(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来")
	task := &tasks.Task{ID: "task-sequence", Intent: parsed, Approved: true}
	robot := &recordingRobot{counts: map[string]int{}}

	first, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task)
	if err != nil {
		t.Fatal(err)
	}
	if got := robot.count("manipulation.pick"); got != 2 {
		t.Fatalf("pick count = %d, want 2", got)
	}
	if len(first.CompletedSteps) != 14 {
		t.Fatalf("completed steps = %d, want 14", len(first.CompletedSteps))
	}

	second, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task)
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

func (r *snapshotRobot) Info(context.Context) (runtime.Snapshot, error) {
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
	store, _ := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
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

	_, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task)
	if !errors.Is(err, runtime.ErrCapabilityUnavailable) {
		t.Fatalf("Run() error = %v, want ErrCapabilityUnavailable", err)
	}
	if got := robot.count("manipulation.pick"); got != 0 {
		t.Fatalf("pick count = %d, want 0", got)
	}
}

func TestRunnerFailsClosedWhenRuntimeReportsNotReady(t *testing.T) {
	store, _ := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-4", Intent: parsed, Approved: true}
	snapshot := validSnapshot()
	snapshot.Ready = false
	snapshot.Blockers = []string{"SERIAL_PORTS_UNAVAILABLE"}
	robot := &snapshotRobot{recordingRobot: recordingRobot{counts: map[string]int{}}, snapshot: snapshot}

	_, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task)
	if !errors.Is(err, runtime.ErrRobotNotReady) {
		t.Fatalf("Run() error = %v, want ErrRobotNotReady", err)
	}
}

type planInspectingRobot struct {
	pick  runtime.Command
	place runtime.Command
}

func (r *planInspectingRobot) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 0.98},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 0.99},
	}, nil
}

func (r *planInspectingRobot) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	switch command.Capability {
	case "manipulation.pick":
		r.pick = command
	case "manipulation.place":
		r.place = command
	}
	return evidenceResult(command.StepID), nil
}

func TestRunnerExecutesLLMOrchestratedPlanWithDeterministicSafetyEnvelope(t *testing.T) {
	store, _ := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
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
	if _, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if robot.pick.Parameters["targetRef"] != "red-cup" {
		t.Fatalf("pick arguments = %#v", robot.pick.Parameters)
	}
	if robot.place.Parameters["targetRef"] != "right-bin" {
		t.Fatalf("place arguments = %#v", robot.place.Parameters)
	}
	if robot.pick.ApprovalID == "" || robot.pick.Lease == 0 || robot.pick.IdempotencyKey == "" {
		t.Fatalf("physical safety envelope was not filled: %+v", robot.pick)
	}
}

func TestRunnerFailsClosedWhenRequestedAdapterDiffersFromConnectedRuntime(t *testing.T) {
	store, _ := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "task-adapter-mismatch", Intent: parsed, Approved: true, Adapter: "xlerobot_direct"}
	snapshot := validSnapshot()
	snapshot.Adapter = "mujoco"
	robot := &snapshotRobot{recordingRobot: recordingRobot{counts: map[string]int{}}, snapshot: snapshot}

	_, err := agent.NewRunner(store, robot, robot).Run(context.Background(), task)
	if !errors.Is(err, runtime.ErrAdapterMismatch) {
		t.Fatalf("Run() error = %v, want ErrAdapterMismatch", err)
	}
	if got := robot.count("manipulation.pick"); got != 0 {
		t.Fatalf("pick count = %d, want 0", got)
	}
}

type budgetRobot struct{ commands []runtime.Command }

func (r *budgetRobot) Info(context.Context) (runtime.Snapshot, error) {
	caps := validSnapshot()
	caps.Capabilities = append(caps.Capabilities, runtime.Capability{Name: "navigation.navigate", Available: true, DefaultTimeout: 4 * time.Minute}, runtime.Capability{Name: "verify_arrival", Available: true})
	return caps, nil
}
func (r *budgetRobot) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	r.commands = append(r.commands, command)
	return evidenceResult(command.StepID), nil
}
func TestRunnerUsesConnectedNavigationBudgetAtActualDispatch(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "budget.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, err := intent.NewDeterministicParser().Parse("从客厅出发，去厨房确认一下环境")
	if err != nil {
		t.Fatal(err)
	}
	robot := &budgetRobot{}
	_, err = agent.NewRunner(store, homeRouteGrounder{}, robot).Run(context.Background(), &tasks.Task{ID: "budget", Intent: parsed, Approved: true})
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, command := range robot.commands {
		if command.Capability == runtime.CapabilityNavigate {
			found = true
			if command.Lease != 4*time.Minute || time.Until(command.Deadline) < 3*time.Minute {
				t.Fatalf("runtime declaration ignored: %#v", command)
			}
		}
	}
	if !found {
		t.Fatal("navigation did not dispatch")
	}
}

// ── step timing ─────────────────────────────────────────────────────────────
// A duration without its phases cannot be acted on: "the task took 40 seconds"
// does not say whether to look at the scheduler, the safety gate, the motion or
// the evidence gate. These cases hold the two properties that make the new
// telemetry usable: every exit path is recorded, and the phases add up.

func TestRunnerRecordsEveryPhaseOfAStep(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("从客厅出发，去厨房确认一下环境")
	task := &tasks.Task{ID: "home-timing", Intent: parsed, Approved: true}

	// The clock runs behind real time so the runtime's own evidence timestamps
	// stay after the dispatch instant: the closure gate must still admit it.
	base := time.Now().Add(-time.Minute)
	ticks := []time.Duration{0, 3 * time.Millisecond, 5 * time.Millisecond,
		705 * time.Millisecond, 709 * time.Millisecond}
	index := 0
	runner := agent.NewRunner(store, homeRouteGrounder{}, &recordingRobot{counts: map[string]int{}})
	runner.Latency = latency.New(64)
	runner.Now = func() time.Time {
		offset := ticks[index]
		if index < len(ticks)-1 {
			index++
		}
		return base.Add(offset)
	}
	if _, err := runner.Run(context.Background(), task); err != nil {
		t.Fatalf("run: %v", err)
	}
	report, err := runner.Latency.Report(latency.GroupByCapability, 0, base.Add(time.Minute))
	if err != nil {
		t.Fatalf("report: %v", err)
	}
	// observe_scene plus one navigate and one verify_arrival per requested room.
	if report.Count == 0 {
		t.Fatalf("no step timings were recorded: %#v", report)
	}
	var total int
	for _, group := range report.Groups {
		if group.Outcomes[latency.OutcomeCompleted] == 0 {
			t.Fatalf("group %s recorded no completion: %#v", group.Key, group)
		}
		total += group.Count
		for _, phase := range latency.Phases {
			if group.Phases[phase].Count != group.Count {
				t.Fatalf("group %s phase %s has %d samples for %d steps",
					group.Key, phase, group.Phases[phase].Count, group.Count)
			}
		}
		if group.Total.P50 < group.Phases[latency.Execute].P50 {
			t.Fatalf("group %s total smaller than its execute phase: %#v", group.Key, group)
		}
	}
	if total == 0 {
		t.Fatal("expected at least one completed step")
	}
}

func TestRunnerRecordsFailedStepsSoSlowFailuresAreVisible(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("从客厅出发，去厨房确认一下环境")
	task := &tasks.Task{ID: "home-timing-failure", Intent: parsed, Approved: true}
	runner := agent.NewRunner(store, homeRouteGrounder{}, preflightFailureRobot{})
	runner.Latency = latency.New(64)
	if _, err := runner.Run(context.Background(), task); err == nil {
		t.Fatal("the preflight failure must still fail the run")
	}
	report, _ := runner.Latency.Report(latency.GroupByOutcome, 0, time.Now())
	failed := 0
	for _, group := range report.Groups {
		if group.Key == latency.OutcomeFailed {
			failed = group.Count
		}
	}
	if failed == 0 {
		t.Fatalf("a refused step left no timing behind: %#v", report.Groups)
	}
}

func TestRunnerWithoutARecorderStillRuns(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("从客厅出发，去厨房确认一下环境")
	task := &tasks.Task{ID: "home-no-timing", Intent: parsed, Approved: true}
	// Observability is optional by construction: a task must not depend on it.
	if _, err := agent.NewRunner(store, homeRouteGrounder{}, &recordingRobot{counts: map[string]int{}}).Run(context.Background(), task); err != nil {
		t.Fatalf("run without a recorder: %v", err)
	}
}
