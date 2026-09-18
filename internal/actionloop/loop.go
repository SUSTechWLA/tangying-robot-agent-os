// Package actionloop runs a bounded loop in which a model chooses the next tool
// call and the harness decides whether it may happen.
//
// # Why it exists
//
// Selecting tools and orchestrating them was already the model's job — but only
// once, at planning time. `orchestration.LLMPlanner` produces a whole step list,
// it is validated against the skill catalog, and from then on `executePlan`
// walks that list. When the world and the plan disagree, nothing re-decides: the
// loop returns an error and the task ends in RECOVERABLE_FAILURE.
//
// This package is the missing half. The list stops being the input and becomes
// the output of each round: observe, let the model choose one call, validate it,
// run it under the same gates, observe again.
//
// # What is deliberately unchanged
//
// A model choosing a step does not weaken anything the step would otherwise face.
// Every call goes through the manifest's own declaration — safety level, whether
// it mutates the world, what approval it needs — and through `closedloop.Gate` for
// completion. There is no "the model chose it, so skip the check" path, and the
// package holds no policy of its own that could drift from the manifest.
//
// # What it adds
//
//   - **Bounds.** A loop driven by a model needs a ceiling that a fixed plan did
//     not, because a fixed plan cannot iterate. Rounds are capped, and the
//     failure classes decide what may follow a failure: an unknown physical
//     outcome ends the loop outright, a perception failure permits another round
//     after a fresh observation, and a validation or fatal failure does not.
//   - **A decision record.** The replay already showed which steps ran. It could
//     not show why this step was chosen over the alternatives, because nothing
//     chose. Each round is recorded as structured data — the candidate set, the
//     choice, the reason, the verdict — so a reader can review the reasoning
//     rather than guess at it.
package actionloop

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
)

// Tool is one thing the loop may call, described by the same vocabulary the rest
// of the system already uses.
//
// The description fields are not documentation: they are what the model is shown
// to choose with, and they are the same fields the harness enforces. A tool whose
// advertised safety level differed from the enforced one would be a tool that
// invites a choice it then refuses.
type Tool struct {
	// Name is the identifier the model must return. It matches the manifest name.
	Name string
	// Description is what the model is told about when to use it.
	Description string
	// Parameters are the argument names, so the model knows what to supply.
	Parameters []string
	// SafetyLevel is the manifest's declaration.
	SafetyLevel skills.SafetyLevel
	// MutatesWorld is the manifest's declaration. A call that mutates the world is
	// not complete until a fresh post-call observation confirms it.
	MutatesWorld bool
	// Call runs it. It returns whatever the tool returns, which is a claim; the
	// gate below decides whether the claim may be recorded as completion.
	Call func(ctx context.Context, arguments map[string]any) (Result, error)
}

// Result is what one call produced.
type Result struct {
	// Success is the tool's own report. On its own it is never completion.
	Success bool
	// Code is the failure code, when there is one. It is classified by
	// `closedloop.Classify`, which is what decides whether another round is safe.
	Code string
	// Message is the tool's own sentence.
	Message string
	// Evidence is the observation taken after the call, when the call changed the
	// world. It is what the gate judges.
	Evidence *closedloop.Evidence
	// Detail is whatever the tool wants recorded, for the replay.
	Detail map[string]any
}

// Observation is what the loop knows about the world at the top of a round.
type Observation struct {
	// Summary is a bounded, human-readable statement of the current state, which
	// is what the model is shown.
	Summary string
	// EvidenceIDs identify the records behind it, so a reader can go and look.
	EvidenceIDs []string
}

// Decision is one round's choice.
//
// Exactly one of the three forms is expected: a tool call, `Done`, or `Blocked`.
// A decision that is none of them is refused rather than guessed at, because a
// model that did not answer the question is not a model that said "no".
type Decision struct {
	// Tool is the name of the tool to call. Empty when the round is not a call.
	Tool string
	// Arguments are passed to the tool.
	Arguments map[string]any
	// Reason is why this tool, in the model's words. It is recorded; it is not
	// trusted for anything.
	Reason string
	// Done means the model believes the goal is met. The harness still decides
	// whether that is true.
	Done bool
	// Blocked means the model cannot proceed, with a sentence for the operator.
	Blocked string
}

