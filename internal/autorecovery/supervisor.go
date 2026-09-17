// Package autorecovery runs the recovery steps a plan is allowed to take on its
// own, so that a system which can notice a problem can also start looking into it
// without waiting for somebody to press a button.
//
// # What "on its own" means here, exactly
//
// Exactly one thing is automatic: an action the catalog classifies as
// `read_only`. Everything else stops and waits, and the three reasons it can stop
// are different from each other and are recorded separately:
//
//   - a `bounded_write` waits for a person, because the catalog says it must;
//   - a `never_automatic` action is not run by anybody's proposal, so it is not
//     even reachable from here;
//   - an action whose tools are missing on this robot cannot run at all.
//
// # Why the line is drawn at read-only rather than at "does not mutate"
//
// The two are not the same question and the catalog answers the first. A service
// declares `MutatesWorld` about itself; the catalog's risk class is a judgement
// about what running the action *means*. Reading the risk class keeps one answer
// to "may this run unattended", in the place where that answer is reviewed. A
// tool-level check would be a second answer, and the two would eventually differ.
//
// # The contract this must never soften
//
// `ANOMALY_UNVERIFIED_MUTATION` — a physical action dispatched with no confirming
// observation — is the finding this system is most often in. The catalog answers
// it with `observe.re-read`, whose only tool is `telemetry.read`. Running that
// automatically is reconciliation, which is the correct and required response.
//
// The forbidden response is to re-dispatch the action. Nothing in this package can
// do that, and not by a check that could be removed: every mutating action is
// classified `bounded_write` or higher, and this package runs neither. The guard
// and the capability are the same fact.
package autorecovery

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
)

// Executor runs one action. It is the recovery executor, narrowed to what this
// package needs so a test can supply one without a robot.
type Executor interface {
	Execute(ctx context.Context, request recoveryexec.Request) (recoveryexec.Result, error)
}

// Catalog resolves an action id to its catalog entry, which is where the risk
// class — and therefore the permission to run unattended — comes from.
type Catalog interface {
	Lookup(id string) (agentruntime.RecoveryAction, bool)
}

// DefaultMaxActionsPerPlan bounds how many actions one plan may run unattended.
//
// The bound exists because plans come from a proposer and a proposer can be
// wrong in a way that produces a long list. Three is enough for the shape the
// catalog actually produces (a look-first read, and at most a couple more reads
// matched by the same finding) while being small enough that a runaway plan
// cannot turn into a long unattended session.
const DefaultMaxActionsPerPlan = 3

// StepOutcome is what happened to one proposed step.
//
// It carries three separate facts rather than one status, because they answer
// different questions and a reader needs all three: whether the executor was
// asked, what it said if it was, and — when it was not asked — why not.
type StepOutcome struct {
	Order    int    `json:"order"`
	ActionID string `json:"actionId"`
	// Risk is the catalog's classification, echoed so a reader can see the
	// classification the decision was made from.
	Risk string `json:"risk"`
	// Ran is true only when the executor was actually asked to run this action.
	// A step that was skipped is not a failure; it is a decision, and SkipReason
	// says which one.
	Ran bool `json:"ran"`
	// SkipReason is set when Ran is false.
	SkipReason string `json:"skipReason,omitempty"`
	// Result is what the executor reported, when it was asked.
	Result recoveryexec.Result `json:"result,omitempty"`
	// Error is set when asking the executor itself broke.
	Error string `json:"error,omitempty"`
}

// Supervisor runs a plan's automatic steps.
type Supervisor struct {
	Executor Executor
	Catalog  Catalog
	// Record is told about every step that was *not* run, and why.
	//
	// It deliberately says nothing about the steps that ran. Those are already
	// recorded by the executor, which owns that fact and records it with the trail
	// and the result; reporting them here as well produced two ledger entries for
	// one action, which is the same duplicate-a-fact bug the alert projection had.
	// One fact, one record: the executor owns "it ran", this owns "we decided not
	// to ask".
	//
	// Skipped steps are reported because a replay that showed only what ran would
	// make "the system looked and decided not to touch this" indistinguishable
	// from "the system never considered it" — and the first is the whole point of
	// an unattended pass.
	Record func(ctx context.Context, taskID, planID string, outcome StepOutcome)
	// MaxActionsPerPlan caps one pass. Zero means DefaultMaxActionsPerPlan.
	MaxActionsPerPlan int
	// Now is the clock. Tests set it.
	Now func() time.Time
}

