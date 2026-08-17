package manipulation

import (
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

const defaultLeaseMS uint32 = 15_000

func Catalog() []skills.SkillManifest {
	readOnly := func(name string) skills.SkillManifest {
		return skills.SkillManifest{Name: name, SafetyLevel: skills.SafetyReadOnly}
	}
	physical := func(name string) skills.SkillManifest {
		return skills.SkillManifest{
			Name:                  name,
			SideEffect:            true,
			SafetyLevel:           skills.SafetyPhysical,
			DefaultLeaseMS:        defaultLeaseMS,
			AllowedSafetyProfiles: []string{"desktop_standard", "simulation"},
			ApprovalPolicy:        skills.ApprovalPolicy{Required: true},
		}
	}
	return []skills.SkillManifest{
		readOnly("observe_scene"),
		readOnly("resolve_targets"),
		readOnly("plan_grasp"),
		physical("manipulation.pick"),
		readOnly("verify_grasp"),
		physical("manipulation.place"),
		readOnly("verify_placement"),
		physical("recover_to_safe_pose"),
		physical("emergency_stop"),
	}
}

func Plan(task GroundedTask, deadline time.Time) taskgraph.TaskPlan {
	approvalID := "approval:" + task.TaskID + ":physical"
	step := func(id, skill string, dependencies ...string) taskgraph.SkillStep {
		return taskgraph.SkillStep{ID: id, Skill: skill, DependsOn: dependencies}
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
	planGrasp.Arguments = map[string]any{"objectId": task.Object.ID, "keepUpright": task.KeepUpright}
	pick := physicalStep(task.TaskID, approvalID, deadline, "pick", "manipulation.pick", "plan_grasp")
	pick.Arguments = map[string]any{"targetRef": task.Object.ID, "keepUpright": task.KeepUpright}
	verifyGrasp := step("verify_grasp", "verify_grasp", "pick")
	verifyGrasp.Arguments = map[string]any{"objectId": task.Object.ID}
	place := physicalStep(task.TaskID, approvalID, deadline, "place", "manipulation.place", "verify_grasp")
	place.Arguments = map[string]any{"targetRef": task.Destination.ID, "keepUpright": task.KeepUpright}
	verifyPlace := step("verify_place", "verify_placement", "place")
	verifyPlace.Arguments = map[string]any{"objectId": task.Object.ID, "destinationId": task.Destination.ID}

	return taskgraph.TaskPlan{
		ID:       task.TaskID,
		Goal:     "pick and place a grounded tabletop object",
		Domain:   "manipulation",
		Revision: 1,
		Steps: []taskgraph.SkillStep{
			observe, resolve, planGrasp, pick, verifyGrasp, place, verifyPlace,
		},
		Budget:     taskgraph.Budget{MaxSteps: 9, MaxRetries: 3},
		StopPolicy: taskgraph.StopPolicy{StopWhenEnough: true, StopOnSafety: true},
	}
}

func physicalStep(taskID, approvalID string, deadline time.Time, id, skill string, dependencies ...string) taskgraph.SkillStep {
	return taskgraph.SkillStep{
		ID:             id,
		Skill:          skill,
		DependsOn:      dependencies,
		SafetyLevel:    string(skills.SafetyPhysical),
		ApprovalID:     approvalID,
		DeadlineUnixMS: deadline.UnixMilli(),
		LeaseMS:        defaultLeaseMS,
		IdempotencyKey: fmt.Sprintf("%s-%s-1", taskID, id),
	}
}