// Request is what a decider is shown.
type Request struct {
	Goal string
	// Tools are the calls available this round, already filtered to those the
	// current deployment may make. A decider is never shown a tool it may not use,
	// so a refusal here means it invented one.
	Tools []Tool
	// History is what previous rounds did.
	History []Round
	// Observation is the state at the top of this round.
	Observation Observation
	// Round counts from 1.
	Round int
}

// Decider chooses the next call. The implementation backed by a model lives with
// the model client; this package only needs the interface, so the loop can be
// tested by scripting decisions instead of standing up a server.
type Decider interface {
	Decide(ctx context.Context, request Request) (Decision, error)
}

// Round is the record of one decision and what came of it.
//
// It is the answer to "why did it do that", which a replay of steps alone cannot
// give: a fixed plan has no alternatives to explain.
type Round struct {
	// Round orders the decisions, starting from 1. It is the only ordering field:
	// a second sequence number that nothing populated would be a field a reader
	// has to check before trusting.
	Round int    `json:"round"`
	Tool  string `json:"tool"`
	// Candidates are the tool names the decider was offered. It is recorded
	// because "chose the only option" and "chose one of nine" are different
	// decisions, and a record that omitted the alternatives would make them look
	// the same.
	Candidates []string       `json:"candidates"`
	Reason     string         `json:"reason,omitempty"`
	Arguments  map[string]any `json:"arguments,omitempty"`
	// Verdict is what the harness said: refused, called, satisfied, unsatisfied.
	Verdict string `json:"verdict"`
	// Detail explains a refusal or an unsatisfied verdict.
	Detail string `json:"detail,omitempty"`
	// Code is the failure code, when the call failed.
	Code string `json:"code,omitempty"`
	// Class is the failure class the code maps to.
	Class string `json:"class,omitempty"`
	// ObservedAt is when the world was last looked at for this round.
	ObservedAt time.Time `json:"observedAt"`
	// DispatchAt is when the call was handed over.
	DispatchAt time.Time `json:"dispatchAt,omitempty"`
	// Duration is how long the call took.
	Duration time.Duration `json:"durationMs,omitempty"`
}

// Verdict values. They are stable identifiers so a console can group them
// without parsing prose.
const (
	VerdictCalled      = "CALLED"
	VerdictRefused     = "REFUSED"
	VerdictSatisfied   = "SATISFIED"
	VerdictUnsatisfied = "UNSATISFIED"
	VerdictDone        = "DONE"
	VerdictBlocked     = "BLOCKED"
	VerdictExhausted   = "ROUNDS_EXHAUSTED"
	// VerdictOutsideScope is a physical call the operator never approved, refused
	// so it can be asked about rather than executed or merely dropped.
	VerdictOutsideScope = "OUTSIDE_APPROVED_SCOPE"
)

// Scope reports whether a physical call is within what an operator approved.
//
// It is a separate question from `Approver`, and the separation is the whole
// point: an approver answers "may this specific call happen", while a scope
// answers "is this the kind of thing that was approved at all". Folding them
// together would mean that having someone able to approve is the same as having
// an unbounded mandate, and a model could widen its own remit simply by asking
// nicely for something the operator never saw.
//
// A call outside the scope is not executed and is not silently skipped. It stops
// the loop and is reported with the tool and arguments, because the useful answer
// to "the robot wanted to do something you did not approve" is the proposal, not
// a silence.
type Scope func(tool Tool) bool

// ScopeOf builds a scope that admits exactly the named tools.
//
// It exists so the common case — the approved plan's tool names — is one call,
// and so that an empty list is visibly an empty scope rather than a nil that
// might be read as "unbounded".
func ScopeOf(names ...string) Scope {
	admitted := make(map[string]struct{}, len(names))
	for _, name := range names {
		admitted[name] = struct{}{}
	}
	return func(tool Tool) bool {
		_, ok := admitted[tool.Name]
		return ok
	}
}

