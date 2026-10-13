package agent_test

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type pointCloudRobot struct {
	grounds          int
	commands         []runtime.Command
	failVerification bool
	failStage        string
	refuseGround     bool
	rejectedTokens   map[string]bool
	targets          map[string]*manipulation.SemanticTarget
	route            map[string]any
}

func (r *pointCloudRobot) Ground(_ context.Context, intent manipulation.Intent) (manipulation.GroundedTask, error) {
	r.grounds++
	if r.refuseGround {
		return manipulation.GroundedTask{}, errors.New("resume must not reselect candidate")
	}
	if r.targets == nil {
		r.targets = map[string]*manipulation.SemanticTarget{}
	}
	r.targets[fmt.Sprintf("attestation-%d", r.grounds)] = intent.SemanticTarget
	return manipulation.GroundedTask{Action: manipulation.ActionHomeRoute, RouteRooms: []string{intent.SemanticTarget.Name},
		RouteGoals: [][]float64{{float64(r.grounds), 1, 0, 1, 0, 0, 0}}, SemanticBindings: []string{fmt.Sprintf("attestation-%d", r.grounds)},
		GoalEvidence: map[string]any{"goalSource": "versioned_semantic_cloud", "semanticTarget": intent.SemanticTarget, "worldFrameRevision": "world-a"}}, nil
}
func (r *pointCloudRobot) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	r.commands = append(r.commands, command)
	failStage := r.failStage
	if failStage == "" {
		failStage = "task02-"
	}
	if r.failVerification && command.Capability == "verify_arrival" && strings.Contains(command.StepID, failStage) {
		r.failVerification = false
		return runtime.Result{Code: "SEMANTIC_NOT_OBSERVED", Message: "no unique current RGB-D match"}, nil
	}
	result := evidenceResult(command.StepID, time.Now())
	result.Evidence.RobotID = command.RobotID
	if token, ok := command.Parameters["semanticBinding"].(string); ok {
		if r.rejectedTokens[token] {
			return runtime.Result{Code: "SEMANTIC_BINDING_INVALID", Message: "runtime binding expired"}, nil
		}
		target := r.targets[token]
		wire, _ := json.Marshal(target.Identity)
		var identity map[string]any
		_ = json.Unmarshal(wire, &identity)
		execution := map[string]any{"identity": identity, "semanticRevision": target.SemanticRevision, "annotationId": target.AnnotationID,
			"worldFrameRevision": "world-a", "verificationMode": target.VerificationMode, "allowStaleSearch": target.AllowStaleSearch, "targetFresh": true}
		hash := fmt.Sprintf("%x", sha256.Sum256([]byte(token)))
		if command.Capability == "navigation.navigate" {
			r.route = map[string]any{"commandId": command.CommandID, "semanticBindingSha256": hash, "semanticExecution": execution, "goalPose": command.Parameters["goalPose"]}
		}
		result.Evidence.RobotState = map[string]any{"map_route": r.route}
		if command.Capability == "verify_arrival" {
			result.Evidence.RobotState["semantic_verification"] = map[string]any{
				"commandId": command.CommandID, "navigationCommandId": r.route["commandId"], "semanticBindingSha256": hash, "semanticExecution": execution,
				"arrivalVerified": true, "freshCaptureAtUnixMs": result.Evidence.ObservedAt.UnixMilli(), "semanticConfirmed": false}
		}
	}
	return result, nil
}
func semanticRunnerTask() *tasks.Task {
	r := strings.Repeat("a", 64)
	makeIntent := func(id string) manipulation.Intent {
		return manipulation.Intent{Action: manipulation.ActionHomeRoute, RobotID: "robot-a", RouteRooms: []string{id},
			SemanticTarget: &manipulation.SemanticTarget{Identity: manipulation.SemanticIdentity{RobotID: "robot-a", MapID: "map", MapRevision: r, CalibrationRevision: r, CloudSHA256: r, FrameID: "map"},
				SemanticRevision: r, AnnotationID: id, Name: id, MaxDistanceM: 2.5, VerificationMode: "arrival"}}
	}
	return &tasks.Task{ID: "semantic-long-route", Approved: true, CurrentRevision: 1,
		Intent: manipulation.Intent{Action: manipulation.ActionHomeRoute, Sequence: []manipulation.Intent{makeIntent("north"), makeIntent("south")}}}
}

