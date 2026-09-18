package training_test

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/training"
)

// verified builds a record that the closed loop confirmed: successful terminal
// state, an LLM plan, and evidence for the step that moved the robot.
func verified(request string) training.Record {
	return training.Record{
		TaskID: "t-" + training.Fingerprint(request), Request: request,
		PlanSource: "llm", State: "SUCCEEDED",
		Skills: []string{"manipulation.pick", "manipulation.place"},
		Steps: []training.Step{
			{StepID: "resolve", SafetyLevel: "read_only", Status: "COMPLETED"},
			{StepID: "pick", SafetyLevel: "physical_motion", Status: "COMPLETED"},
		},
		Evidence: []training.Evidence{{ID: "e1", StepID: "pick"}},
	}
}

func classify(record training.Record) training.Record {
	record.Classify()
	return record
}

// --- the one rule: success is not proof -------------------------------------

// A successful terminal state with no observation after the physical step is the
// case the closed loop exists to catch. Calling it a positive example would teach
// a model exactly the claim the execution layer refuses to believe.
func TestASuccessWithoutEvidenceIsNotAPositive(t *testing.T) {
	record := verified("把杯子拿给我")
	record.Evidence = nil // the physical step has nothing after it
	record = classify(record)
	if record.Verdict != training.VerdictUnknownOutcome {
		t.Fatalf("verdict = %q, want unknown-outcome", record.Verdict)
	}
	if !strings.Contains(record.Why, "观测") {
		t.Fatalf("why = %q", record.Why)
	}
}

func TestAConfirmedSuccessIsAPositive(t *testing.T) {
	record := classify(verified("把杯子拿给我"))
	if record.Verdict != training.VerdictVerified {
		t.Fatalf("verdict = %q (%s)", record.Verdict, record.Why)
	}
}

// A read-only step needs no evidence: it changed nothing, so there is nothing to
// confirm. Demanding it would mark every look-first plan unverified.
func TestAReadOnlyStepNeedsNoEvidence(t *testing.T) {
	record := training.Record{
		Request: "去厨房看看", PlanSource: "llm", State: "SUCCEEDED",
		Steps: []training.Step{{StepID: "navigate", SafetyLevel: "read_only", Status: "COMPLETED"}},
	}
	if got := classify(record); got.Verdict != training.VerdictVerified {
		t.Fatalf("verdict = %q (%s)", got.Verdict, got.Why)
	}
}

// The default for an unknown safety level is "it changed the world", because the
// cost of wrongly demanding evidence is smaller than the cost of wrongly calling an
// unverified physical action verified.
func TestAnUnknownSafetyLevelIsTreatedAsChangingTheWorld(t *testing.T) {
	record := training.Record{
		Request: "把杯子拿给我", PlanSource: "llm", State: "SUCCEEDED",
		Steps: []training.Step{{StepID: "mystery", SafetyLevel: "", Status: "COMPLETED"}},
	}
	if got := classify(record); got.Verdict != training.VerdictVerified {
		t.Fatalf("an empty safety level was treated as changing the world: %v", got.Verdict)
	}
	record.Steps[0].SafetyLevel = "brand_new_level"
	if got := classify(record); got.Verdict != training.VerdictUnknownOutcome {
		t.Fatalf("a new safety level was assumed harmless: %v", got.Verdict)
	}
}

// --- unknown outcome is excluded from both halves ---------------------------

// Counting it as failure would teach the model to avoid the safest action it took;
// counting it as success would teach it to take unchecked physical actions.
func TestAnUnknownOutcomeIsNeitherPositiveNorNegative(t *testing.T) {
	record := verified("把杯子拿给我")
	record.State = "RECOVERABLE_FAILURE"
	record = classify(record)
	if record.Verdict != training.VerdictUnknownOutcome {
		t.Fatalf("verdict = %q", record.Verdict)
	}
	dataset := training.Split([]training.Record{record})
	if len(dataset.SFT) != 0 || len(dataset.Negative) != 0 {
		t.Fatalf("an unknown outcome landed in a training set: %+v", dataset)
	}
}

// --- de-duplication ---------------------------------------------------------

// The archive is dominated by repetition; counting rows would make four sentences
// look like a dataset.
func TestRepeatedRequestsCountOnce(t *testing.T) {
	records := []training.Record{
		classify(verified("把杯子拿给我")),
		classify(verified("把杯子拿给我")),
		classify(verified(" 把杯子拿给我 ")),
	}
	quantification := training.Quantify(records)
	if quantification.DistinctRequests != 1 {
		t.Fatalf("distinct = %d, want 1", quantification.DistinctRequests)
	}
	if quantification.Records != 3 {
		t.Fatalf("records = %d", quantification.Records)
	}
}

// A request that both succeeded and failed must not appear as both. Success wins,
// because success is the thing being taught.
func TestARequestThatSucceededAndFailedBecomesAPositive(t *testing.T) {
	succeeded := classify(verified("把杯子拿给我"))
	failed := classify(verified("把杯子拿给我"))
	failed.State = "RECOVERABLE_FAILURE"
	dataset := training.Split([]training.Record{failed, succeeded})
	if len(dataset.SFT) != 1 {
		t.Fatalf("sft = %d, want 1", len(dataset.SFT))
	}
	if len(dataset.Negative) != 0 {
		t.Fatalf("the same request also landed in the negative set: %+v", dataset.Negative)
	}
	_ = failed
}

// --- a verified task with no plan teaches nothing ---------------------------

// The deterministic planner returns an empty bundle by design, so a task planned
// by it carries an outcome but no orchestration. Counting it as an SFT row would
// pad the set with rows that have nothing to learn from.
func TestAVerifiedTaskWithoutAPlanIsExcludedNotCounted(t *testing.T) {
	record := verified("去厨房看看")
	record.PlanSource = "deterministic"
	record.Skills = nil
	dataset := training.Split([]training.Record{classify(record)})
	if len(dataset.SFT) != 0 {
		t.Fatalf("a planless record reached the SFT set: %+v", dataset.SFT)
	}
	if dataset.Excluded["verified-but-no-plan"] != 1 {
		t.Fatalf("excluded = %+v", dataset.Excluded)
	}
}

// --- the advice, which is the point of the whole exercise -------------------

// With no usable positives the summary must say "collect more data" rather than
// printing a green table and letting somebody start a training run.
func TestWithNoPositivesTheSummarySaysSo(t *testing.T) {
	quantification := training.Quantify([]training.Record{classify(verified("x"))})
	if quantification.UsablePositives != 1 {
		t.Fatalf("usable = %d", quantification.UsablePositives)
	}
	empty := training.Quantify(nil)
	if !strings.Contains(empty.Summary(), "没有可用正样本") {
		t.Fatalf("summary = %q", empty.Summary())
	}
}

// A handful of distinct positives is enough to seed an evaluation set and nowhere
// near enough to train on, and the summary has to say that rather than report a
// record count that looks impressive.
func TestFewDistinctPositivesAreCalledInsufficient(t *testing.T) {
	records := []training.Record{
		classify(verified("a")), classify(verified("b")), classify(verified("c")),
	}
	summary := training.Quantify(records).Summary()
	if !strings.Contains(summary, "不足以训练") {
		t.Fatalf("summary = %q", summary)
	}
}
