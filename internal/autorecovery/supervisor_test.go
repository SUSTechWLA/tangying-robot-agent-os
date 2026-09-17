package autorecovery_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/autorecovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
)

// --- fakes ------------------------------------------------------------------

// fakeExecutor records what it was asked to run and answers with a canned result.
type fakeExecutor struct {
	asked   []string
	askedOp []bool // OperatorApproved, per call
	result  recoveryexec.Result
	err     error
}

func (f *fakeExecutor) Execute(_ context.Context, request recoveryexec.Request) (recoveryexec.Result, error) {
	f.asked = append(f.asked, request.Action.ID)
	f.askedOp = append(f.askedOp, request.OperatorApproved)
	if f.err != nil {
		return recoveryexec.Result{}, f.err
	}
	result := f.result
	result.ActionID = request.Action.ID
	return result, nil
}

// fakeCatalog answers from the real shipped catalog, so a test cannot pass by
// agreeing with a catalog that does not exist.
type fakeCatalog struct {
	actions map[string]agentruntime.RecoveryAction
}

func realCatalog() *fakeCatalog {
	actions := map[string]agentruntime.RecoveryAction{}
	for _, action := range agentruntime.DefaultRecoveryCatalog().Actions() {
		actions[action.ID] = action
	}
	return &fakeCatalog{actions: actions}
}

func (c *fakeCatalog) Lookup(id string) (agentruntime.RecoveryAction, bool) {
	action, ok := c.actions[id]
	return action, ok
}

func plan(steps ...agentcontract.RecoveryStep) *agentcontract.RecoveryPlanPayload {
	return &agentcontract.RecoveryPlanPayload{
		PlanID: "plan-1", TaskID: "task-1", Verdict: agentcontract.VerdictPlan, Steps: steps,
	}
}

func step(order int, action string) agentcontract.RecoveryStep {
	return agentcontract.RecoveryStep{Order: order, Action: action}
}

func newSupervisor(executor autorecovery.Executor) (*autorecovery.Supervisor, *[]autorecovery.StepOutcome) {
	recorded := &[]autorecovery.StepOutcome{}
	return &autorecovery.Supervisor{
		Executor: executor, Catalog: realCatalog(),
		Record: func(_ context.Context, _, _ string, outcome autorecovery.StepOutcome) {
			*recorded = append(*recorded, outcome)
		},
	}, recorded
}

// --- the line, and why it is where it is ------------------------------------

// A read-only action runs. This is the whole point: the system can start looking
// into a problem without a person pressing anything.
func TestAReadOnlyActionRunsUnattended(t *testing.T) {
	executor := &fakeExecutor{result: recoveryexec.Result{Executed: true, Verified: true}}
	supervisor, _ := newSupervisor(executor)

	outcomes, err := supervisor.Run(context.Background(), plan(step(1, "observe.re-read")))
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(executor.asked) != 1 || executor.asked[0] != "observe.re-read" {
		t.Fatalf("asked = %v", executor.asked)
	}
	if len(outcomes) != 1 || !outcomes[0].Ran {
		t.Fatalf("outcomes = %+v", outcomes)
	}
}

// Nobody approved it, and the record must not say otherwise. "The system decided
// this was safe to do alone" is a different fact from "a person agreed", and a
// later reader has no other way to tell them apart.
func TestUnattendedRunsAreNotRecordedAsApproved(t *testing.T) {
	executor := &fakeExecutor{result: recoveryexec.Result{Executed: true, Verified: true}}
	supervisor, _ := newSupervisor(executor)

	if _, err := supervisor.Run(context.Background(), plan(step(1, "observe.re-read"))); err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(executor.askedOp) != 1 || executor.askedOp[0] {
		t.Fatalf("OperatorApproved = %v: an unattended run claimed a person's consent", executor.askedOp)
	}
}