// Outcome is what the loop concluded.
type Outcome struct {
	// Completed is true only when the harness confirmed the last mutating call
	// and the model then said it was done.
	Completed bool `json:"completed"`
	// Escalated is true when the loop stopped and a person has to decide.
	Escalated bool `json:"escalated"`
	// Reason is a sentence for the operator.
	Reason string `json:"reason"`
	// Calls is how many tool calls were actually dispatched.
	//
	// It exists because "the loop returned without an error" and "something was
	// called" are different facts, and a caller that conflated them would report
	// a blocked run as an action taken. Only this package knows whether a call
	// left the process, so it says so here rather than making every caller infer
	// it from the verdicts — an inference that would be a second copy of the
	// dispatch rules, and would drift from them.
	Calls int `json:"calls"`
	// Rounds is every decision, in order.
	Rounds []Round `json:"rounds"`
}

// Errors the caller can act on.
var (
	// ErrUnknownTool means the decider named a tool it was not offered.
	ErrUnknownTool = errors.New("the decider chose a tool it was not offered")
	// ErrApprovalRefused means a physical call was not approved.
	ErrApprovalRefused = errors.New("a physical call was not approved")
)

// Approver decides whether one call may happen. It is a port rather than a flag
// so that "who may approve" stays outside this package, where it is already
// decided once for the whole system.
type Approver func(ctx context.Context, tool Tool, arguments map[string]any) (bool, error)

// Loop runs rounds until the goal is met, the model gives up, or a bound stops it.
type Loop struct {
	// Tools are the calls available, already filtered to the current deployment.
	Tools []Tool
	// Decider chooses.
	Decider Decider
	// Approve is consulted before a call whose manifest requires approval. Nil
	// means nothing may be approved, which is the safe reading of a missing
	// approver rather than a reason to skip the check.
	Approve Approver
	// Scope bounds which physical calls may be made at all. A physical call
	// outside it is refused and the loop stops, whatever Approve would have said.
	//
	// Nil refuses every physical call. That is deliberate and follows the same
	// rule as a nil Approve: a deployment that has not said what was approved has
	// not approved anything, and reading a missing scope as an unbounded one would
	// make the operator's approval meaningless exactly when it matters.
	//
	// Read-only and local calls are not scoped. They change nothing in the world,
	// and the loop cannot work without looking.
	Scope Scope
	// Observe reads the world. It is called before every round, and again after a
	// call that mutates the world — the second read is what the gate judges.
	Observe func(ctx context.Context) (Observation, error)
	// Record, when set, receives every round as it happens, so a console can watch
	// the loop rather than wait for it.
	Record func(round Round)
	// MaxRounds caps decisions. Zero means DefaultMaxRounds.
	MaxRounds int
	// MaxUnproductive caps consecutive rounds that chose nothing the harness could
	// run: an unknown tool, a refusal, or a decider error. Two is the default
	// because a model that has failed twice at the same question is not about to
	// answer it, and the operator should hear about it now.
	MaxUnproductive int
	// Now is the clock. Tests set it; production leaves it nil.
	Now func() time.Time
}

// DefaultMaxRounds bounds a loop that a fixed plan never needed to.
//
// It is well above a typical household task's step count so it does not cut
// legitimate recovery short, and finite so a model that keeps finding new things
// to try eventually stops and asks.
const DefaultMaxRounds = 24

// DefaultMaxUnproductive is how many futile rounds are tolerated in a row.
const DefaultMaxUnproductive = 2

// clock and bounded accessors.
func (l Loop) now() time.Time {
	if l.Now != nil {
		return l.Now().UTC()
	}
	return time.Now().UTC()
}

func (l Loop) maxRounds() int {
	if l.MaxRounds > 0 {
		return l.MaxRounds
	}
	return DefaultMaxRounds
}

func (l Loop) maxUnproductive() int {
	if l.MaxUnproductive > 0 {
		return l.MaxUnproductive
	}
	return DefaultMaxUnproductive
}

