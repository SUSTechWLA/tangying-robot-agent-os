package manipulation

import (
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

const defaultLeaseMS uint32 = 15_000

func physicalLeaseMS(skill string) uint32 {
	if skill == "navigation.navigate" {
		return 60_000
	}
	return defaultLeaseMS
}

func Catalog() []skills.SkillManifest {
	readOnly := func(name string, required ...string) skills.SkillManifest {
		return skills.SkillManifest{Name: name, SafetyLevel: skills.SafetyReadOnly, RequiredParameters: required}
	}
	physical := func(name string, required ...string) skills.SkillManifest {
		return skills.SkillManifest{
			Name:                  name,
			RequiredParameters:    required,
			SideEffect:            true,
			SafetyLevel:           skills.SafetyPhysical,
			DefaultLeaseMS:        physicalLeaseMS(name),
			AllowedSafetyProfiles: []string{"desktop_standard", "simulation"},
			ApprovalPolicy:        skills.ApprovalPolicy{Required: true},
		}
	}
	return []skills.SkillManifest{
		readOnly("observe_scene"),
		readOnly("resolve_targets", "objectId", "destinationId"),
		physical("navigation.navigate", "goalPose"),
		readOnly("plan_grasp", "objectId", "destinationId"),
		physical("manipulation.pick", "targetRef"),
		readOnly("verify_grasp", "objectId"),
		physical("manipulation.place", "targetRef"),
		readOnly("verify_placement", "objectId", "destinationId"),
		physical("recover_to_safe_pose"),
		physical("emergency_stop"),
	}
}

func Plan(task GroundedTask, deadline time.Time) taskgraph.TaskPlan {
	prefix := task.StepIDPrefix
	approvalID := "approval:" + task.TaskID + ":physical"
	step := func(id, skill string, dependencies ...string) taskgraph.SkillStep {
		prefixed := make([]string, 0, len(dependencies))
		for _, dependency := range dependencies {
			prefixed = append(prefixed, prefix+dependency)
		}
		return taskgraph.SkillStep{ID: prefix + id, Skill: skill, RobotID: task.RobotID, DependsOn: prefixed}
	}
	observe := step("observe", "observe_scene")
	resolve := step("resolve", "resolve_targets", "observe")
	resolve.Arguments = map[string]any{
		"objectId":              task.Object.ID,
		"objectConfidence":      task.Object.Confidence,
		"destinationId":         task.Destination.ID,
		"destinationConfidence": task.Destination.Confidence,
	}
	planGrasp := step("plan_grasp", "plan_grasp", "resolve")
	planGrasp.Arguments = map[string]any{
		"objectId":      task.Object.ID,
		"destinationId": task.Destination.ID,
		"keepUpright":   task.KeepUpright,
	}
	pick := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix, "pick", "manipulation.pick", "plan_grasp")
	pick.Arguments = map[string]any{"targetRef": task.Object.ID, "keepUpright": task.KeepUpright}
	verifyGrasp := step("verify_grasp", "verify_grasp", "pick")
	verifyGrasp.Arguments = map[string]any{"objectId": task.Object.ID}
	place := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix, "place", "manipulation.place", "verify_grasp")
	place.Arguments = map[string]any{"targetRef": task.Destination.ID, "keepUpright": task.KeepUpright}
	verifyPlace := step("verify_place", "verify_placement", "place")
	verifyPlace.Arguments = map[string]any{"objectId": task.Object.ID, "destinationId": task.Destination.ID}
	steps := []taskgraph.SkillStep{observe, resolve}
	if len(task.NavigationGoal) > 0 {
		navigate := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix, "navigate", "navigation.navigate", "resolve")
		navigate.Arguments = map[string]any{"goalPose": append([]float64(nil), task.NavigationGoal...)}
		after := step("observe_after_navigation", "observe_scene", "navigate")
		planGrasp.DependsOn = []string{after.ID}
		steps = append(steps, navigate, after)
	}
	steps = append(steps, planGrasp, pick, verifyGrasp, place, verifyPlace)

	goal := "pick and place a grounded tabletop object"
	if task.Action == ActionFetch {
		goal = "fetch a grounded tabletop object to the front delivery tray"
	}
	return taskgraph.TaskPlan{
		ID:         task.TaskID,
		Goal:       goal,
		Domain:     "manipulation",
		Revision:   1,
		Steps:      steps,
		Budget:     taskgraph.Budget{MaxSteps: len(steps) + 2, MaxRetries: 3},
		StopPolicy: taskgraph.StopPolicy{StopWhenEnough: true, StopOnSafety: true},
	}
}

func physicalStep(taskID, approvalID string, deadline time.Time, robotID, prefix, id, skill string, dependencies ...string) taskgraph.SkillStep {
	prefixed := make([]string, 0, len(dependencies))
	for _, dependency := range dependencies {
		prefixed = append(prefixed, prefix+dependency)
	}
	return taskgraph.SkillStep{
		ID:             prefix + id,
		Skill:          skill,
		RobotID:        robotID,
		DependsOn:      prefixed,
		SafetyLevel:    string(skills.SafetyPhysical),
		ApprovalID:     approvalID,
		DeadlineUnixMS: deadline.UnixMilli(),
		LeaseMS:        physicalLeaseMS(skill),
		IdempotencyKey: fmt.Sprintf("%s-%s-1", taskID, prefix+id),
	}
}
