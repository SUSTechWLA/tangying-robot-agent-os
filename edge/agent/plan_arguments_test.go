package agent

import (
	"math"
	"reflect"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// A planner names rooms; a physical tool accepts only poses.
//
// Both rules are deliberate. A planner writes "go to the kitchen" the way a person
// would, and manipulation/plugin.go states why the runtime contract takes only
// `goalPose`: a semantic label must never be able to address the robot's motion.
// The name therefore has to be resolved in between, against the poses the
// grounding actually certified.
//
// This is the join that was missing. An LLM plan for the household task died on
// its first step — `goalPose: "kitchen"` failing strict validation — while the
// grounding held the pose for that very room.

// A route goal is a 7-element pose: position plus a quaternion, which is what the
// runtime contract takes and what goalYaw reads its heading out of. The fixture
// uses the real shape, because a fixture that is easier than reality tests the
// fixture.
func routeGoal(x, y, yaw float64) []float64 {
	half := yaw / 2
	return []float64{x, y, 0, math.Cos(half), 0, 0, math.Sin(half)}
}

func groundedRoute() manipulation.GroundedTask {
	return manipulation.GroundedTask{
		RouteRooms: []string{"living_room", "kitchen", "living_room"},
		RouteGoals: [][]float64{
			routeGoal(0, -1.25, 0),
			routeGoal(2.05, 3, math.Pi/2),
			routeGoal(0, -1.25, 0),
		},
		Object:      manipulation.SceneRef{ID: "cup-1"},
		Destination: manipulation.SceneRef{ID: "tray-1"},
	}
}

func TestARoomInAPoseArgumentBecomesTheGroundedPose(t *testing.T) {
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "navigate_to_kitchen", Skill: "navigation.navigate", Arguments: map[string]any{"goalPose": "kitchen"}},
			{ID: "verify_arrival_kitchen", Skill: "verify_arrival", Arguments: map[string]any{"goalPose": "kitchen"}},
		},
	}
	plan, err := materializePlanTemplate(template, "task-1", groundedRoute(), time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	for _, step := range plan.Steps {
		got, ok := step.Arguments["goalPose"].([]float64)
		if !ok {
			t.Fatalf("%s: goalPose = %#v, want a pose; a room name reached a physical tool", step.ID, step.Arguments["goalPose"])
		}
		if !reflect.DeepEqual(got, routeGoal(2.05, 3, math.Pi/2)) {
			t.Fatalf("%s: goalPose = %v, want the kitchen pose from the route", step.ID, got)
		}
	}
}

// A room the grounding never certified is left as the name it is. Guessing a pose
// for it would drive the robot at a coordinate nobody certified, and the tool's
// own strict validation is the right answer for a plan that asks for that.
func TestARoomOutsideTheRouteIsNotInvented(t *testing.T) {
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "navigate_to_attic", Skill: "navigation.navigate", Arguments: map[string]any{"goalPose": "attic"}},
		},
	}
	plan, err := materializePlanTemplate(template, "task-1", groundedRoute(), time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if got := plan.Steps[0].Arguments["goalPose"]; got != "attic" {
		t.Fatalf("goalPose = %#v, want the name left alone for the tool to refuse", got)
	}
}

// The pose is handed out as a copy: a step that mutated its arguments would
// otherwise edit the grounding every later step reads.
func TestTheResolvedPoseIsACopy(t *testing.T) {
	grounded := groundedRoute()
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "navigate_to_kitchen", Skill: "navigation.navigate", Arguments: map[string]any{"goalPose": "kitchen"}},
		},
	}
	plan, err := materializePlanTemplate(template, "task-1", grounded, time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	plan.Steps[0].Arguments["goalPose"].([]float64)[0] = 99
	if grounded.RouteGoals[1][0] == 99 {
		t.Fatal("the step's pose aliases the grounded route")
	}
}

// Only a pose argument is resolved. A room name in some other argument is data
// the step asked for, and rewriting it would be the same mistake in reverse.
func TestOnlyPoseArgumentsAreResolved(t *testing.T) {
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "observe", Skill: "observe_scene", Arguments: map[string]any{"room": "kitchen"}},
		},
	}
	plan, err := materializePlanTemplate(template, "task-1", groundedRoute(), time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if got := plan.Steps[0].Arguments["room"]; got != "kitchen" {
		t.Fatalf("room = %#v, want the name untouched", got)
	}
}

// The reference resolution that was already there must keep working.
func TestObjectAndDestinationReferencesStillResolve(t *testing.T) {
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "pick_cup", Skill: "manipulation.pick", Arguments: map[string]any{"targetRef": "@object"}},
			{ID: "place", Skill: "manipulation.place", Arguments: map[string]any{"targetRef": "@destination"}},
		},
	}
	plan, err := materializePlanTemplate(template, "task-1", groundedRoute(), time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if plan.Steps[0].Arguments["targetRef"] != "cup-1" || plan.Steps[1].Arguments["targetRef"] != "tray-1" {
		t.Fatalf("references = %#v / %#v", plan.Steps[0].Arguments, plan.Steps[1].Arguments)
	}
}