func TestSemanticCheckpointsPersistBeforeMotionAndResumeWithoutReselection(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "runtime.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	task := semanticRunnerTask()
	robot := &pointCloudRobot{failVerification: true}
	var events []tasks.TaskEvent
	newRunner := func() *agent.Runner {
		r := agent.NewRunner(store, robot, robot)
		r.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
			events = append(events, event)
			return nil
		}
		return r
	}
	if _, err := newRunner().Run(t.Context(), task); err == nil || !strings.Contains(err.Error(), "SEMANTIC_NOT_OBSERVED") {
		t.Fatalf("expected second checkpoint failure, got %v", err)
	}
	if robot.grounds != 2 {
		t.Fatalf("grounded %d stages", robot.grounds)
	}
	frozen := 0
	for _, event := range events {
		if event.Type == "SEMANTIC_GROUNDING_FROZEN" {
			frozen++
		}
	}
	if frozen != 2 {
		t.Fatalf("missing durable checkpoints: %+v", events)
	}
	// A new Runner receives the durable event snapshot, just as restart/recovery
	// does. Even a now-unavailable planner cannot change the already run goals.
	task.Events = events
	robot.refuseGround = true
	if _, err := newRunner().RunControlled(t.Context(), task, agent.RunControl{ObservationAttempt: "retry-1"}); err != nil {
		t.Fatal(err)
	}
	if robot.grounds != 2 {
		t.Fatal("resume re-grounded a physical goal")
	}
	navigation := 0
	for _, command := range robot.commands {
		if command.Capability == "navigation.pre_position" && command.Parameters["semanticBinding"] == nil {
			t.Fatal("initial physical positioning lost the same semantic attestation")
		}
		if command.Capability == "navigation.navigate" {
			navigation++
			if command.Parameters["semanticBinding"] == nil {
				t.Fatal("navigation lost attestation")
			}
		}
		if command.Capability == "verify_arrival" && command.Parameters["semanticBinding"] == nil {
			t.Fatal("verification lost attestation")
		}
	}
	if navigation != 2 {
		t.Fatalf("physical navigation repeated: %d", navigation)
	}
}