// Run drives the loop toward one goal.
//
// It never returns an error for a refusal or a failed call: those are rounds, and
// rounds are the record. An error is returned only when the loop could not run at
// all — no decider, or an unreadable world — because those are the caller's
// problems rather than the task's.
func (l Loop) Run(ctx context.Context, goal string) (Outcome, error) {
	if l.Decider == nil {
		return Outcome{}, errors.New("actionloop requires a decider")
	}
	if l.Observe == nil {
		return Outcome{}, errors.New("actionloop requires an observer")
	}
	index := map[string]Tool{}
	for _, tool := range l.Tools {
		if _, duplicate := index[tool.Name]; duplicate {
			return Outcome{}, fmt.Errorf("two tools share the name %q", tool.Name)
		}
		index[tool.Name] = tool
	}

	outcome := Outcome{}
	unproductive := 0
	for round := 1; round <= l.maxRounds(); round++ {
		if err := ctx.Err(); err != nil {
			outcome.Reason = "循环被取消"
			return outcome, err
		}
		observation, err := l.Observe(ctx)
		if err != nil {
			return outcome, fmt.Errorf("read the world before round %d: %w", round, err)
		}
		request := Request{
			Goal: goal, Tools: l.Tools, History: outcome.Rounds,
			Observation: observation, Round: round,
		}
		decision, err := l.Decider.Decide(ctx, request)
		if err != nil {
			// A decider that failed is a futile round, not a crash: the model being
			// unreachable is a fact about this attempt, and the bound decides when
			// it has happened enough times to stop.
			unproductive++
			outcome.Rounds = append(outcome.Rounds, l.record(Round{
				Round: round, Candidates: names(l.Tools), Verdict: VerdictRefused,
				Detail: "决策失败：" + err.Error(), ObservedAt: l.now(),
			}))
			if unproductive >= l.maxUnproductive() {
				outcome.Escalated = true
				outcome.Reason = "模型连续两次没能给出可执行的决策，需要人工介入"
				return outcome, nil
			}
			continue
		}

		switch {
		case decision.Blocked != "":
			outcome.Rounds = append(outcome.Rounds, l.record(Round{
				Round: round, Verdict: VerdictBlocked, Detail: decision.Blocked,
				Candidates: names(l.Tools), Reason: decision.Reason, ObservedAt: l.now(),
			}))
			outcome.Escalated = true
			outcome.Reason = decision.Blocked
			return outcome, nil

		case decision.Done:
			// "Done" is the decider's claim. Whether it is true is the harness's
			// question, and it was already answered: nothing reaches here with an
			// unconfirmed mutating call behind it, because that case stops the loop
			// where it happens.
			outcome.Rounds = append(outcome.Rounds, l.record(Round{
				Round: round, Verdict: VerdictDone, Reason: decision.Reason, ObservedAt: l.now(),
			}))
			outcome.Completed = true
			// The decider's own sentence is used when it gave one. A fixed
			// sentence here said "the model declared the goal met" for every
			// completion — including ones decided by the deterministic single-tool
			// decider on a deployment with no model at all, which put a model's
			// authority into a record no model had touched. It reached the task
			// ledger as the reason for a recovery, where it read as a model's
			// judgement.
			//
			// The fallback names the decider rather than a model, because the loop
			// knows it has one and does not know what kind it is.
			outcome.Reason = decision.Reason
			if outcome.Reason == "" {
				outcome.Reason = "决策器宣告目标达成，且此前每一步都通过了证据门"
			}
			return outcome, nil
		}

		tool, known := index[decision.Tool]
		if !known {
			// A model that names a tool it was not offered has misunderstood the
			// question, so this is a futile round rather than a call to refuse and
			// move past.
			unproductive++
			outcome.Rounds = append(outcome.Rounds, l.record(Round{
				Round: round, Tool: decision.Tool, Candidates: names(l.Tools),
				Reason: decision.Reason, Arguments: decision.Arguments,
				Verdict: VerdictRefused, Detail: "这个工具不在本轮可选范围内",
				ObservedAt: l.now(),
			}))
			if unproductive >= l.maxUnproductive() {
				outcome.Escalated = true
				outcome.Reason = "模型连续两次选择了不可用的工具，需要人工介入"
				return outcome, nil
			}
			continue
		}

		if needsScope(tool) && !l.withinScope(tool) {
			// Refused, recorded with the exact call, and escalated: the operator
			// needs to see what the model wanted, not that something was blocked.
			outcome.Rounds = append(outcome.Rounds, l.record(Round{
				Round: round, Tool: tool.Name, Candidates: names(l.Tools),
				Reason: decision.Reason, Arguments: decision.Arguments,
				Verdict: VerdictOutsideScope,
				Detail: fmt.Sprintf("%s 会动机器人，但不在已批准的范围内；需要重新批准后才能执行",
					tool.Name),
				ObservedAt: l.now(),
			}))
			outcome.Escalated = true
			outcome.Reason = fmt.Sprintf(
				"模型想执行 %s，它不在已批准的范围内。需要人工批准这一动作后才能继续。", tool.Name)
			return outcome, nil
		}

		if needsApproval(tool) {
			approved, err := l.approved(ctx, tool, decision.Arguments)
			if err != nil {
				return outcome, fmt.Errorf("ask for approval: %w", err)
			}
			if !approved {
				// Refused, not skipped and not retried: a physical call nobody
				// approved is a decision for a person, and trying a different tool
				// instead would be working around the refusal.
				outcome.Rounds = append(outcome.Rounds, l.record(Round{
					Round: round, Tool: tool.Name, Candidates: names(l.Tools),
					Reason: decision.Reason, Arguments: decision.Arguments,
					Verdict: VerdictRefused, Detail: "物理动作没有得到批准",
					ObservedAt: l.now(),
				}))
				outcome.Escalated = true
				outcome.Reason = fmt.Sprintf("%s 会动机器人，没有得到批准", tool.Name)
				return outcome, nil
			}
		}

		unproductive = 0
		roundRecord, verdict := l.call(ctx, round, tool, decision)
		// Counted here, at the one place a call is dispatched. Everything above
		// this line is a decision *not* to call, and counting any of it would make
		// "the loop did something" true for a run that only refused.
		outcome.Calls++
		outcome.Rounds = append(outcome.Rounds, l.record(roundRecord))

		switch verdict {
		case callUnknownOutcome:
			// The one outcome that ends everything. The world may already have
			// changed, so no further call is safe — not a different tool, not the
			// same tool again. This is the closed-loop contract, and a loop driven
			// by a model is exactly where it would be tempting to soften.
			outcome.Escalated = true
			outcome.Reason = roundRecord.Detail
			return outcome, nil
		case callFatal:
			outcome.Escalated = true
			outcome.Reason = roundRecord.Detail
			return outcome, nil
		}
	}

	outcome.Escalated = true
	outcome.Reason = fmt.Sprintf("达到决策轮数上限（%d），停止并向人工升级", l.maxRounds())
	outcome.Rounds = append(outcome.Rounds, l.record(Round{
		Round: l.maxRounds(), Verdict: VerdictExhausted,
		Detail: outcome.Reason, ObservedAt: l.now(),
	}))
	return outcome, nil
}

