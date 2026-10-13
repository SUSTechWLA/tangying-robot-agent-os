package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/compiler"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// A completed checkpoint is historical evidence, not a claim that the robot is
// still there. After groundIntent and validateResumeBindings have checked its
// immutable target and commands, skip the entire stage only when every child
// has a durable completion. Refreshing its old arrival verification would fail
// after later navigation or a Runtime restart and prevent forward progress.
// An incomplete stage still follows the normal fresh-read recovery path with
// its original token; this never re-signs a token or replays completed motion.
func (r *Runner) completedSemanticStage(ctx context.Context, task *tasks.Task, graph compiler.ExecutionGraph, control RunControl) (bool, error) {
	if len(graph.Order) == 0 {
		return false, nil
	}
	for _, id := range graph.Order {
		status, err := r.store.StepStatus(ctx, task.ID, revisionExecutionStepID(task, control.StepPrefix+id))
		if err != nil {
			return false, err
		}
		if status != middleware.StepCompleted {
			return false, nil
		}
	}
	if control.BeforeStep != nil {
		if err := control.BeforeStep(ctx); err != nil {
			return false, err
		}
	}
	return true, nil
}

// A completed navigation cannot be attached to a newly sampled candidate on
// recovery. Freeze its grounding before the first action and reuse that exact
// provider binding on subsequent attempts. A Runtime restart may invalidate its
// token; that requires explicit reconciliation/replanning, never silent replay.
func (r *Runner) groundIntent(ctx context.Context, task *tasks.Task, intent manipulation.Intent, index, count int, control RunControl) (manipulation.GroundedTask, error) {
	if intent.SemanticTarget == nil {
		return r.grounder.Ground(ctx, intent)
	}
	if err := intent.SemanticTarget.Validate(intent.RobotID); err != nil {
		return manipulation.GroundedTask{}, err
	}
	if r.TaskEvents == nil {
		return manipulation.GroundedTask{}, errors.New("semantic execution requires durable grounding evidence")
	}
	prefix := ""
	if count > 1 {
		prefix = fmt.Sprintf("task%02d-", index+1)
	}
	checkpointID := revisionExecutionStepID(task, control.StepPrefix+prefix+"semantic-grounding")
	binding := capability.Fingerprint(intent)
	events := task.Events
	if r.Tasks != nil {
		latest, err := r.Tasks.Get(ctx, task.ID)
		if err != nil {
			return manipulation.GroundedTask{}, err
		}
		if latest.CurrentRevision != task.CurrentRevision {
			return manipulation.GroundedTask{}, ErrResumeBindingChanged
		}
		events = latest.Events
	}
	for i := len(events) - 1; i >= 0; i-- {
		event := events[i]
		if event.Type != "SEMANTIC_GROUNDING_FROZEN" || event.StepID != checkpointID {
			continue
		}
		if event.Payload["intentBinding"] != binding {
			return manipulation.GroundedTask{}, ErrResumeBindingChanged
		}
		wire, err := json.Marshal(event.Payload["grounded"])
		if err != nil {
			return manipulation.GroundedTask{}, err
		}
		var saved manipulation.GroundedTask
		if err = json.Unmarshal(wire, &saved); err != nil {
			return saved, err
		}
		if saved.TaskID != task.ID || saved.RobotID != intent.RobotID || saved.StepIDPrefix != prefix ||
			saved.Action != manipulation.ActionHomeRoute || len(saved.RouteGoals) != 1 || len(saved.RouteGoals[0]) != 7 ||
			len(saved.RouteRooms) != 1 || saved.RouteRooms[0] != intent.SemanticTarget.Name || len(saved.SemanticBindings) != 1 || saved.SemanticBindings[0] == "" ||
			semanticTargetFingerprint(saved.GoalEvidence["semanticTarget"]) != capability.Fingerprint(intent.SemanticTarget) {
			return manipulation.GroundedTask{}, fmt.Errorf("%w: corrupt semantic grounding checkpoint", ErrResumeBindingChanged)
		}
		return saved, nil
	}
	// Missing evidence cannot mean "choose a new target" after any physical
	// step has begun. Failed-before-admission records are safe to re-ground.
	reader, ok := r.store.(middleware.ExecutionReader)
	if !ok {
		return manipulation.GroundedTask{}, ErrRecoveryUnavailable
	}
	runs, err := reader.ListStepRuns(ctx, task.ID)
	if err != nil {
		return manipulation.GroundedTask{}, err
	}
	stepPrefix := revisionExecutionStepID(task, control.StepPrefix+prefix)
	for _, run := range runs {
		if len(run.StepID) >= len(stepPrefix) && run.StepID[:len(stepPrefix)] == stepPrefix && run.SafetyLevel == "physical_motion" &&
			(run.Status == middleware.StepStarted || run.Status == middleware.StepCompleted) {
			return manipulation.GroundedTask{}, fmt.Errorf("%w: missing semantic grounding evidence", ErrResumeBindingChanged)
		}
	}
	grounded, err := r.grounder.Ground(ctx, intent)
	if err != nil {
		return grounded, err
	}
	grounded.TaskID, grounded.RobotID, grounded.Action, grounded.StepIDPrefix = task.ID, intent.RobotID, intent.Action, prefix
	if len(grounded.RouteGoals) != 1 || len(grounded.SemanticBindings) != 1 || grounded.SemanticBindings[0] == "" ||
		semanticTargetFingerprint(grounded.GoalEvidence["semanticTarget"]) != capability.Fingerprint(intent.SemanticTarget) {
		return manipulation.GroundedTask{}, errors.New("semantic grounder did not preserve its approved target and provider binding")
	}
	event := tasks.TaskEvent{Type: "SEMANTIC_GROUNDING_FROZEN", StepID: checkpointID,
		Payload: map[string]any{"intentBinding": binding, "grounded": grounded, "taskRevision": task.CurrentRevision, "goalSource": "versioned_semantic_cloud"}}
	if err := r.TaskEvents(ctx, task.ID, event); err != nil {
		return manipulation.GroundedTask{}, fmt.Errorf("persist semantic grounding: %w", err)
	}
	// Keep the caller's in-memory view coherent too, including cloud workers
	// whose TaskEvents callback writes remotely but do not have a TaskReader.
	task.Events = append(task.Events, event)
	return grounded, nil
}

func semanticTargetFingerprint(value any) string {
	wire, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	var target manipulation.SemanticTarget
	if json.Unmarshal(wire, &target) != nil {
		return ""
	}
	return capability.Fingerprint(&target)
}
