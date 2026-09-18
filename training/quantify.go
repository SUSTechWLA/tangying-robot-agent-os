// Package training turns the task ledger into training data and into the numbers
// that say whether that data is worth training on.
//
// # The one rule
//
// **A record is a positive example only when the closed loop confirmed it.** A step
// whose status says COMPLETED is not evidence that anything happened; the whole
// point of this repository's evidence gate is that a tool's own account of its
// success is not proof. The same standard applies here, one level up: if training
// data were built from "the step reported success", a model would be taught the
// exact claim the execution layer refuses to believe.
//
// So a positive needs a fresh observation captured after the action. Everything
// else is a negative, a diagnostic, or unusable — and the counts of each are
// reported, because "how much good data do I have" is the question that decides
// whether to train at all.
//
// # Why this is a pipeline and not an agent
//
// Nothing here decides anything. It reads a ledger, counts what it found, and
// writes files. The judgement — is this data good enough, is this checkpoint
// better — belongs to the evaluation and to `train.Compare`, which the thing being
// trained cannot reach. A component that both produced training data and judged it
// would be marking its own homework.
package training

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"time"
)

// Verdict is how much a record can be trusted, derived from the evidence rather
// than from any status field.
type Verdict string

const (
	// VerdictVerified: the task reached a successful terminal state and the steps
	// that changed the world have a fresh observation captured after them. Only
	// these are positive examples.
	VerdictVerified Verdict = "verified"
	// VerdictFailed: the task ended in a failure state. Usable as a negative, and
	// as the seed of an evaluation case.
	VerdictFailed Verdict = "failed"
	// VerdictUnknownOutcome: a physical step started and never reported finishing,
	// with no observation to settle it. **Deliberately excluded from both halves.**
	// Teaching a model that this is success teaches it to take physical actions
	// whose effect nobody checked; teaching it that this is failure teaches it to
	// avoid the safest thing it did. The honest use is to exclude it and report how
	// many there were.
	VerdictUnknownOutcome Verdict = "unknown-outcome"
	// VerdictUnfinished: still running, or never started. Not yet a fact.
	VerdictUnfinished Verdict = "unfinished"
	// VerdictRefused: the system declined the request. These are the samples that
	// keep a fine-tune from learning to always answer.
	VerdictRefused Verdict = "refused"
)

// Step is one step as the ledger recorded it.
type Step struct {
	StepID      string `json:"stepId"`
	Action      string `json:"action,omitempty"`
	Capability  string `json:"capability,omitempty"`
	SafetyLevel string `json:"safetyLevel,omitempty"`
	Status      string `json:"status"`
	// Postcondition is what the plan said would be true afterwards.
	Postcondition string `json:"requiredPostcondition,omitempty"`
}

// Evidence is one archived observation, reduced to what training needs.
//
// The pixels are deliberately not carried: a training export that embedded frames
// would be gigabytes of mostly-unlabelled images, and the frame is not what this
// pipeline is training on. What matters is that a capture exists, when it happened
// relative to the action, and which step it belongs to.
type Evidence struct {
	ID      string    `json:"id"`
	StepID  string    `json:"stepId,omitempty"`
	Capture string    `json:"captureId,omitempty"`
	At      time.Time `json:"at"`
}

// Record is one task, as training data.
type Record struct {
	TaskID  string `json:"taskId"`
	Request string `json:"request"`
	Adapter string `json:"adapter,omitempty"`
	// PlanSource names who planned it: deterministic, llm, llm_consensus. A record
	// whose plan came from the deterministic planner carries no orchestration
	// signal at all — that planner returns an empty bundle by design — and the
	// quantification counts those separately rather than inflating the total.
	PlanSource string `json:"planSource,omitempty"`
	// Skills is the plan's skill sequence, when there was one.
	Skills []string `json:"skills,omitempty"`
	State  string   `json:"state"`
	Steps  []Step   `json:"steps,omitempty"`
	// Evidence is every observation archived for this task.
	Evidence []Evidence `json:"evidence,omitempty"`
	// FailureClass is the closed-loop classification of what went wrong, when the
	// ledger recorded one.
	FailureClass string `json:"failureClass,omitempty"`

	Verdict Verdict `json:"verdict"`
	// Why explains the verdict in words, so a reviewer can disagree with it. A
	// verdict without a reason is a number nobody can audit.
	Why string `json:"why"`
}