// ErrNoCatalog means the supervisor cannot tell what is allowed to run, so it
// runs nothing. Being unable to check the permission is not the same as having it.
var ErrNoCatalog = errors.New("no recovery catalog is configured; nothing may run unattended")

// Run executes the steps of one plan that are allowed to run unattended.
//
// It stops at the first step that ran and reported an unexecuted or unconfirmed
// result. Continuing past that would mean deciding the next action against a
// world the previous one may have changed, which is the situation the closed loop
// exists to prevent.
func (s *Supervisor) Run(ctx context.Context, plan *agentcontract.RecoveryPlanPayload) ([]StepOutcome, error) {
	if plan == nil {
		return nil, nil
	}
	if s.Executor == nil || s.Catalog == nil {
		return nil, ErrNoCatalog
	}
	// An escalated plan is not a shorter plan. Its steps were deliberately kept
	// out of the payload, and the ones it retains are not to be acted on.
	if plan.Verdict != agentcontract.VerdictPlan {
		return nil, nil
	}

	budget := s.MaxActionsPerPlan
	if budget <= 0 {
		budget = DefaultMaxActionsPerPlan
	}

	outcomes := make([]StepOutcome, 0, len(plan.Steps))
	for _, step := range plan.Steps {
		if len(outcomes) >= budget {
			outcomes = append(outcomes, s.skip(plan.TaskID, plan.PlanID, step, fmt.Sprintf(
				"本次自动处理最多执行 %d 个动作，已到上限；其余留给操作者", budget)))
			break
		}
		outcome := s.runOne(ctx, plan, step)
		outcomes = append(outcomes, outcome)
		if outcome.Ran && !outcome.Result.Verified {
			// Ran but not confirmed — including "ran and could not tell". Nothing
			// further is safe to do automatically from here.
			break
		}
		if outcome.Ran && !outcome.Result.Executed {
			// Asked, and the executor declined to call anything. The next step
			// would be decided against the same unchanged world, but the reason
			// the first one did not run is usually a fact about the deployment
			// (no model, missing tool) that will not have changed either.
			break
		}
	}
	return outcomes, nil
}

func (s *Supervisor) runOne(ctx context.Context, plan *agentcontract.RecoveryPlanPayload, step agentcontract.RecoveryStep) StepOutcome {
	action, known := s.Catalog.Lookup(step.Action)
	if !known {
		return s.skip(plan.TaskID, plan.PlanID, step, "目录里没有这个动作")
	}
	if !action.Executable() {
		// The catalog's own sentence is used verbatim: a second wording here
		// would be a second answer to "why not this one".
		reason := action.Refusal
		if reason == "" {
			reason = "目录不允许由系统执行这个动作"
		}
		return s.skip(plan.TaskID, plan.PlanID, step, reason)
	}
	if action.Risk != agentruntime.RiskReadOnly {
		// This is the line. Everything that can change the robot stops here and
		// waits for a person, whatever the plan's proposer thought.
		return s.skip(plan.TaskID, plan.PlanID, step, fmt.Sprintf(
			"%s 会改动机器人（%s），自动处理不会执行它；需要操作者批准",
			action.ID, action.Risk))
	}

	result, err := s.Executor.Execute(ctx, recoveryexec.Request{
		Action: action,
		PlanID: plan.PlanID,
		TaskID: plan.TaskID,
		// Nobody approved this: it is running because the catalog classifies it
		// as changing nothing. Saying otherwise would put a person's consent in
		// the record where there was none. This flag is also how a reader tells an
		// automatic run from one a person started.
		OperatorApproved: false,
	})
	outcome := StepOutcome{
		Order: step.Order, ActionID: action.ID, Risk: string(action.Risk),
		Ran: true, Result: result,
	}
	if err != nil {
		outcome.Error = err.Error()
	}
	// Not recorded here: the executor records its own runs, with the trail. See
	// the Record field.
	return outcome
}

func (s *Supervisor) skip(taskID, planID string, step agentcontract.RecoveryStep, reason string) StepOutcome {
	outcome := StepOutcome{
		Order: step.Order, ActionID: step.Action, Risk: step.Risk,
		Ran: false, SkipReason: reason,
	}
	// context.Background() rather than the caller's: a skip is reported even when
	// the pass is being cancelled, because "we chose not to run this" is exactly
	// the record a cancelled run most needs to leave behind.
	s.record(context.Background(), taskID, planID, outcome)
	return outcome
}

func (s *Supervisor) record(ctx context.Context, taskID, planID string, outcome StepOutcome) {
	if s.Record == nil {
		return
	}
	s.Record(ctx, taskID, planID, outcome)
}