// callVerdict is how one call ended, as far as the loop is concerned.
type callVerdict int

const (
	callOK callVerdict = iota
	// callUnknownOutcome means the call may have changed the world and that could
	// not be established. Nothing may run after it.
	callUnknownOutcome
	// callFatal means the failure class permits no further attempt.
	callFatal
	// callRecoverable means another round is allowed, after a fresh observation.
	callRecoverable
)

func (l Loop) call(
	ctx context.Context,
	round int,
	tool Tool,
	decision Decision,
) (Round, callVerdict) {
	record := Round{
		Round: round, Tool: tool.Name,
		// Every round carries the menu, not only the refusals: "chose the only
		// option" and "chose one of nine" are different decisions, and a record
		// that showed the alternatives only when the choice was rejected would
		// make the successful ones look inevitable.
		Candidates: names(l.Tools),
		Reason:     decision.Reason, Arguments: decision.Arguments,
		ObservedAt: l.now(),
	}
	dispatchedAt := l.now()
	record.DispatchAt = dispatchedAt
	startedAt := l.now()
	result, err := tool.Call(ctx, decision.Arguments)
	record.Duration = l.now().Sub(startedAt)
	record.Verdict = VerdictCalled

	if err != nil && result.Code == "" {
		// The tool threw and named nothing. That is the unknown case: nothing is
		// known about whether it reached the world before it threw.
		result.Code = "TOOL_EXECUTION_ERROR"
		result.Success = false
	}
	if !result.Success {
		record.Code = result.Code
		class := closedloop.Classify(result.Code)
		record.Class = string(class)
		record.Detail = result.Message
		if class == closedloop.UnknownOutcome {
			record.Verdict = VerdictUnsatisfied
			record.Detail = "动作结果未知：" + describeCode(result)
			return record, callUnknownOutcome
		}
		if !class.Retryable() {
			record.Verdict = VerdictUnsatisfied
			record.Detail = fmt.Sprintf("%s 属于 %s，重试同类动作不会成功：%s",
				result.Code, class, result.Message)
			return record, callFatal
		}
		record.Verdict = VerdictUnsatisfied
		return record, callRecoverable
	}

	// The tool reported success. Whether that is completion is not its to say.
	verdict := closedloop.Gate(
		closedloop.Declaration{Manifest: tool.MutatesWorld}, dispatchedAt, result.Evidence)
	if err := verdict.Require(); err != nil {
		record.Verdict = VerdictUnsatisfied
		record.Detail = "动作自述成功，但没有动作后的新鲜观测可以确认；按闭环契约不能记为完成"
		if tool.MutatesWorld {
			// A successful return with no post-call observation is the definition of
			// an unknown outcome, not a failed step to try again.
			//
			// The class and code are written here, not left to be inferred from the
			// verdict. This is the most consequential refusal the loop makes — it is
			// the one that forbids every later call — and a round record that named
			// no class left a reader to work out from the verdict alone that the
			// step was unretryable. UNVERIFIED_WORLD_MUTATION is the code
			// core/closedloop already classifies as UnknownOutcome for this case.
			record.Code = "UNVERIFIED_WORLD_MUTATION"
			record.Class = string(closedloop.UnknownOutcome)
			record.Detail = "动作自述成功，但没有动作后的新鲜观测可以确认；结果未知，不能记为完成，也不能重做"
			return record, callUnknownOutcome
		}
		return record, callRecoverable
	}
	record.Verdict = VerdictSatisfied
	return record, callOK
}