// Classify decides what a record is worth, from its evidence.
//
// It takes the record by pointer and fills Verdict and Why, because the decision
// and its stated reason must not be separable — a caller that could set one
// without the other would eventually do so.
func (r *Record) Classify() {
	switch {
	case r.State == successState:
		if missing := r.stepsNeedingEvidence(); len(missing) > 0 {
			// Reached a successful terminal state, but a step that changes the world
			// has no observation after it. This is the case the closed loop exists
			// to catch, and it is not a positive example.
			r.Verdict = VerdictUnknownOutcome
			r.Why = fmt.Sprintf(
				"任务成功结束，但有 %d 个改动世界的步骤没有后续观测（%s）；按闭环契约不能算已确认",
				len(missing), strings.Join(missing, "、"))
			return
		}
		r.Verdict = VerdictVerified
		r.Why = "任务成功结束，且每个改动世界的步骤都有动作后的观测"
	case r.State == unknownState:
		r.Verdict = VerdictUnknownOutcome
		r.Why = "有物理步骤开始后没有结束，且没有观测能判定结果；既不作为正样本也不作为负样本"
	case r.State == "" || r.State == readyState:
		r.Verdict = VerdictUnfinished
		r.Why = "任务尚未结束（未开始或仍在运行），还不是一个事实"
	default:
		r.Verdict = VerdictFailed
		r.Why = "任务以失败状态结束：" + r.State
	}
}

// stepsNeedingEvidence names the steps that changed the world and have no
// observation captured at or after the moment they were recorded.
//
// The comparison is conservative: an observation is accepted when it is not
// earlier than the step's own record. Anything finer would need per-step clocks
// the ledger does not keep, and guessing would make the verdict unfalsifiable.
func (r *Record) stepsNeedingEvidence() []string {
	missing := make([]string, 0)
	for _, step := range r.Steps {
		if !changesWorld(step.SafetyLevel) {
			continue
		}
		if r.evidenceFor(step.StepID) {
			continue
		}
		missing = append(missing, step.StepID)
	}
	return missing
}

func (r *Record) evidenceFor(stepID string) bool {
	for _, evidence := range r.Evidence {
		if evidence.StepID == stepID {
			return true
		}
	}
	return false
}

// changesWorld reports whether a safety level means the step touched the world.
//
// It is a whitelist of the levels that do not, so a level added later is treated
// as changing the world until somebody says otherwise. The cost of being wrong in
// that direction is demanding evidence that may not be needed; the cost in the
// other direction is calling an unverified physical action a verified one.
func changesWorld(safetyLevel string) bool {
	switch strings.ToLower(strings.TrimSpace(safetyLevel)) {
	case "", "read_only", "readonly":
		return false
	default:
		return true
	}
}

// Ledger states, named here so the classifier does not compare bare strings.
const (
	successState = "SUCCEEDED"
	unknownState = "RECOVERABLE_FAILURE"
	readyState   = "READY"
)

// Fingerprint identifies a request by its content, so that the same request asked
// twice counts once.
//
// The archive is dominated by repetition — 64 tasks in the reference deployment
// were 15 distinct requests — and a training set built by counting rows would be
// mostly duplicates of four sentences. Counting distinct fingerprints is what makes
// "how much data do I have" an answerable question.
func Fingerprint(request string) string {
	normalised := strings.Join(strings.Fields(strings.ToLower(strings.TrimSpace(request))), " ")
	sum := sha256.Sum256([]byte(normalised))
	return hex.EncodeToString(sum[:8])
}

