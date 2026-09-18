// Package eval scores natural-language requests end to end: request → intent →
// skill graph.
//
// # Why this exists
//
// Every part of the language path — the parser, the orchestration planner, and a
// future fine-tuned replacement for either — is judged here by one question: given
// a request, did the system produce a plan that would actually run, and did it
// refuse the ones that must not run? Nothing in this package looks at model
// quality in the abstract, because "the model seems good" is not a measurement.
//
// # What it is for
//
//   - Comparing planners. The same cases run against the deterministic planner, a
//     hosted model, and a locally served fine-tune. Swapping an endpoint is the
//     whole interface, so a post-training run can be accepted or rejected on
//     numbers rather than on samples somebody read.
//   - Making refusals first-class. A planner that invents a plan for "把红色方块
//     放进冰箱" is wrong even though it produced output, and a planner that
//     refuses everything scores zero on the executable cases. Both failures are
//     counted, so neither can be traded for the other.
//   - Producing the reward signal. The per-case detail is what a training loop
//     needs; a single accuracy number is not.
//
// # What it deliberately does not do
//
// It never runs a robot. A plan that scores perfectly here has been *understood*,
// not *executed*, and the two are different claims. Whether a plan completes is
// decided by the closed-loop evidence gate during execution, and by
// core/harness on the world state afterwards. Keeping this package out of that
// business is what lets it run in a second without a simulation.
package eval