func describeCode(result Result) string {
	if result.Message != "" {
		return result.Code + "：" + result.Message
	}
	return result.Code
}

// needsApproval reports whether the manifest requires a person before this call.
//
// It is derived from the safety level rather than asked of the tool, so a tool
// cannot opt itself out of approval by claiming it does not need one.
func needsApproval(tool Tool) bool {
	return tool.SafetyLevel == skills.SafetyPhysical
}

// needsScope reports whether a call is bounded by the approved scope.
//
// Only physical calls are. A read-only call cannot change the world, and a local
// side effect does not reach the robot; bounding those would stop the loop from
// even looking, which is not a safety property but an outage.
func needsScope(tool Tool) bool {
	return tool.SafetyLevel == skills.SafetyPhysical
}

func (l Loop) withinScope(tool Tool) bool {
	if l.Scope == nil {
		return false
	}
	return l.Scope(tool)
}

func (l Loop) approved(ctx context.Context, tool Tool, arguments map[string]any) (bool, error) {
	if l.Approve == nil {
		return false, nil
	}
	return l.Approve(ctx, tool, arguments)
}

func (l Loop) record(round Round) Round {
	if l.Record != nil {
		l.Record(round)
	}
	return round
}

func names(tools []Tool) []string {
	list := make([]string, 0, len(tools))
	for _, tool := range tools {
		list = append(list, tool.Name)
	}
	return list
}