// Quantification is what the archive actually contains.
//
// Every field answers a question somebody has to answer before spending GPU time:
// is there enough, is it varied, and how much of it is trustworthy.
type Quantification struct {
	Records int `json:"records"`
	// DistinctRequests is the number of distinct fingerprints. It is the number
	// that matters: repeated identical requests teach a model nothing new.
	DistinctRequests int `json:"distinctRequests"`
	// ByVerdict is the honesty table.
	ByVerdict map[Verdict]int `json:"byVerdict"`
	// ByPlanSource separates records whose orchestration is real from those whose
	// plan is an empty deterministic bundle.
	ByPlanSource map[string]int `json:"byPlanSource"`
	// UsablePositives counts verified records with an LLM or consensus plan: the
	// only records that carry orchestration signal.
	UsablePositives int `json:"usablePositives"`
	// DistinctPositiveRequests is the size of the set that could actually teach
	// something.
	DistinctPositiveRequests int `json:"distinctPositiveRequests"`
	// UnknownOutcomes is reported as its own line rather than folded into failures,
	// because a growing count here means the evidence gate is firing, which is a
	// different problem from tasks going wrong.
	UnknownOutcomes int `json:"unknownOutcomes"`
}

// Quantify counts what is in a set of records.
func Quantify(records []Record) Quantification {
	result := Quantification{
		Records:      len(records),
		ByVerdict:    map[Verdict]int{},
		ByPlanSource: map[string]int{},
	}
	requests := map[string]bool{}
	positiveRequests := map[string]bool{}
	for _, record := range records {
		result.ByVerdict[record.Verdict]++
		source := record.PlanSource
		if source == "" {
			source = "(none)"
		}
		result.ByPlanSource[source]++
		requests[Fingerprint(record.Request)] = true
		if record.Verdict == VerdictVerified {
			if source == "llm" || source == "llm_consensus" {
				result.UsablePositives++
				positiveRequests[Fingerprint(record.Request)] = true
			}
		}
		if record.Verdict == VerdictUnknownOutcome {
			result.UnknownOutcomes++
		}
	}
	result.DistinctRequests = len(requests)
	result.DistinctPositiveRequests = len(positiveRequests)
	return result
}

// Summary renders the quantification for a person deciding whether to train.
func (q Quantification) Summary() string {
	var builder strings.Builder
	fmt.Fprintf(&builder, "记录 %d 条，其中不同请求 %d 个\n", q.Records, q.DistinctRequests)
	builder.WriteString("按可信度：\n")
	for _, verdict := range []Verdict{
		VerdictVerified, VerdictFailed, VerdictRefused,
		VerdictUnknownOutcome, VerdictUnfinished,
	} {
		if q.ByVerdict[verdict] == 0 {
			continue
		}
		fmt.Fprintf(&builder, "  %-16s %4d\n", verdict, q.ByVerdict[verdict])
	}
	builder.WriteString("按计划来源：\n")
	sources := make([]string, 0, len(q.ByPlanSource))
	for source := range q.ByPlanSource {
		sources = append(sources, source)
	}
	sort.Strings(sources)
	for _, source := range sources {
		fmt.Fprintf(&builder, "  %-16s %4d\n", source, q.ByPlanSource[source])
	}
	fmt.Fprintf(&builder, "可用正样本（已确认 + 真编排）：%d 条，覆盖 %d 个不同请求\n",
		q.UsablePositives, q.DistinctPositiveRequests)
	if q.UnknownOutcomes > 0 {
		fmt.Fprintf(&builder, "结果未知（两半都不计入）：%d 条\n", q.UnknownOutcomes)
	}
	builder.WriteString(verdictAdvice(q))
	return builder.String()
}

// verdictAdvice says what the numbers mean for training, because a table of counts
// does not tell a reader whether to spend a GPU week.
func verdictAdvice(q Quantification) string {
	switch {
	case q.DistinctPositiveRequests >= 200 && q.UnknownOutcomes*4 < q.Records:
		return "→ 数据量足以开始 SFT；先按请求去重再切分，避免同一句话同时进训练与评测。\n"
	case q.DistinctPositiveRequests > 0:
		return fmt.Sprintf(
			"→ 正样本覆盖 %d 个不同请求，**不足以训练**，足以**起一套评测集**。\n"+
				"  先补数据：每个技能至少几十个不同说法，否则模型学到的是这几句话。\n",
			q.DistinctPositiveRequests)
	default:
		return "→ 没有可用正样本。先修执行链（当前正样本为 0 说明任务根本没成功到可确认），再谈训练。\n"
	}
}
