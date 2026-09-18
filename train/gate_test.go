package train_test

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration/eval"
	"github.com/SUSTechWLA/tangying-robot-agent-os/train"
)

// report builds an eval report with the given split.
//
// Executable and refusal outcomes are produced separately because the whole point
// of the gate is that these two counts are not interchangeable.
func report(planner string, executablePassed, executableCases, refusalPassed, refusalCases int) eval.Report {
	result := eval.Report{
		Planner:          planner,
		Cases:            executableCases + refusalCases,
		Passed:           executablePassed + refusalPassed,
		ExecutableCases:  executableCases,
		ExecutablePassed: executablePassed,
	}
	for index := 0; index < executableCases; index++ {
		result.Outcomes = append(result.Outcomes, eval.Outcome{
			CaseID: caseName("exec", index), Passed: index < executablePassed,
		})
	}
	for index := 0; index < refusalCases; index++ {
		result.Outcomes = append(result.Outcomes, eval.Outcome{
			CaseID: caseName("refuse", index), Passed: index < refusalPassed, Refused: index < refusalPassed,
		})
	}
	return result
}

func caseName(kind string, index int) string {
	return kind + "-" + string(rune('a'+index))
}

func hasReason(decision train.Decision, reason string) bool {
	for _, entry := range decision.Reasons {
		if strings.HasPrefix(entry, reason) {
			return true
		}
	}
	return false
}

// --- the exchange rate that does not exist ----------------------------------

