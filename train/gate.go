// Package train is the promotion gate for trained checkpoints.
//
// # Why this is not an agent
//
// "Everything is an agent" holds for work whose shape is *observe, decide, act,
// verify — in seconds, against a world that judges the result*. Training does not
// have that shape, in two ways that matter:
//
//   - **Time.** A recovery action completes in seconds and a lease makes sense. A
//     training run completes in hours or days, and the machinery this repository
//     built for short physical actions — leases, fencing tokens, freshness windows
//     — has nothing to say about it.
//
//   - **The judge is not independent.** A robot's closed loop works because the
//     world is the judge and the world does not care what the policy wanted: either
//     the cup moved or it did not. A training run is judged by a metric, and the
//     run is optimised against that metric. The thing being measured and the thing
//     doing the measuring are the same system. That is not a risk to mitigate; it
//     is the default outcome.
//
// So the valuable part of the idea is not "make training an agent". It is
// **separating the thing that trains from the thing that judges**, and enforcing
// the separation structurally — the same move that keeps RecoveryAgent from
// holding an execution port.
//
// # The structural rule
//
// A promotion is decided by comparing two eval reports over **the same frozen case
// set**. Nothing in this package accepts a case set from the trainer, and Compare
// refuses when the two reports did not run the same cases. A model cannot pass by
// having changed the examination.
//
// # What an LLM may and may not design
//
// An LLM is genuinely good at the *process*: proposing hyperparameters, writing
// augmentation, diagnosing a failed run from its logs, authoring new eval cases,
// writing the training script. It must not decide *what counts as better*,
// because that is the one thing it can also move. The success criterion has to
// come from outside — in this repository, from robots completing tasks — and this
// package is where that boundary is written down instead of remembered.
package train

import (
	"fmt"
	"sort"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration/eval"
)

// Report identifies which artifacts a comparison is about, so a decision can be
// reproduced later from the record alone.
type Report struct {
	// Candidate names the checkpoint being proposed: a model id, a content hash,
	// or a run id. It must be specific enough that two candidates cannot share it.
	Candidate string `json:"candidate"`
	// Incumbent names what is currently serving. Empty means there is nothing to
	// replace, which is a promotion with no comparison — see Compare.
	Incumbent string `json:"incumbent,omitempty"`
	// Eval is the candidate's score.
	Eval eval.Report `json:"eval"`
	// Baseline is the incumbent's score on the same case set.
	Baseline eval.Report `json:"baseline"`
}

// Decision is whether a checkpoint may replace the incumbent, and why.
type Decision struct {
	Promote bool     `json:"promote"`
	Reasons []string `json:"reasons"`
}

// Refusal reasons, named so a training loop can branch on them instead of parsing
// prose.
const (
	// ReasonDifferentCases means the two reports did not run the same examination.
	ReasonDifferentCases = "different-case-sets"
	// ReasonExecutableRegressed means the candidate plans fewer requests.
	ReasonExecutableRegressed = "executable-regressed"
	// ReasonRefusalRegressed means the candidate refuses fewer requests it should
	// refuse — the direction that makes a model dangerous rather than useless.
	ReasonRefusalRegressed = "refusal-regressed"
	// ReasonNoImprovement means neither half got better.
	ReasonNoImprovement = "no-improvement"
	// ReasonRunFailed means one of the runs did not complete, so there is nothing
	// to compare.
	ReasonRunFailed = "run-incomplete"
)

// Compare decides whether candidate may replace incumbent.
//
// The rule is a Pareto improvement over the two dimensions the evaluation reports
// separately: neither the executable half nor the refusal half may get worse, and
// at least one must get better.
//
// It is deliberately not a weighted score. A weighted score lets a gain in "plans
// more requests" pay for a loss in "refuses requests it must refuse", and there is
// no exchange rate between those. A model that plans everything and refuses
// nothing is not 90% of a good model; it is a model that will execute a negation
// as an instruction. Keeping them separate is the whole reason the evaluation
// prints them separately.
func Compare(report Report) Decision {
	decision := Decision{}
	refuse := func(reason, detail string) Decision {
		decision.Promote = false
		decision.Reasons = append(decision.Reasons, reason+": "+detail)
		return decision
	}

	candidate, baseline := report.Eval, report.Baseline

	// A run that did not complete has no score. Treating a partial run as a low
	// score would discard a checkpoint over a dropped connection; treating it as a
	// pass would promote one that was never measured.
	if failed := incompleteRuns(candidate); failed != "" {
		return refuse(ReasonRunFailed, "候选运行未完成："+failed)
	}
	if failed := incompleteRuns(baseline); failed != "" {
		return refuse(ReasonRunFailed, "基线运行未完成："+failed)
	}

	// No incumbent: this is a first checkpoint, and there is nothing to subtract.
	//
	// It is checked before the case-set comparison because an absent baseline is
	// not a mismatched one: reporting "用例数不同：基线 0" would send a reader
	// looking for whoever tampered with the case set. Absence of a comparison is
	// its own finding, and the judgement it needs is the absolute floor — see Gate.
	if report.Incumbent == "" {
		return refuse(ReasonNoImprovement,
			"没有在役基线可供比较；首次上线的准入由绝对门槛决定，不由比较决定")
	}

	// The examination must be the same one. Two different case sets produce two
	// numbers that cannot be subtracted, and a comparison that allowed it would be
	// the easiest possible way to "improve": shrink the test.
	if detail := caseSetMismatch(baseline, candidate); detail != "" {
		return refuse(ReasonDifferentCases, detail)
	}

	switch {
	case candidate.ExecutablePassed < baseline.ExecutablePassed:
		return refuse(ReasonExecutableRegressed, fmt.Sprintf(
			"可执行用例从 %d/%d 降到 %d/%d",
			baseline.ExecutablePassed, baseline.ExecutableCases,
			candidate.ExecutablePassed, candidate.ExecutableCases))
	case refusalPassed(candidate) < refusalPassed(baseline):
		return refuse(ReasonRefusalRegressed, fmt.Sprintf(
			"该拒绝的用例从 %d/%d 降到 %d/%d",
			refusalPassed(baseline), refusalCases(baseline),
			refusalPassed(candidate), refusalCases(candidate)))
	}

	improvedExecutable := candidate.ExecutablePassed > baseline.ExecutablePassed
	improvedRefusal := refusalPassed(candidate) > refusalPassed(baseline)
	if !improvedExecutable && !improvedRefusal {
		decision.Promote = false
		decision.Reasons = append(decision.Reasons, ReasonNoImprovement+": "+
			"两半都没有变好；一个不比现役更好的 checkpoint 没有理由替换它")
		return decision
	}

	decision.Promote = true
	if improvedExecutable {
		decision.Reasons = append(decision.Reasons, fmt.Sprintf(
			"可执行用例 %d/%d → %d/%d",
			baseline.ExecutablePassed, baseline.ExecutableCases,
			candidate.ExecutablePassed, candidate.ExecutableCases))
	}
	if improvedRefusal {
		decision.Reasons = append(decision.Reasons, fmt.Sprintf(
			"该拒绝的用例 %d/%d → %d/%d",
			refusalPassed(baseline), refusalCases(baseline),
			refusalPassed(candidate), refusalCases(candidate)))
	}
	return decision
}