// THE guard. A bounded write is never run by the automatic pass, however the plan
// was produced and whatever it says about itself.
func TestABoundedWriteIsNeverRunUnattended(t *testing.T) {
	// Every executable mutating action in the shipped catalog, so this cannot
	// pass by the test having picked the one action that happens to be checked.
	mutating := []string{}
	for _, action := range agentruntime.DefaultRecoveryCatalog().Actions() {
		if action.Executable() && action.Risk != agentruntime.RiskReadOnly {
			mutating = append(mutating, action.ID)
		}
	}
	if len(mutating) == 0 {
		t.Fatal("the catalog has no mutating actions; this test is not testing anything")
	}

	executor := &fakeExecutor{result: recoveryexec.Result{Executed: true, Verified: true}}
	supervisor, recorded := newSupervisor(executor)

	steps := make([]agentcontract.RecoveryStep, 0, len(mutating))
	for index, id := range mutating {
		steps = append(steps, step(index+1, id))
	}
	// The cap would otherwise stop the pass early; this test is about the
	// classification, not the budget.
	supervisor.MaxActionsPerPlan = len(mutating) + 1

	if _, err := supervisor.Run(context.Background(), plan(steps...)); err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(executor.asked) != 0 {
		t.Fatalf("asked = %v: a mutating action was run with nobody deciding", executor.asked)
	}
	for _, outcome := range *recorded {
		if outcome.Ran {
			t.Fatalf("outcome = %+v", outcome)
		}
		if !strings.Contains(outcome.SkipReason, "需要操作者批准") {
			t.Fatalf("skip reason = %q", outcome.SkipReason)
		}
	}
}

// A never-automatic action is refused by the catalog before the risk question is
// even reached, and it is refused with the catalog's own sentence rather than a
// second wording invented here.
func TestANeverAutomaticActionIsRefusedWithTheCatalogsOwnWords(t *testing.T) {
	executor := &fakeExecutor{}
	supervisor, recorded := newSupervisor(executor)

	if _, err := supervisor.Run(context.Background(), plan(step(1, "estop.release"))); err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(executor.asked) != 0 {
		t.Fatalf("asked = %v", executor.asked)
	}
	refusal, known := agentruntime.DefaultRecoveryCatalog().Lookup("estop.release")
	if !known {
		t.Fatal("estop.release is not in the shipped catalog")
	}
	if !strings.Contains((*recorded)[0].SkipReason, refusal.Refusal) {
		t.Fatalf("skip reason = %q, want the catalog's %q", (*recorded)[0].SkipReason, refusal.Refusal)
	}
}

// An action the catalog does not contain cannot be run, so a plan naming one is
// not partially honoured — the step is skipped and the reason says why.
func TestAnActionOutsideTheCatalogIsSkipped(t *testing.T) {
	executor := &fakeExecutor{}
	supervisor, recorded := newSupervisor(executor)

	if _, err := supervisor.Run(context.Background(), plan(step(1, "robot.take_over"))); err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(executor.asked) != 0 {
		t.Fatalf("asked = %v", executor.asked)
	}
	if !strings.Contains((*recorded)[0].SkipReason, "目录里没有这个动作") {
		t.Fatalf("skip reason = %q", (*recorded)[0].SkipReason)
	}
}

// --- stopping rules ---------------------------------------------------------

// A run that reported an unconfirmed result stops the pass. Continuing would
// decide the next action against a world the previous one may have changed.
func TestAnUnconfirmedResultStopsThePass(t *testing.T) {
	executor := &fakeExecutor{result: recoveryexec.Result{Executed: true, Verified: false}}
	supervisor, _ := newSupervisor(executor)

	outcomes, err := supervisor.Run(context.Background(), plan(
		step(1, "observe.re-read"), step(2, "map.read-status")))
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(executor.asked) != 1 {
		t.Fatalf("asked = %v: the pass continued past an unconfirmed result", executor.asked)
	}
	if len(outcomes) != 1 {
		t.Fatalf("outcomes = %+v", outcomes)
	}
}

// An escalated plan is not a shorter plan. Its steps were deliberately withheld,
// and whatever remains is not to be acted on.
func TestAnEscalatedPlanRunsNothing(t *testing.T) {
	executor := &fakeExecutor{}
	supervisor, _ := newSupervisor(executor)

	escalated := plan(step(1, "observe.re-read"))
	escalated.Verdict = agentcontract.VerdictEscalate

	outcomes, err := supervisor.Run(context.Background(), escalated)
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(executor.asked) != 0 || len(outcomes) != 0 {
		t.Fatalf("an escalated plan was partially executed: asked=%v outcomes=%+v", executor.asked, outcomes)
	}
}