// A mobile plan must open on certified-clear floor, whoever wrote it.
//
// The deterministic household plan has always started with pre-positioning. An
// LLM plan for the same task did not, and nothing noticed: it read correctly, its
// skills were right, and it died on the first drive with LOCALIZATION_NOT_CLEAR
// because the base was standing on ground the finished map never certified — the
// one patch a forward-facing camera cannot measure.

func mobileGrounded() manipulation.GroundedTask {
	grounded := groundedRoute()
	grounded.Action = manipulation.ActionHomeManipulation
	grounded.Object.WorkArea = "kitchen"
	grounded.RobotID = "robot-1"
	return grounded
}

func TestAMobilePlanGetsThePreambleItOmitted(t *testing.T) {
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "navigate_to_kitchen", Skill: "navigation.navigate", Arguments: map[string]any{"goalPose": "kitchen"}},
			{ID: "verify_arrival_kitchen", Skill: "verify_arrival", DependsOn: []string{"navigate_to_kitchen"}},
		},
	}
	plan, err := materializePlanTemplate(template, "task-1", mobileGrounded(), time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Steps) != 4 {
		t.Fatalf("steps = %d, want the two written plus the preamble", len(plan.Steps))
	}
	if plan.Steps[0].Skill != "observe_scene" || plan.Steps[1].Skill != "navigation.pre_position" {
		t.Fatalf("preamble = %s / %s", plan.Steps[0].Skill, plan.Steps[1].Skill)
	}
	// The plan's own entry point now waits: the whole point of inserting the
	// preamble is that nothing drives before it.
	if got := plan.Steps[2].DependsOn; len(got) != 1 || got[0] != plan.Steps[1].ID {
		t.Fatalf("the first planned step does not wait for the preamble: %v", got)
	}
	// The step that already had a dependency keeps it.
	if got := plan.Steps[3].DependsOn; len(got) != 1 || got[0] != plan.Steps[2].ID {
		t.Fatalf("an existing dependency was rewritten: %v", got)
	}
	// Pre-positioning faces the first goal, for the reason the deterministic path
	// records: the driver refuses a heading beyond its own rotation budget.
	if _, ok := plan.Steps[1].Arguments["alignYaw"]; !ok {
		t.Fatalf("no alignYaw was passed: %#v", plan.Steps[1].Arguments)
	}
}

func TestAMobilePlanThatAlreadyPrePositionsIsLeftAlone(t *testing.T) {
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "observe", Skill: "observe_scene"},
			{ID: "pre_position", Skill: "navigation.pre_position", DependsOn: []string{"observe"}},
			{ID: "navigate_to_kitchen", Skill: "navigation.navigate", DependsOn: []string{"pre_position"}},
		},
	}
	plan, err := materializePlanTemplate(template, "task-1", mobileGrounded(), time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Steps) != 3 {
		t.Fatalf("steps = %d; a plan that already pre-positions was given a second one", len(plan.Steps))
	}
}

// A tabletop pick does not drive, so it must not be given a driving preamble.
func TestAStationaryPlanIsNotGivenAMobilePreamble(t *testing.T) {
	grounded := groundedRoute()
	grounded.Action = manipulation.ActionPickAndPlace
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "pick_cup", Skill: "manipulation.pick", Arguments: map[string]any{"targetRef": "@object"}},
		},
	}
	plan, err := materializePlanTemplate(template, "task-1", grounded, time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Steps) != 1 || plan.Steps[0].Skill != "manipulation.pick" {
		t.Fatalf("a stationary plan was given a driving preamble: %#v", plan.Steps)
	}
}

// The injector writes into a planner's namespace, so a colliding id has to be
// impossible by construction rather than unlikely.
//
// It was not: a household plan with a step literally called "observe" collided
// with the preamble's own, and the whole subtask was refused with "duplicate step
// id: observe" before anything ran.
func TestAPreambleNeverCollidesWithAPlannedStepID(t *testing.T) {
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{
			{ID: "observe", Skill: "observe_scene"},
			{ID: "pre_position", Skill: "navigation.pre_position", DependsOn: []string{"observe"}},
			{ID: "pick_cup", Skill: "manipulation.pick", DependsOn: []string{"pre_position"}},
		},
	}
	// A plan that already pre-positions is left alone, so this one is not a
	// collision case; drop the pre-position step to make it one.
	template.Steps = template.Steps[:1]
	plan, err := materializePlanTemplate(template, "task-1", mobileGrounded(), time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for _, step := range plan.Steps {
		if seen[step.ID] {
			t.Fatalf("duplicate step id %q in %v", step.ID, stepIDs(plan.Steps))
		}
		seen[step.ID] = true
	}
	for _, step := range plan.Steps {
		for _, dependency := range step.DependsOn {
			if !seen[dependency] {
				t.Fatalf("step %q depends on %q, which is not in the plan", step.ID, dependency)
			}
		}
	}
	// The plan's own observe step keeps its identity; the preamble's is the one
	// that moved.
	if !seen["observe"] || !seen["pre_position"] {
		t.Fatalf("a planned step was renamed: %v", stepIDs(plan.Steps))
	}
}

func stepIDs(steps []taskgraph.SkillStep) []string {
	ids := make([]string, 0, len(steps))
	for _, step := range steps {
		ids = append(ids, step.ID)
	}
	return ids
}