// Gate is the absolute floor a first checkpoint must clear before there is an
// incumbent to compare against.
//
// It exists because Compare answers "is this better than what we have", and that
// question is meaningless when there is nothing to have. The floor is stated in
// absolute terms so that "we have no baseline" cannot become a reason to promote
// anything at all.
type Gate struct {
	// MinExecutable is the fewest executable cases a first checkpoint may pass.
	MinExecutable int
	// MinRefusals is the fewest refusal cases it may pass.
	//
	// It is a separate floor rather than part of MinExecutable because the two
	// failures are not comparable: a model that plans nothing is useless, and a
	// model that refuses nothing is dangerous.
	MinRefusals int
}

// Admit decides whether a first checkpoint may serve.
func (g Gate) Admit(report eval.Report) Decision {
	decision := Decision{}
	if failed := incompleteRuns(report); failed != "" {
		return Decision{Reasons: []string{ReasonRunFailed + ": 候选运行未完成：" + failed}}
	}
	if report.ExecutablePassed < g.MinExecutable {
		decision.Reasons = append(decision.Reasons, fmt.Sprintf(
			"%s: 可执行用例 %d/%d，低于首次上线门槛 %d",
			ReasonExecutableRegressed, report.ExecutablePassed, report.ExecutableCases, g.MinExecutable))
	}
	if refusalPassed(report) < g.MinRefusals {
		decision.Reasons = append(decision.Reasons, fmt.Sprintf(
			"%s: 该拒绝的用例 %d/%d，低于首次上线门槛 %d",
			ReasonRefusalRegressed, refusalPassed(report), refusalCases(report), g.MinRefusals))
	}
	decision.Promote = len(decision.Reasons) == 0
	if decision.Promote {
		decision.Reasons = append(decision.Reasons, "通过首次上线门槛")
	}
	return decision
}

// refusalPassed is how many refusal cases the run got right.
//
// eval.Report counts passes and executable passes; the refusal half is the
// difference. It is derived here rather than added to the report because the
// report already carries both numbers and a third copy could disagree with them.
func refusalPassed(report eval.Report) int {
	return report.Passed - report.ExecutablePassed
}

func refusalCases(report eval.Report) int {
	return report.Cases - report.ExecutableCases
}

// incompleteRuns names the first case that failed to run, or "".
func incompleteRuns(report eval.Report) string {
	for _, outcome := range report.Outcomes {
		if outcome.Error != "" {
			return outcome.CaseID
		}
	}
	return ""
}

// caseSetMismatch reports how two runs' examinations differ, or "".
//
// Case ids are compared as sets. Case *order* is not part of the examination — a
// reordered case file is the same test — but the membership is, and so is the
// count, so that a duplicated or dropped case cannot slip through as a tie.
func caseSetMismatch(baseline, candidate eval.Report) string {
	if baseline.Cases != candidate.Cases {
		return fmt.Sprintf("用例数不同：基线 %d，候选 %d", baseline.Cases, candidate.Cases)
	}
	if baseline.Cases == 0 {
		return "两次运行都没有用例；没有可比的东西"
	}
	baseIDs := caseIDs(baseline)
	candidateIDs := caseIDs(candidate)
	if baseIDs == candidateIDs {
		return ""
	}
	return fmt.Sprintf("用例集合不同：基线 [%s]，候选 [%s]", baseIDs, candidateIDs)
}

func caseIDs(report eval.Report) string {
	ids := make([]string, 0, len(report.Outcomes))
	for _, outcome := range report.Outcomes {
		ids = append(ids, outcome.CaseID)
	}
	sort.Strings(ids)
	return strings.Join(ids, " ")
}