// The rule the gate exists for: planning more may not be paid for by refusing
// less. A model that plans everything and refuses nothing is not most of a good
// model — it will execute a negation as an instruction.
func TestPlanningMoreCannotPayForRefusingLess(t *testing.T) {
	decision := train.Compare(train.Report{
		Candidate: "candidate", Incumbent: "incumbent",
		Baseline: report("incumbent", 3, 4, 6, 6),
		Eval:     report("candidate", 4, 4, 5, 6), // better planning, worse refusals
	})
	if decision.Promote {
		t.Fatalf("a refusal regression was paid for with planning gains: %+v", decision)
	}
	if !hasReason(decision, train.ReasonRefusalRegressed) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// And the reverse: refusing more may not be paid for by planning less.
func TestRefusingMoreCannotPayForPlanningLess(t *testing.T) {
	decision := train.Compare(train.Report{
		Candidate: "candidate", Incumbent: "incumbent",
		Baseline: report("incumbent", 4, 4, 5, 6),
		Eval:     report("candidate", 3, 4, 6, 6),
	})
	if decision.Promote {
		t.Fatalf("an executable regression was paid for with refusal gains: %+v", decision)
	}
	if !hasReason(decision, train.ReasonExecutableRegressed) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// --- a Pareto improvement promotes ------------------------------------------

func TestAParetoImprovementIsPromoted(t *testing.T) {
	decision := train.Compare(train.Report{
		Candidate: "candidate", Incumbent: "incumbent",
		Baseline: report("incumbent", 3, 4, 5, 6),
		Eval:     report("candidate", 4, 4, 6, 6),
	})
	if !decision.Promote {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// Equal is not better. A checkpoint that ties the incumbent has no reason to
// replace it, and promoting ties is how a model set slowly drifts.
func TestATieIsNotPromoted(t *testing.T) {
	decision := train.Compare(train.Report{
		Candidate: "candidate", Incumbent: "incumbent",
		Baseline: report("incumbent", 4, 4, 6, 6),
		Eval:     report("candidate", 4, 4, 6, 6),
	})
	if decision.Promote {
		t.Fatal("a tie was promoted")
	}
	if !hasReason(decision, train.ReasonNoImprovement) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// --- the examination cannot be changed by the candidate ---------------------

// The easiest way to "improve" is to shrink the test. Compare refuses to subtract
// two numbers that came from different examinations.
func TestASmallerCaseSetIsRefused(t *testing.T) {
	decision := train.Compare(train.Report{
		Candidate: "candidate", Incumbent: "incumbent",
		Baseline: report("incumbent", 3, 4, 5, 6),
		Eval:     report("candidate", 3, 3, 5, 6), // two cases removed
	})
	if decision.Promote {
		t.Fatal("a comparison across different case sets was allowed")
	}
	if !hasReason(decision, train.ReasonDifferentCases) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// Same count, different cases, is also a different examination.
func TestASwappedCaseSetIsRefused(t *testing.T) {
	baseline := report("incumbent", 3, 4, 5, 6)
	candidate := report("candidate", 4, 4, 6, 6)
	candidate.Outcomes[0].CaseID = "a-case-nobody-has-seen"
	decision := train.Compare(train.Report{
		Candidate: "candidate", Incumbent: "incumbent",
		Baseline: baseline, Eval: candidate,
	})
	if decision.Promote {
		t.Fatal("a swapped case was allowed through on a matching count")
	}
	if !hasReason(decision, train.ReasonDifferentCases) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// --- an unmeasured checkpoint is neither promoted nor discarded -------------

// A run that did not complete has no score. Promoting it adopts a model nobody
// measured; scoring it zero discards a checkpoint over a dropped connection.
func TestAnIncompleteRunIsRefusedAsIncomplete(t *testing.T) {
	candidate := report("candidate", 4, 4, 6, 6)
	candidate.Outcomes[2].Error = "connection refused"
	decision := train.Compare(train.Report{
		Candidate: "candidate", Incumbent: "incumbent",
		Baseline: report("incumbent", 3, 4, 5, 6), Eval: candidate,
	})
	if decision.Promote {
		t.Fatalf("an unmeasured checkpoint was promoted: %+v", decision)
	}
	if !hasReason(decision, train.ReasonRunFailed) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// --- the first checkpoint is judged absolutely, not relatively --------------

func TestTheFirstCheckpointIsNotComparedToNothing(t *testing.T) {
	decision := train.Compare(train.Report{
		Candidate: "first", Eval: report("first", 4, 4, 6, 6),
	})
	if decision.Promote {
		t.Fatal("a checkpoint with no incumbent was promoted on a comparison with nothing")
	}
	if !hasReason(decision, train.ReasonNoImprovement) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

func TestTheAbsoluteFloorAdmitsACompetentFirstCheckpoint(t *testing.T) {
	gate := train.Gate{MinExecutable: 3, MinRefusals: 5}
	if decision := gate.Admit(report("first", 4, 4, 6, 6)); !decision.Promote {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// The floor has two independent halves for the same reason Compare does.
func TestTheAbsoluteFloorRefusesARefusalRegression(t *testing.T) {
	gate := train.Gate{MinExecutable: 3, MinRefusals: 5}
	decision := gate.Admit(report("first", 4, 4, 4, 6))
	if decision.Promote {
		t.Fatal("a checkpoint that refuses too little cleared the floor")
	}
	if !hasReason(decision, train.ReasonRefusalRegressed) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

func TestTheAbsoluteFloorRefusesAPlanningRegression(t *testing.T) {
	gate := train.Gate{MinExecutable: 3, MinRefusals: 5}
	decision := gate.Admit(report("first", 1, 4, 6, 6))
	if decision.Promote {
		t.Fatal("a checkpoint that plans almost nothing cleared the floor")
	}
	if !hasReason(decision, train.ReasonExecutableRegressed) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// A first checkpoint that could not be measured is refused, not assumed good.
func TestTheAbsoluteFloorRefusesAnUnmeasuredCheckpoint(t *testing.T) {
	unmeasured := report("first", 4, 4, 6, 6)
	unmeasured.Outcomes[0].Error = "endpoint unreachable"
	decision := train.Gate{MinExecutable: 3, MinRefusals: 5}.Admit(unmeasured)
	if decision.Promote {
		t.Fatalf("an unmeasured checkpoint cleared the floor: %+v", decision)
	}
	if !hasReason(decision, train.ReasonRunFailed) {
		t.Fatalf("reasons = %v", decision.Reasons)
	}
}

// --- the scorer this gate reads is the one that was already built -----------

// The gate compares eval reports, so the two must agree on what the halves mean.
// This asserts the coupling is real rather than assumed: a report where the
// executable half is counted differently would silently change the gate's answer.
func TestTheGateReadsTheSameHalvesTheEvaluationPrints(t *testing.T) {
	built := eval.Report{
		Planner: "x", Cases: 3, Passed: 2,
		ExecutableCases: 1, ExecutablePassed: 1,
		Outcomes: []eval.Outcome{
			{CaseID: "e", Passed: true}, {CaseID: "r1", Passed: true}, {CaseID: "r2"},
		},
	}
	// 2 passed, 1 of them executable, so 1 refusal passed out of 2 refusal cases.
	if built.Cases-built.ExecutableCases != 2 {
		t.Fatalf("refusal cases = %d", built.Cases-built.ExecutableCases)
	}
	decision := train.Compare(train.Report{
		Candidate: "c", Incumbent: "i",
		Baseline: built,
		Eval: eval.Report{
			Planner: "c", Cases: 3, Passed: 3,
			ExecutableCases: 1, ExecutablePassed: 1,
			Outcomes: []eval.Outcome{
				{CaseID: "e", Passed: true}, {CaseID: "r1", Passed: true}, {CaseID: "r2", Passed: true},
			},
		},
	})
	if !decision.Promote {
		t.Fatalf("a refusal improvement was not promoted: %v", decision.Reasons)
	}
}