func TestSemanticControlledResumeSkipsCompletedHistoryAndOnlyVerifiesCurrentStage(t *testing.T) {
	for _, currentTokenExpired := range []bool{false, true} {
		t.Run(fmt.Sprintf("current-token-expired=%t", currentTokenExpired), func(t *testing.T) {
			store, err := sqlite.Open(filepath.Join(t.TempDir(), "runtime.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			task := semanticRunnerTask()
			third := task.Intent.Sequence[0]
			target := *third.SemanticTarget
			target.Name, target.AnnotationID = "east", "east"
			third.SemanticTarget, third.RouteRooms = &target, []string{"east"}
			task.Intent.Sequence = append(task.Intent.Sequence, third)
			robot := &pointCloudRobot{failVerification: true, failStage: "task03-"}
			var events []tasks.TaskEvent
			newRunner := func() *agent.Runner {
				r := agent.NewRunner(store, robot, robot)
				r.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
					events = append(events, event)
					return nil
				}
				return r
			}
			control := agent.RunControl{StepPrefix: "cap-02-"}
			if _, err := newRunner().RunControlled(t.Context(), task, control); err == nil || !strings.Contains(err.Error(), "SEMANTIC_NOT_OBSERVED") {
				t.Fatalf("expected third stage verification failure, got %v", err)
			}
			task.Events = events
			before := len(robot.commands)
			// The robot has reached stage three. Historical tokens may even have
			// expired; they cannot authorize new reads or navigation on resume.
			robot.refuseGround = true
			robot.rejectedTokens = map[string]bool{"attestation-1": true, "attestation-2": true, "attestation-3": currentTokenExpired}
			control.ObservationAttempt = "resume-current-stage"
			_, err = newRunner().RunControlled(t.Context(), task, control)
			if currentTokenExpired {
				if err == nil || !strings.Contains(err.Error(), "SEMANTIC_BINDING_INVALID") {
					t.Fatalf("expired current binding must reject without re-signing: %v", err)
				}
			} else if err != nil {
				t.Fatal(err)
			}
			if robot.grounds != 3 {
				t.Fatalf("resume re-grounded completed motion: %d", robot.grounds)
			}
			resumed := robot.commands[before:]
			if len(resumed) != 2 || resumed[0].Capability != "observe_scene" || resumed[1].Capability != "verify_arrival" {
				t.Fatalf("expected only fresh observation and current arrival verification, got %+v", resumed)
			}
			for _, command := range resumed {
				if !strings.Contains(command.StepID, "cap-02-task03-") || !strings.Contains(command.CommandID, "/resume-read/resume-current-stage") {
					t.Fatalf("resumed historical stage or reused old read: %+v", command)
				}
			}
		})
	}
}

func TestSemanticCheckpointEvidenceFailurePreventsMotion(t *testing.T) {
	for _, missing := range []bool{false, true} {
		t.Run(fmt.Sprint(missing), func(t *testing.T) {
			store, err := sqlite.Open(filepath.Join(t.TempDir(), "runtime.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			robot := &pointCloudRobot{}
			r := agent.NewRunner(store, robot, robot)
			if !missing {
				r.TaskEvents = func(context.Context, string, tasks.TaskEvent) error { return errors.New("event storage unavailable") }
			}
			if _, err := r.Run(t.Context(), semanticRunnerTask()); err == nil {
				t.Fatal("missing grounding persistence was ignored")
			}
			if len(robot.commands) != 0 {
				t.Fatal("motion occurred before grounding persistence")
			}
		})
	}
}

func TestSemanticCheckpointCannotBindCompletedMotionToChangedIntent(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "runtime.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	task := semanticRunnerTask()
	robot := &pointCloudRobot{}
	r := agent.NewRunner(store, robot, robot)
	r.TaskEvents = func(context.Context, string, tasks.TaskEvent) error { return nil }
	if _, err := r.Run(t.Context(), task); err != nil {
		t.Fatal(err)
	}
	commands := len(robot.commands)
	task.Intent.Sequence[0].SemanticTarget.MaxDistanceM = 1.5
	if _, err := r.RunControlled(t.Context(), task, agent.RunControl{ObservationAttempt: "changed-target"}); !errors.Is(err, agent.ErrResumeBindingChanged) {
		t.Fatalf("changed target accepted: %v", err)
	}
	if len(robot.commands) != commands {
		t.Fatal("changed target replayed motion")
	}
}

func TestSemanticMissingCheckpointAfterPhysicalCompletionCannotReplan(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "runtime.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	task := semanticRunnerTask()
	robot := &pointCloudRobot{}
	r := agent.NewRunner(store, robot, robot)
	r.TaskEvents = func(context.Context, string, tasks.TaskEvent) error { return nil }
	if _, err := r.Run(t.Context(), task); err != nil {
		t.Fatal(err)
	}
	task.Events = nil
	if _, err := r.RunControlled(t.Context(), task, agent.RunControl{ObservationAttempt: "missing-checkpoint"}); !errors.Is(err, agent.ErrResumeBindingChanged) {
		t.Fatalf("missing checkpoint did not block: %v", err)
	}
	if robot.grounds != 2 {
		t.Fatal("missing evidence led to a new physical candidate")
	}
}
