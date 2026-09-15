package manipulation

import (
	"fmt"
	"math"
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
	// physical marks a tool with hardware effect. mutatesWorld additionally
	// declares that the resulting world state must be confirmed from a fresh
	// post-command observation before the step may be recorded as done, so the
	// Agent's closure gate applies to it. Emergency stop is physical but is not
	// a world mutation in that sense: its effect is the latched stop state,
	// which the runtime reports directly rather than through scene perception.
	physical := func(name string, required ...string) skills.SkillManifest {
		return skills.SkillManifest{
			Name:                  name,
			RequiredParameters:    required,
			SideEffect:            true,
			SafetyLevel:           skills.SafetyPhysical,
			DefaultLeaseMS:        physicalLeaseMS(name),
			AllowedSafetyProfiles: []string{"desktop_standard", "simulation"},
			ApprovalPolicy:        skills.ApprovalPolicy{Required: true},
			MutatesWorld:          true,
		}
	}
	emergency := physical("emergency_stop")
	emergency.MutatesWorld = false
	return []skills.SkillManifest{
		readOnly("observe_scene"),
		readOnly("resolve_targets", "objectId", "destinationId"),
		physical("navigation.navigate", "goalPose"),
		// Put the base on certified-clear floor, without changing its heading,
		// before the task starts driving. A survey can leave the robot standing on
		// ground the map never certified (the floor under a standing robot is the
		// one patch a forward-facing camera cannot measure), and the first
		// navigation is then refused with LOCALIZATION_NOT_CLEAR - measured on a
		// real survey map. It also keeps the goal heading reachable inside the
		// driver's bounded turn budget. No parameters: the runtime owns the map.
		physical("navigation.pre_position"),
		readOnly("plan_grasp", "objectId", "destinationId"),
		physical("manipulation.pick", "targetRef"),
		readOnly("verify_grasp", "objectId"),
		readOnly("verify_arrival", "goalPose"),
		physical("manipulation.place", "targetRef"),
		readOnly("verify_placement", "objectId", "destinationId"),
		physical("recover_to_safe_pose"),
		emergency,
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
	if task.Action == ActionHomeRoute {
		// The reposition step runs once, before the first drive, and the first
		// navigation waits for it: a later checkpoint can rely on the base being
		// somewhere the map certified.
		reposition := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix,
			"pre_position", "navigation.pre_position", "observe")
		// Face the first goal before driving. The driver refuses a navigation whose
		// goal heading is more than its own rotation budget (0.5 rad) from the
		// current heading - measured, a household task failed its first navigation
		// with NAV_ROTATION_LIMIT while standing on clear floor. Only the heading is
		// passed: the runtime still chooses where to stand.
		if yaw, ok := goalYaw(task.RouteGoals, 1); ok {
			reposition.Arguments = map[string]any{"alignYaw": yaw}
		}
		steps := []taskgraph.SkillStep{step("observe", "observe_scene"), reposition}
		dependsOn := "pre_position"
		for index := range task.RouteRooms {
			navigateID := fmt.Sprintf("navigate_%02d", index)
			navigate := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix,
				navigateID, "navigation.navigate", dependsOn)
			// The runtime contract intentionally accepts only goalPose. Room and
			// segment identity stay in the immutable step ID/plan metadata so a
			// semantic label can never bypass strict physical-tool validation.
			navigate.Arguments = map[string]any{}
			var goalPose []float64
			if index < len(task.RouteGoals) {
				goalPose = append([]float64(nil), task.RouteGoals[index]...)
			}
			if len(goalPose) > 0 {
				navigate.Arguments["goalPose"] = append([]float64(nil), goalPose...)
			}
			verifyID := fmt.Sprintf("verify_arrival_%02d", index)
			verify := step(verifyID, "verify_arrival", navigateID)
			// Arrival verification receives the same goal pose used by navigation;
			// the runtime then captures a fresh base RGB-D frame and pose.
			verify.Arguments = map[string]any{}
			if len(goalPose) > 0 {
				verify.Arguments["goalPose"] = append([]float64(nil), goalPose...)
			}
			steps = append(steps, navigate, verify)
			dependsOn = verifyID
		}
		return taskgraph.TaskPlan{
			ID: task.TaskID, Goal: "inspect a registered semantic route with RGB-D evidence", Domain: "navigation",
			Revision: 1, Steps: steps, Budget: taskgraph.Budget{MaxSteps: len(steps) + 2, MaxRetries: 3},
			StopPolicy: taskgraph.StopPolicy{StopWhenEnough: true, StopOnSafety: true},
		}
	}
	if task.Action == ActionHomeManipulation {
		reposition := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix,
			"pre_position", "navigation.pre_position", "observe")
		// Face the first goal before driving. The driver refuses a navigation whose
		// goal heading is more than its own rotation budget (0.5 rad) from the
		// current heading - measured, a household task failed its first navigation
		// with NAV_ROTATION_LIMIT while standing on clear floor. Only the heading is
		// passed: the runtime still chooses where to stand.
		if yaw, ok := goalYaw(task.RouteGoals, 1); ok {
			reposition.Arguments = map[string]any{"alignYaw": yaw}
		}
		steps := []taskgraph.SkillStep{step("observe", "observe_scene"), reposition}
		dependsOn := "pre_position"
		manipulationIndex := -1
		if task.ManipulationRouteIndex != nil {
			// The grounder validated this exact canonical room/visit. Reverting
			// to a first matching room would move operations across route stages.
			manipulationIndex = *task.ManipulationRouteIndex
		} else {
			for index, room := range task.RouteRooms {
				if room == task.Object.WorkArea && index > 0 {
					manipulationIndex = index
					break
				}
			}
		}
		// RouteRooms[0] is the commissioned start room. Every subsequent room
		// gets its own navigation and fresh RGB-D arrival checkpoint.
		firstRouteIndex := 1
		if task.ManipulationRouteIndex != nil && manipulationIndex == 0 {
			firstRouteIndex = 0
		}
		for index := firstRouteIndex; index < len(task.RouteRooms); index++ {
			navigateID := fmt.Sprintf("navigate_%02d", index)
			navigate := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix,
				navigateID, "navigation.navigate", dependsOn)
			navigate.Arguments = map[string]any{}
			var goalPose []float64
			if index < len(task.RouteGoals) {
				goalPose = append([]float64(nil), task.RouteGoals[index]...)
			}
			if len(goalPose) == 0 {
				continue
			}
			navigate.Arguments["goalPose"] = append([]float64(nil), goalPose...)
			verifyID := fmt.Sprintf("verify_arrival_%02d", index)
			verify := step(verifyID, "verify_arrival", navigateID)
			verify.Arguments = map[string]any{"goalPose": append([]float64(nil), goalPose...)}
			steps = append(steps, navigate, verify)
			dependsOn = verifyID
			// Insert manipulation after its bound arrival, retaining every
			// unrelated route checkpoint before and after that operation.
			if index == manipulationIndex {
				afterObserve := step("observe_after_navigation", "observe_scene", verifyID)
				steps = append(steps, afterObserve)
				resolve := step("resolve", "resolve_targets", "observe_after_navigation")
				resolve.Arguments = map[string]any{
					"objectId": task.Object.ID, "objectConfidence": task.Object.Confidence,
					"destinationId": task.Destination.ID, "destinationConfidence": task.Destination.Confidence,
				}
				planGrasp := step("plan_grasp", "plan_grasp", "resolve", "observe_after_navigation")
				planGrasp.Arguments = map[string]any{"objectId": task.Object.ID, "destinationId": task.Destination.ID, "keepUpright": task.KeepUpright}
				pick := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix, "pick", "manipulation.pick", "plan_grasp")
				pick.Arguments = map[string]any{"targetRef": task.Object.ID, "keepUpright": task.KeepUpright}
				verifyGrasp := step("verify_grasp", "verify_grasp", "pick")
				verifyGrasp.Arguments = map[string]any{"objectId": task.Object.ID}
				place := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix, "place", "manipulation.place", "verify_grasp")
				place.Arguments = map[string]any{"targetRef": task.Destination.ID, "keepUpright": task.KeepUpright}
				verifyPlace := step("verify_place", "verify_placement", "place")
				verifyPlace.Arguments = map[string]any{"objectId": task.Object.ID, "destinationId": task.Destination.ID}
				steps = append(steps, resolve, planGrasp, pick, verifyGrasp, place, verifyPlace)
				dependsOn = "verify_place"
			}
		}
		return taskgraph.TaskPlan{
			ID: task.TaskID, Goal: "navigate to a household room, transfer an RGB-D grounded object, and verify the return", Domain: "home_task",
			Revision: 1, Steps: steps, Budget: taskgraph.Budget{MaxSteps: len(steps) + 2, MaxRetries: 3},
			StopPolicy: taskgraph.StopPolicy{StopWhenEnough: true, StopOnSafety: true},
		}
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
		// Same repositioning rule as the household route: certify the ground the
		// base is standing on and face the goal before the first drive.
		reposition := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix,
			"pre_position", "navigation.pre_position", "resolve")
		if yaw, ok := goalYaw([][]float64{task.NavigationGoal}, 0); ok {
			reposition.Arguments = map[string]any{"alignYaw": yaw}
		}
		steps = append(steps, reposition)
		navigate := physicalStep(task.TaskID, approvalID, deadline, task.RobotID, prefix, "navigate", "navigation.navigate", "pre_position")
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

// goalYaw reads the heading out of a commanded pose, so the repositioning step
// can face the same way the navigation will demand. The pose is
// [x, y, z, qw, qx, qy, qz]; an absent or malformed goal simply means no
// alignment request, and the runtime then only certifies the ground.
func goalYaw(goals [][]float64, index int) (float64, bool) {
	if index < 0 || index >= len(goals) {
		return 0, false
	}
	pose := goals[index]
	if len(pose) != 7 {
		return 0, false
	}
	norm := pose[3]*pose[3] + pose[6]*pose[6]
	if norm < 1e-9 {
		return 0, false
	}
	return 2 * math.Atan2(pose[6], pose[3]), true
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