// The budget is a real bound, and the step it stopped at is reported rather than
// dropped: a plan silently truncated is a plan a reader will misread.
func TestTheBudgetIsEnforcedAndTheStoppedStepIsReported(t *testing.T) {
	executor := &fakeExecutor{result: recoveryexec.Result{Executed: true, Verified: true}}
	supervisor, recorded := newSupervisor(executor)
	supervisor.MaxActionsPerPlan = 2

	if _, err := supervisor.Run(context.Background(), plan(
		step(1, "observe.re-read"), step(2, "map.read-status"), step(3, "nav.read-map"),
	)); err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(executor.asked) != 2 {
		t.Fatalf("asked = %v", executor.asked)
	}
	last := (*recorded)[len(*recorded)-1]
	if last.ActionID != "nav.read-map" || last.Ran {
		t.Fatalf("the step the budget stopped at was not reported: %+v", last)
	}
	if !strings.Contains(last.SkipReason, "上限") {
		t.Fatalf("skip reason = %q", last.SkipReason)
	}
}

// --- refusals that are not crashes ------------------------------------------

// Being unable to check what is allowed is not the same as being allowed.
func TestWithNoCatalogNothingRuns(t *testing.T) {
	executor := &fakeExecutor{}
	supervisor := &autorecovery.Supervisor{Executor: executor}

	if _, err := supervisor.Run(context.Background(), plan(step(1, "observe.re-read"))); !errors.Is(err, autorecovery.ErrNoCatalog) {
		t.Fatalf("err = %v", err)
	}
	if len(executor.asked) != 0 {
		t.Fatalf("asked = %v", executor.asked)
	}
}

// A plan that does not exist is not an error and runs nothing.
func TestANilPlanRunsNothing(t *testing.T) {
	executor := &fakeExecutor{}
	supervisor, _ := newSupervisor(executor)

	outcomes, err := supervisor.Run(context.Background(), nil)
	if err != nil || outcomes != nil {
		t.Fatalf("outcomes = %+v err = %v", outcomes, err)
	}
	if len(executor.asked) != 0 {
		t.Fatalf("asked = %v", executor.asked)
	}
}

// An executor that breaks is reported as a failed step, not as a panic and not as
// a silent success.
func TestAnExecutorFailureIsRecordedAgainstTheStep(t *testing.T) {
	executor := &fakeExecutor{err: errors.New("机器人连接断了")}
	supervisor, _ := newSupervisor(executor)

	outcomes, err := supervisor.Run(context.Background(), plan(step(1, "observe.re-read")))
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(outcomes) != 1 || outcomes[0].Error == "" {
		t.Fatalf("outcomes = %+v", outcomes)
	}
	if !strings.Contains(outcomes[0].Error, "机器人连接断了") {
		t.Fatalf("outcomes = %+v", outcomes)
	}
}

// One fact, one record.
//
// A step that ran is recorded by the executor, which owns that fact and records it
// with the trail and the result. If the supervisor recorded it too, every
// automatic run would appear twice in the task's replay — the same
// duplicate-a-fact bug the alert projection had, in a new place.
func TestARunStepIsNotRecordedTwice(t *testing.T) {
	executor := &fakeExecutor{result: recoveryexec.Result{Executed: true, Verified: true}}
	supervisor, recorded := newSupervisor(executor)

	if _, err := supervisor.Run(context.Background(), plan(step(1, "observe.re-read"))); err != nil {
		t.Fatalf("run: %v", err)
	}
	for _, outcome := range *recorded {
		if outcome.Ran {
			t.Fatalf("the supervisor also recorded a run, which the executor already records: %+v", outcome)
		}
	}
}

// ...but a step that was *not* run is recorded, because nothing else knows about
// it. Without this, "the system decided not to touch this" and "the system never
// considered it" look identical in a replay.
func TestASkippedStepIsRecorded(t *testing.T) {
	executor := &fakeExecutor{}
	supervisor, recorded := newSupervisor(executor)

	if _, err := supervisor.Run(context.Background(), plan(step(1, "map.activate"))); err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(*recorded) != 1 {
		t.Fatalf("recorded = %+v", *recorded)
	}
	if (*recorded)[0].Ran || (*recorded)[0].SkipReason == "" {
		t.Fatalf("recorded = %+v", (*recorded)[0])
	}
}
