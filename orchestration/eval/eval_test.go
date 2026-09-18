package eval_test

import (
	"errors"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration/eval"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// scripted answers from a table, so the scorer can be tested without a model.
type scripted struct {
	label   string
	answers map[string]eval.Answer
	err     error
}

func (s scripted) Name() string { return s.label }

func (s scripted) Answer(request string) (eval.Answer, error) {
	if s.err != nil {
		return eval.Answer{}, s.err
	}
	answer, ok := s.answers[request]
	if !ok {
		return eval.Answer{Refused: true, RefusalReason: "not scripted"}, nil
	}
	return answer, nil
}

func bundleWith(skills ...string) *eval.Bundle {
	steps := make([]taskgraph.SkillStep, 0, len(skills))
	for index, skill := range skills {
		step := taskgraph.SkillStep{ID: skill, Skill: skill}
		// Chain them so the plan's own order matches the argument order; a scorer
		// that ignored dependencies would still read these in slice order.
		if index > 0 {
			step.DependsOn = []string{skills[index-1]}
		}
		steps = append(steps, step)
	}
	return &eval.Bundle{
		Source: orchestration.SourceLLM,
		Plans:  []taskgraph.TaskPlan{{ID: "plan", Steps: steps}},
	}
}

func oneCase(id string) []eval.Case {
	for _, testCase := range eval.DefaultCases {
		if testCase.ID == id {
			return []eval.Case{testCase}
		}
	}
	panic("no such case: " + id)
}

// --- the scorer rewards the right answer and rejects the wrong one -----------

func TestAPlanWithTheRightSkillsPasses(t *testing.T) {
	system := scripted{label: "good", answers: map[string]eval.Answer{
		"把杯子拿给我": {
			Intent: manipulation.Intent{Action: manipulation.ActionFetch,
				Object: manipulation.EntitySelector{Category: "cup"}},
			Bundle: bundleWith("observe_scene", "manipulation.pick", "manipulation.place"),
		},
	}}
	report := eval.Run(system, oneCase("fetch-cup"))
	if !report.Outcomes[0].Passed {
		t.Fatalf("failures = %v", report.Outcomes[0].Failures)
	}
	if report.ExecutablePassed != 1 {
		t.Fatalf("report = %+v", report)
	}
}

func TestAPlanInTheWrongOrderFails(t *testing.T) {
	// Placing before picking is the mistake that looks like a complete plan.
	system := scripted{label: "reversed", answers: map[string]eval.Answer{
		"把杯子拿给我": {
			Intent: manipulation.Intent{Action: manipulation.ActionFetch,
				Object: manipulation.EntitySelector{Category: "cup"}},
			Bundle: bundleWith("manipulation.place", "manipulation.pick"),
		},
	}}
	report := eval.Run(system, oneCase("fetch-cup"))
	if report.Outcomes[0].Passed {
		t.Fatal("a plan that places before it picks was scored as correct")
	}
	if report.Failures[eval.FailureWrongOrder] != 1 {
		t.Fatalf("failures = %+v", report.Failures)
	}
}

func TestAMissingSkillFails(t *testing.T) {
	system := scripted{label: "thin", answers: map[string]eval.Answer{
		"把杯子拿给我": {
			Intent: manipulation.Intent{Action: manipulation.ActionFetch,
				Object: manipulation.EntitySelector{Category: "cup"}},
			Bundle: bundleWith("manipulation.pick"),
		},
	}}
	report := eval.Run(system, oneCase("fetch-cup"))
	if report.Failures[eval.FailureMissingSkill] != 1 {
		t.Fatalf("failures = %+v", report.Failures)
	}
}

func TestADroppedColourFails(t *testing.T) {
	// "put the cup in the right bin" when the user said *red* cup is a wrong plan
	// even though every skill is present and in order.
	system := scripted{label: "colourblind", answers: map[string]eval.Answer{
		"把红色杯子放进右边储物箱": {
			Intent: manipulation.Intent{
				Action: manipulation.ActionPickAndPlace,
				Object: manipulation.EntitySelector{Category: "cup", Attributes: map[string]string{}},
				Destination: manipulation.EntitySelector{
					Category: "storage_bin", Relation: "right_side"},
			},
			Bundle: bundleWith("manipulation.pick", "manipulation.place"),
		},
	}}
	report := eval.Run(system, oneCase("place-red-cup-right-bin"))
	if report.Outcomes[0].Passed {
		t.Fatal("a plan that dropped the colour was scored as correct")
	}
	if report.Failures[eval.FailureWrongColor] != 1 {
		t.Fatalf("failures = %+v", report.Failures)
	}
}

// --- refusals are their own dimension ---------------------------------------

func TestPlanningSomethingThatMustBeRefusedFails(t *testing.T) {
	// The dangerous direction: a negation turned into an action.
	system := scripted{label: "obedient", answers: map[string]eval.Answer{
		"把红色方块放到交接区是不允许的": {
			Intent: manipulation.Intent{Action: manipulation.ActionPickAndPlace},
			Bundle: bundleWith("manipulation.pick", "manipulation.place"),
		},
	}}
	report := eval.Run(system, oneCase("negation"))
	if report.Outcomes[0].Passed {
		t.Fatal("a forbidden action was planned and scored as correct")
	}
	if report.Failures[eval.FailureMissingRefusal] != 1 {
		t.Fatalf("failures = %+v", report.Failures)
	}
}

func TestRefusingWhatShouldBePlannedFails(t *testing.T) {
	// The other direction. A system that refuses everything must not score well.
	system := scripted{label: "timid", answers: map[string]eval.Answer{}}
	report := eval.Run(system, eval.DefaultCases)
	if report.Outcomes[0].Passed {
		t.Fatal("a refusal of an executable request was scored as correct")
	}
	if report.ExecutablePassed != 0 {
		t.Fatalf("executable passed = %d", report.ExecutablePassed)
	}
	// It does score on the refusal half, which is exactly why the report prints the
	// two halves separately rather than one accuracy number.
	if report.Passed == 0 {
		t.Fatal("refusing everything scored zero even on the refusal cases")
	}
}

// --- a broken run is not a wrong answer -------------------------------------

func TestAModelThatCannotBeReachedIsReportedAsARunFailure(t *testing.T) {
	system := scripted{label: "unreachable", err: errors.New("connection refused")}
	report := eval.Run(system, oneCase("fetch-cup"))
	outcome := report.Outcomes[0]
	if outcome.Passed {
		t.Fatal("an unreachable model scored as correct")
	}
	if outcome.Error == "" {
		t.Fatal("the transport failure was not reported as such")
	}
	if outcome.Refused {
		t.Fatal("an unreachable model was recorded as a refusal, which is a correct answer")
	}
}

// --- the case set guards the boundaries it claims to ------------------------

// The refusal cases must stay refusals and the executable cases must stay
// executable, or the score stops measuring what its two halves claim.
func TestTheCaseSetKeepsBothDirections(t *testing.T) {
	executable, refusals := 0, 0
	for _, testCase := range eval.DefaultCases {
		if testCase.Why == "" {
			t.Fatalf("case %s has no recorded reason; nobody can safely delete it", testCase.ID)
		}
		if testCase.MustRefuse {
			refusals++
			continue
		}
		executable++
		if len(testCase.WantSkills) == 0 {
			t.Fatalf("executable case %s asserts no skills, so it cannot fail", testCase.ID)
		}
	}
	if executable == 0 || refusals == 0 {
		t.Fatalf("case set is one-sided: %d executable, %d refusals", executable, refusals)
	}
}

// The report must name the planner, or two runs cannot be compared.
func TestTheReportNamesWhatWasScored(t *testing.T) {
	report := eval.Run(scripted{label: "candidate-7b", answers: map[string]eval.Answer{}}, oneCase("fetch-cup"))
	if report.Planner != "candidate-7b" {
		t.Fatalf("planner = %q", report.Planner)
	}
	if !strings.Contains(report.Summary(), "candidate-7b") {
		t.Fatal("the summary does not name the planner")
	}
}

// A plan that lists steps out of slice order but respects its own dependencies
// must be read in dependency order, or the scorer reports a fault the executed
// plan would not have had.
func TestStepsAreReadInDependencyOrderNotSliceOrder(t *testing.T) {
	bundle := &eval.Bundle{
		Source: orchestration.SourceLLM,
		Plans: []taskgraph.TaskPlan{{ID: "plan", Steps: []taskgraph.SkillStep{
			{ID: "place", Skill: "manipulation.place", DependsOn: []string{"pick"}},
			{ID: "pick", Skill: "manipulation.pick"},
		}}},
	}
	system := scripted{label: "unordered", answers: map[string]eval.Answer{
		"把杯子拿给我": {
			Intent: manipulation.Intent{Action: manipulation.ActionFetch,
				Object: manipulation.EntitySelector{Category: "cup"}},
			Bundle: bundle,
		},
	}}
	report := eval.Run(system, oneCase("fetch-cup"))
	if !report.Outcomes[0].Passed {
		t.Fatalf("failures = %v (skills = %v)",
			report.Outcomes[0].Failures, report.Outcomes[0].Skills)
	}
}