import (
	"fmt"
	"sort"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// Case is one request and what a correct answer must contain.
//
// A case asserts on the *plan*, not on a reference plan's text. Two planners can
// produce different graphs that are both correct — a `fetch` may or may not include
// an explicit stow step — so equality against one golden string would score
// wording rather than correctness.
type Case struct {
	ID      string `json:"id"`
	Request string `json:"request"`
	// Why records what this case is protecting. A case whose reason is not written
	// down is a case nobody can safely delete.
	Why string `json:"why"`

	// MustRefuse marks a request that must not produce a runnable plan.
	//
	// It is a separate dimension from the expectations below, because "produced
	// nothing" and "produced the right thing" fail in opposite directions and a
	// single score would let one hide the other.
	MustRefuse bool `json:"mustRefuse"`

	// WantAction is the intent action the request must resolve to, when it is
	// executable. Empty means "any action", which is what a navigation-only case
	// wants to leave open.
	WantAction string `json:"wantAction,omitempty"`
	// WantObjectCategory and WantColor pin the object, when the request names one.
	WantObjectCategory string `json:"wantObjectCategory,omitempty"`
	WantColor          string `json:"wantColor,omitempty"`
	// WantDestinationCategory and WantRelation pin where it goes.
	WantDestinationCategory string `json:"wantDestinationCategory,omitempty"`
	WantRelation            string `json:"wantRelation,omitempty"`

	// WantSkills must all appear in the plan.
	WantSkills []string `json:"wantSkills,omitempty"`
	// WantOrder must appear in the plan in this relative order. It is separate from
	// WantSkills because a plan can name every right skill in an order that would
	// pick before it looks.
	WantOrder []string `json:"wantOrder,omitempty"`
	// ForbidSkills must not appear. A plan that reaches for a skill this robot has
	// no business calling is wrong however good the rest of it is.
	ForbidSkills []string `json:"forbidSkills,omitempty"`
}

// Outcome is what running one case produced.
type Outcome struct {
	CaseID string `json:"caseId"`
	// Passed is true only when every applicable dimension passed.
	Passed bool `json:"passed"`
	// Refused is true when the request produced no runnable plan.
	Refused bool `json:"refused"`
	// PlanSource names who produced the plan: deterministic, llm, llm_consensus.
	PlanSource string `json:"planSource,omitempty"`
	// Skills is the plan's skill sequence, flattened in dependency order. It is
	// reported even for a failing case, because "what did it actually do" is the
	// first thing anyone asks.
	Skills []string `json:"skills,omitempty"`
	// Failures names each dimension that failed, in words an operator can act on.
	Failures []string `json:"failures,omitempty"`
	// Error is set when the run itself broke (model unreachable, malformed reply),
	// which is a different finding from a wrong plan.
	Error string `json:"error,omitempty"`
}

// Report is one planner's run over the whole case set.
type Report struct {
	// Planner identifies what was scored: "deterministic", or the model name.
	Planner string `json:"planner"`
	// Cases, Passed and Refused are the headline counts.
	Cases   int `json:"cases"`
	Passed  int `json:"passed"`
	Refused int `json:"refused"`
	// ExecutableCases and ExecutablePassed are reported separately from the totals
	// so that a planner which refuses everything cannot look good by scoring well
	// on the refusal half.
	ExecutableCases  int `json:"executableCases"`
	ExecutablePassed int `json:"executablePassed"`
	// Failures counts how often each dimension failed, so a systematic weakness is
	// visible without reading every row.
	Failures map[string]int `json:"failures,omitempty"`
	Outcomes []Outcome      `json:"outcomes"`
}

// Accuracy is the fraction of all cases that passed.
func (r Report) Accuracy() float64 {
	if r.Cases == 0 {
		return 0
	}
	return float64(r.Passed) / float64(r.Cases)
}

// Summary renders the report for a person.
//
// It prints the two halves separately on purpose. A single accuracy figure hides
// the difference between a planner that cannot understand requests and one that
// invents plans for requests it should refuse, and those need opposite fixes.
func (r Report) Summary() string {
	var builder strings.Builder
	fmt.Fprintf(&builder, "planner=%s  %d/%d cases (%.0f%%)\n",
		r.Planner, r.Passed, r.Cases, r.Accuracy()*100)
	fmt.Fprintf(&builder, "  executable: %d/%d\n", r.ExecutablePassed, r.ExecutableCases)
	fmt.Fprintf(&builder, "  refusals:   %d/%d\n", r.Passed-(r.ExecutablePassed),
		r.Cases-r.ExecutableCases)
	if len(r.Failures) > 0 {
		kinds := make([]string, 0, len(r.Failures))
		for kind := range r.Failures {
			kinds = append(kinds, kind)
		}
		sort.Slice(kinds, func(i, j int) bool {
			if r.Failures[kinds[i]] != r.Failures[kinds[j]] {
				return r.Failures[kinds[i]] > r.Failures[kinds[j]]
			}
			return kinds[i] < kinds[j]
		})
		builder.WriteString("  failure kinds: ")
		for index, kind := range kinds {
			if index > 0 {
				builder.WriteString(", ")
			}
			fmt.Fprintf(&builder, "%s×%d", kind, r.Failures[kind])
		}
		builder.WriteString("\n")
	}
	for _, outcome := range r.Outcomes {
		if outcome.Passed {
			continue
		}
		fmt.Fprintf(&builder, "  ✗ %s", outcome.CaseID)
		if outcome.Error != "" {
			fmt.Fprintf(&builder, " (run failed: %s)", outcome.Error)
		}
		builder.WriteString("\n")
		for _, failure := range outcome.Failures {
			fmt.Fprintf(&builder, "      %s\n", failure)
		}
	}
	return builder.String()
}

// Answer is what a planner under test produces for one request.
//
// It is a struct rather than a pair of returns so that a system which parses but
// cannot plan, or plans without parsing, can say which half it got to.
type Answer struct {
	Intent manipulation.Intent
	Bundle *Bundle
	// Refused is set when the system declined the request: a clarification, an
	// unsupported action, or a rejection by the planner.
	Refused bool
	// RefusalReason is why, in the system's own words.
	RefusalReason string
}

// Bundle is the planner's output, aliased so the scoring code below reads without
// spelling out the package at every use.
type Bundle = orchestration.Bundle

// System answers one request. Both a real stack and a test double implement it.
type System interface {
	// Name identifies this system in the report.
	Name() string
	// Answer runs the request. A returned error means the run itself broke; a
	// refusal is not an error and must be reported through Answer.Refused.
	Answer(request string) (Answer, error)
}

// Scoring dimensions, named so a failure count can be grouped by them.
const (
	FailureUnexpectedPlan = "unexpected-plan"
	FailureMissingRefusal = "should-have-refused"
	FailureWrongAction    = "wrong-action"
	FailureWrongObject    = "wrong-object"
	FailureWrongColor     = "wrong-color"
	FailureWrongTarget    = "wrong-target"
	FailureMissingSkill   = "missing-skill"
	FailureWrongOrder     = "wrong-order"
	FailureForbiddenSkill = "forbidden-skill"
)

// Run scores one system over the case set.
func Run(system System, cases []Case) Report {
	report := Report{
		Planner: system.Name(), Cases: len(cases),
		Failures: map[string]int{}, Outcomes: make([]Outcome, 0, len(cases)),
	}
	for _, testCase := range cases {
		outcome := scoreOne(system, testCase)
		if outcome.Passed {
			report.Passed++
		}
		if !testCase.MustRefuse {
			report.ExecutableCases++
			if outcome.Passed {
				report.ExecutablePassed++
			}
		}
		if outcome.Refused {
			report.Refused++
		}
		for _, failure := range outcome.Failures {
			report.Failures[failureKind(failure)]++
		}
		report.Outcomes = append(report.Outcomes, outcome)
	}
	return report
}

func scoreOne(system System, testCase Case) Outcome {
	outcome := Outcome{CaseID: testCase.ID}
	answer, err := system.Answer(testCase.Request)
	if err != nil {
		// A broken run is not a wrong answer, and conflating them would let an
		// unreachable model look like a model with bad judgement.
		outcome.Error = err.Error()
		outcome.Failures = append(outcome.Failures, "run failed: "+err.Error())
		return outcome
	}
	outcome.Refused = answer.Refused
	outcome.Skills = skillsOf(answer.Bundle)
	if answer.Bundle != nil {
		outcome.PlanSource = answer.Bundle.Source
	}

	if testCase.MustRefuse {
		if !answer.Refused && len(outcome.Skills) > 0 {
			outcome.Failures = append(outcome.Failures, fmt.Sprintf(
				"%s: 这个请求必须被拒绝，但产出了 %d 个步骤（%s）",
				FailureMissingRefusal, len(outcome.Skills), strings.Join(outcome.Skills, "→")))
			return outcome
		}
		outcome.Passed = true
		return outcome
	}

	if answer.Refused {
		outcome.Failures = append(outcome.Failures, fmt.Sprintf(
			"%s: 这个请求本应可以规划，却被拒绝：%s", FailureUnexpectedPlan, answer.RefusalReason))
		return outcome
	}

	if testCase.WantAction != "" && answer.Intent.Action != testCase.WantAction {
		outcome.Failures = append(outcome.Failures, fmt.Sprintf(
			"%s: action = %q, want %q", FailureWrongAction, answer.Intent.Action, testCase.WantAction))
	}
	if testCase.WantObjectCategory != "" && answer.Intent.Object.Category != testCase.WantObjectCategory {
		outcome.Failures = append(outcome.Failures, fmt.Sprintf(
			"%s: object = %q, want %q",
			FailureWrongObject, answer.Intent.Object.Category, testCase.WantObjectCategory))
	}
	if testCase.WantColor != "" && answer.Intent.Object.Attributes["color"] != testCase.WantColor {
		outcome.Failures = append(outcome.Failures, fmt.Sprintf(
			"%s: color = %q, want %q",
			FailureWrongColor, answer.Intent.Object.Attributes["color"], testCase.WantColor))
	}
	if testCase.WantDestinationCategory != "" && answer.Intent.Destination.Category != testCase.WantDestinationCategory {
		outcome.Failures = append(outcome.Failures, fmt.Sprintf(
			"%s: destination = %q, want %q",
			FailureWrongTarget, answer.Intent.Destination.Category, testCase.WantDestinationCategory))
	}
	if testCase.WantRelation != "" && answer.Intent.Destination.Relation != testCase.WantRelation {
		outcome.Failures = append(outcome.Failures, fmt.Sprintf(
			"%s: relation = %q, want %q",
			FailureWrongTarget, answer.Intent.Destination.Relation, testCase.WantRelation))
	}

	have := map[string]bool{}
	for _, skill := range outcome.Skills {
		have[skill] = true
	}
	for _, want := range testCase.WantSkills {
		if !have[want] {
			outcome.Failures = append(outcome.Failures, fmt.Sprintf(
				"%s: 计划里没有 %s（实际：%s）",
				FailureMissingSkill, want, strings.Join(outcome.Skills, "→")))
		}
	}
	for _, forbidden := range testCase.ForbidSkills {
		if have[forbidden] {
			outcome.Failures = append(outcome.Failures, fmt.Sprintf(
				"%s: 计划里出现了不该有的 %s", FailureForbiddenSkill, forbidden))
		}
	}
	if missing := orderViolation(testCase.WantOrder, outcome.Skills); missing != "" {
		outcome.Failures = append(outcome.Failures, fmt.Sprintf(
			"%s: %s（实际：%s）",
			FailureWrongOrder, missing, strings.Join(outcome.Skills, "→")))
	}

	outcome.Passed = len(outcome.Failures) == 0
	return outcome
}

// orderViolation reports the first required ordering that the plan does not
// respect, or "" when every pair is in order.
//
// "In order" is relative position, not adjacency: a plan may legitimately do more
// between two required steps, and demanding adjacency would fail a better plan for
// being more thorough.
func orderViolation(want []string, have []string) string {
	position := map[string]int{}
	for index, skill := range have {
		if _, seen := position[skill]; !seen {
			position[skill] = index
		}
	}
	previous, previousSkill := -1, ""
	for _, skill := range want {
		index, present := position[skill]
		if !present {
			// Absence is reported by the missing-skill dimension; repeating it here
			// would double-count one problem as two.
			continue
		}
		if index < previous {
			return fmt.Sprintf("%s 必须在 %s 之后，计划里反了", skill, previousSkill)
		}
		previous, previousSkill = index, skill
	}
	return ""
}

// skillsOf flattens a bundle's plans into one skill sequence in dependency order.
func skillsOf(bundle *Bundle) []string {
	if bundle == nil {
		return nil
	}
	skills := make([]string, 0)
	for _, plan := range bundle.Plans {
		for _, step := range orderedSteps(plan) {
			skills = append(skills, step.Skill)
		}
	}
	return skills
}

// orderedSteps returns a plan's steps so that a step never precedes something it
// depends on.
//
// The plans carry an explicit order already; this is a defence for the case where
// that order is wrong, because a scorer that read steps in slice order would then
// report an ordering failure the executed plan would not have had.
func orderedSteps(plan taskgraph.TaskPlan) []taskgraph.SkillStep {
	steps := plan.Steps
	byID := make(map[string]taskgraph.SkillStep, len(steps))
	for _, step := range steps {
		byID[step.ID] = step
	}
	ordered := make([]taskgraph.SkillStep, 0, len(steps))
	emitted := map[string]bool{}
	var visit func(step taskgraph.SkillStep)
	visit = func(step taskgraph.SkillStep) {
		if emitted[step.ID] {
			return
		}
		emitted[step.ID] = true
		for _, dependency := range step.DependsOn {
			if parent, ok := byID[dependency]; ok {
				visit(parent)
			}
		}
		ordered = append(ordered, step)
	}
	for _, step := range steps {
		visit(step)
	}
	return ordered
}

// failureKind strips a failure message down to its dimension, so counts group.
func failureKind(failure string) string {
	if index := strings.Index(failure, ":"); index > 0 {
		return failure[:index]
	}
	return "other"
}
