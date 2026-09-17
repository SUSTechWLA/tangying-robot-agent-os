package actionloop_test

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

// scriptedDecider returns pre-set decisions in order and records what it was
// offered, so a test can assert on the choice and on the menu it was chosen from.
type scriptedDecider struct {
	decisions []actionloop.Decision
	errs      []error
	requests  []actionloop.Request
	calls     int
}

func (d *scriptedDecider) Decide(_ context.Context, request actionloop.Request) (actionloop.Decision, error) {
	d.requests = append(d.requests, request)
	index := d.calls
	d.calls++
	if index < len(d.errs) && d.errs[index] != nil {
		return actionloop.Decision{}, d.errs[index]
	}
	if index >= len(d.decisions) {
		return actionloop.Decision{Done: true, Reason: "没有更多要做的"}, nil
	}
	return d.decisions[index], nil
}

func readOnlyTool(name string, calls *[]string) actionloop.Tool {
	return actionloop.Tool{
		Name: name, Description: "只读", SafetyLevel: skills.SafetyReadOnly,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			*calls = append(*calls, name)
			return actionloop.Result{Success: true}, nil
		},
	}
}

func loopingObserver() func(context.Context) (actionloop.Observation, error) {
	return func(context.Context) (actionloop.Observation, error) {
		return actionloop.Observation{Summary: "工作台上有红色杯子", EvidenceIDs: []string{"obs-1"}}, nil
	}
}

// --- no hardcoded sequence ---------------------------------------------------

// The whole point: the sequence is not written down anywhere. A decider that
// picks B, then A, then C gets exactly that, even though the tools were offered
// in a different order.
func TestTheSequenceComesFromTheDeciderNotFromTheCode(t *testing.T) {
	var calls []string
	tools := []actionloop.Tool{
		readOnlyTool("observe_scene", &calls),
		readOnlyTool("navigation.navigate", &calls),
		readOnlyTool("manipulation.pick", &calls),
	}
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "navigation.navigate", Reason: "先过去"},
		{Tool: "observe_scene", Reason: "再看一眼"},
		{Tool: "manipulation.pick", Reason: "拿起来"},
		{Done: true, Reason: "拿到了"},
	}}
	loop := actionloop.Loop{Tools: tools, Decider: decider, Observe: loopingObserver()}

	outcome, err := loop.Run(context.Background(), "把红色杯子拿过来")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Completed {
		t.Fatalf("outcome = %+v", outcome)
	}
	if strings.Join(calls, ",") != "navigation.navigate,observe_scene,manipulation.pick" {
		t.Fatalf("calls = %v, want the order the decider chose", calls)
	}
}

// The decider is shown the tools it may choose from, so a model is never asked
// to pick from a menu it cannot see.
func TestTheDeciderIsShownTheAvailableToolsAndTheHistory(t *testing.T) {
	var calls []string
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "a", Reason: "第一步"},
		{Done: true},
	}}
	loop := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("a", &calls), readOnlyTool("b", &calls)},
		Decider: decider, Observe: loopingObserver(),
	}
	if _, err := loop.Run(context.Background(), "目标"); err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(decider.requests) != 2 {
		t.Fatalf("the decider was asked %d times, want 2", len(decider.requests))
	}
	first := decider.requests[0]
	if len(first.Tools) != 2 || first.Tools[0].Name != "a" {
		t.Fatalf("tools offered = %+v", first.Tools)
	}
	if first.Observation.Summary == "" || len(first.Observation.EvidenceIDs) == 0 {
		t.Fatalf("the first round saw no observation: %+v", first.Observation)
	}
	// The second round sees what the first did, which is what lets a model react
	// rather than follow a plan.
	if len(decider.requests[1].History) != 1 || decider.requests[1].History[0].Tool != "a" {
		t.Fatalf("history = %+v", decider.requests[1].History)
	}
}

// --- the closed-loop contract, under a model's hand --------------------------

// A successful return is not completion. A call that mutates the world and comes
// back "success" without a post-call observation is an unknown outcome, and that
// ends the loop — not with a retry, and not by trying a different tool.
func TestAMutatingCallWithoutEvidenceEndsTheLoop(t *testing.T) {
	var calls []string
	tools := []actionloop.Tool{
		{
			Name: "manipulation.pick", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true,
			Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				calls = append(calls, "pick")
				// Success, and nothing to confirm it with.
				return actionloop.Result{Success: true, Message: "拿起来了"}, nil
			},
		},
		readOnlyTool("observe_scene", &calls),
	}
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "manipulation.pick"},
		{Tool: "observe_scene", Reason: "换个工具再试"},
	}}
	loop := actionloop.Loop{
		Tools: tools, Decider: decider, Observe: loopingObserver(),
		Scope:   actionloop.ScopeOf("manipulation.pick"),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
	}
	outcome, err := loop.Run(context.Background(), "拿起杯子")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if outcome.Completed {
		t.Fatal("a call with no post-call evidence was recorded as completing the task")
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v, want escalation", outcome)
	}
	if strings.Join(calls, ",") != "pick" {
		t.Fatalf("calls = %v: nothing may run after an unknown outcome", calls)
	}
	if len(decider.requests) != 1 {
		t.Fatalf("the decider was asked %d times; it must not be asked again", len(decider.requests))
	}
}

// And with evidence, the same call completes.
func TestAMutatingCallWithFreshEvidenceSatisfiesTheGate(t *testing.T) {
	var calls []string
	dispatched := time.Now().UTC()
	tools := []actionloop.Tool{{
		Name: "manipulation.pick", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			calls = append(calls, "pick")
			return actionloop.Result{
				Success: true,
				Evidence: &closedloop.Evidence{
					ObservationID: "obs-2", ObservedAt: dispatched.Add(time.Second),
					Freshness: "FRESH", SourceID: "robot-1/head-rgbd", Confidence: 0.9,
				},
			}, nil
		},
	}}
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "manipulation.pick"}, {Done: true}}}
	loop := actionloop.Loop{
		Tools: tools, Decider: decider, Observe: loopingObserver(),
		Scope:   actionloop.ScopeOf("manipulation.pick"),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
	}
	outcome, err := loop.Run(context.Background(), "拿起杯子")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Completed {
		t.Fatalf("outcome = %+v, want completion", outcome)
	}
	if outcome.Rounds[0].Verdict != actionloop.VerdictSatisfied {
		t.Fatalf("verdict = %s, want SATISFIED", outcome.Rounds[0].Verdict)
	}
}

// A failure whose code says "the world may have moved" ends everything, even
// though it arrived as an ordinary failure with a code.
func TestAnUnknownOutcomeFailureCodeEndsTheLoop(t *testing.T) {
	var calls []string
	tools := []actionloop.Tool{{
		Name: "manipulation.place", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			calls = append(calls, "place")
			return actionloop.Result{Success: false, Code: "PLACEMENT_NOT_OBSERVED", Message: "未观测到"}, nil
		},
	}}
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "manipulation.place"}, {Done: true}}}
	loop := actionloop.Loop{
		Tools: tools, Decider: decider, Observe: loopingObserver(),
		Scope:   actionloop.ScopeOf("manipulation.place"),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
	}
	outcome, err := loop.Run(context.Background(), "放进盒子")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Escalated || outcome.Completed {
		t.Fatalf("outcome = %+v", outcome)
	}
	if len(calls) != 1 {
		t.Fatalf("calls = %v, want exactly one", calls)
	}
	if !strings.Contains(outcome.Reason, "结果未知") {
		t.Fatalf("reason = %q, want it to say the outcome is unknown", outcome.Reason)
	}
}

// A perception failure may be tried again after a fresh look — that is what the
// class means — so the loop continues.
func TestARecoverableFailurePermitsAnotherRound(t *testing.T) {
	attempts := 0
	tools := []actionloop.Tool{{
		Name: "manipulation.pick", SafetyLevel: skills.SafetyReadOnly,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			attempts++
			if attempts == 1 {
				return actionloop.Result{Success: false, Code: "GRASP_FAILED", Message: "没夹住"}, nil
			}
			return actionloop.Result{Success: true}, nil
		},
	}}
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "manipulation.pick"}, {Tool: "manipulation.pick"}, {Done: true},
	}}
	outcome, err := actionloop.Loop{Tools: tools, Decider: decider, Observe: loopingObserver()}.
		Run(context.Background(), "拿起来")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if attempts != 2 {
		t.Fatalf("attempts = %d, want 2: GRASP_FAILED is a perception failure", attempts)
	}
	if outcome.Rounds[0].Class != string(closedloop.Perception) {
		t.Fatalf("class = %q, want PERCEPTION", outcome.Rounds[0].Class)
	}
	if !outcome.Completed {
		t.Fatalf("outcome = %+v", outcome)
	}
}

// A validation failure does not: repeating the same command cannot succeed, and
// the model is not allowed to keep trying until the round cap.
func TestAFailureThatCannotSucceedStopsImmediately(t *testing.T) {
	tools := []actionloop.Tool{{
		Name: "manipulation.pick", SafetyLevel: skills.SafetyReadOnly,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			return actionloop.Result{Success: false, Code: "TOOL_PARAMETERS_INVALID", Message: "参数不合法"}, nil
		},
	}}
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "manipulation.pick"}}}
	outcome, err := actionloop.Loop{Tools: tools, Decider: decider, Observe: loopingObserver()}.
		Run(context.Background(), "拿起来")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v, want escalation", outcome)
	}
	if len(outcome.Rounds) != 1 || len(decider.requests) != 1 {
		t.Fatalf("the loop kept going after a failure that cannot succeed: %+v", outcome.Rounds)
	}
}

// A tool that throws is the unknown case, whatever it would have said.
func TestAToolThatThrowsIsAnUnknownOutcome(t *testing.T) {
	var calls []string
	tools := []actionloop.Tool{{
		Name: "manipulation.pick", SafetyLevel: skills.SafetyReadOnly, MutatesWorld: true,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			calls = append(calls, "pick")
			return actionloop.Result{}, errors.New("serial bus exploded")
		},
	}}
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "manipulation.pick"}, {Done: true}}}
	outcome, err := actionloop.Loop{Tools: tools, Decider: decider, Observe: loopingObserver()}.
		Run(context.Background(), "拿起来")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Escalated || len(calls) != 1 {
		t.Fatalf("outcome = %+v calls = %v", outcome, calls)
	}
	if outcome.Rounds[0].Code != "TOOL_EXECUTION_ERROR" {
		t.Fatalf("code = %q", outcome.Rounds[0].Code)
	}
}

// --- approval ----------------------------------------------------------------

// A physical call nobody approved stops the loop rather than working around the
// refusal with a different tool.
func TestAnUnapprovedPhysicalCallStopsTheLoop(t *testing.T) {
	var calls []string
	tools := []actionloop.Tool{
		{
			Name: "manipulation.pick", SafetyLevel: skills.SafetyPhysical,
			Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				calls = append(calls, "pick")
				return actionloop.Result{Success: true}, nil
			},
		},
		readOnlyTool("observe_scene", &calls),
	}
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "manipulation.pick"}, {Tool: "observe_scene", Reason: "那条路走不通"},
	}}
	outcome, err := actionloop.Loop{
		Tools: tools, Decider: decider, Observe: loopingObserver(),
		Scope:   actionloop.ScopeOf("manipulation.pick"),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return false, nil },
	}.Run(context.Background(), "拿起杯子")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if calls != nil {
		t.Fatalf("calls = %v; nothing may run without approval", calls)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v", outcome)
	}
}

// No approver means nothing may be approved. A missing port is read as "no", not
// as a reason to skip the check.
func TestAMissingApproverRefusesRatherThanPermits(t *testing.T) {
	var called bool
	tools := []actionloop.Tool{{
		Name: "manipulation.pick", SafetyLevel: skills.SafetyPhysical,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			called = true
			return actionloop.Result{Success: true}, nil
		},
	}}
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "manipulation.pick"}}}
	outcome, _ := actionloop.Loop{
		Tools: tools, Decider: decider, Observe: loopingObserver(),
		Scope: actionloop.ScopeOf("manipulation.pick"),
	}.Run(context.Background(), "拿起杯子")
	if called {
		t.Fatal("a physical tool ran with no approver configured")
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v", outcome)
	}
}

// A read-only tool needs no approval, so the loop does not stop asking for one.
func TestAReadOnlyToolNeedsNoApproval(t *testing.T) {
	var calls []string
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "observe_scene"}, {Done: true}}}
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", &calls)},
		Decider: decider, Observe: loopingObserver(),
	}.Run(context.Background(), "看一眼")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Completed || len(calls) != 1 {
		t.Fatalf("outcome = %+v calls = %v", outcome, calls)
	}
}

// --- the model misbehaving ---------------------------------------------------

// A tool the decider was not offered is refused, and enough of those stop the loop.
func TestChoosingAToolThatWasNotOfferedIsRefusedAndBounded(t *testing.T) {
	var calls []string
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "rm -rf"},         // not offered -> futile
		{Tool: "observe_scene"},  // offered -> runs, resets the counter
		{Tool: "self_destruct"},  // not offered -> futile
		{Tool: "navigation.fly"}, // not offered -> bound reached
	}}
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", &calls)},
		Decider: decider, Observe: loopingObserver(),
	}.Run(context.Background(), "随便")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if strings.Join(calls, ",") != "observe_scene" {
		t.Fatalf("calls = %v; only the offered tool may run", calls)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v", outcome)
	}
	// Two futile rounds in a row is the bound; the intervening offered tool resets
	// it, so the loop survives one mistake and not two in a row.
	if len(decider.requests) != 4 {
		t.Fatalf("the decider was asked %d times, want 4", len(decider.requests))
	}
	for _, round := range outcome.Rounds {
		if round.Verdict != actionloop.VerdictCalled && round.Tool != "observe_scene" && round.Tool == "" {
			t.Fatalf("an unoffered tool produced a runnable record: %+v", round)
		}
	}
}

// A decider that errors is a futile round, not a crash, and it is bounded too.
func TestADeciderThatKeepsFailingIsBounded(t *testing.T) {
	decider := &scriptedDecider{errs: []error{errors.New("model unreachable"), errors.New("model unreachable")}}
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", new([]string))},
		Decider: decider, Observe: loopingObserver(),
	}.Run(context.Background(), "目标")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v", outcome)
	}
	if decider.calls != 2 {
		t.Fatalf("the decider was asked %d times, want the bound of 2", decider.calls)
	}
}

// The model saying "I cannot" is an answer, not a failure.
func TestAModelThatReportsItIsBlockedEscalatesWithItsReason(t *testing.T) {
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Blocked: "工作台高度和标定不符，需要人重新标定"},
	}}
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", new([]string))},
		Decider: decider, Observe: loopingObserver(),
	}.Run(context.Background(), "放上去")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Escalated || outcome.Completed {
		t.Fatalf("outcome = %+v", outcome)
	}
	if outcome.Reason != "工作台高度和标定不符，需要人重新标定" {
		t.Fatalf("reason = %q, want the model's own sentence", outcome.Reason)
	}
}

// --- bounds ------------------------------------------------------------------

// A loop a model drives needs a ceiling a fixed plan never needed.
func TestTheRoundCapStopsALoopThatKeepsTrying(t *testing.T) {
	var calls []string
	tools := []actionloop.Tool{readOnlyTool("observe_scene", &calls)}
	decider := &scriptedDecider{}
	for index := 0; index < 50; index++ {
		decider.decisions = append(decider.decisions, actionloop.Decision{Tool: "observe_scene"})
	}
	outcome, err := actionloop.Loop{
		Tools: tools, Decider: decider, Observe: loopingObserver(), MaxRounds: 5,
	}.Run(context.Background(), "永远做不完")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v", outcome)
	}
	if len(calls) != 5 {
		t.Fatalf("calls = %d, want the cap of 5", len(calls))
	}
	if outcome.Rounds[len(outcome.Rounds)-1].Verdict != actionloop.VerdictExhausted {
		t.Fatalf("the last round is not the exhaustion record: %+v", outcome.Rounds[len(outcome.Rounds)-1])
	}
}

// A cancelled context stops the loop rather than being reported as a task failure.
func TestACancelledContextStopsTheLoop(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", new([]string))},
		Decider: &scriptedDecider{}, Observe: loopingObserver(),
	}.Run(ctx, "目标")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
}

// An unreadable world is the caller's problem, not the task's.
func TestAnUnreadableWorldIsReportedAsAnError(t *testing.T) {
	_, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", new([]string))},
		Decider: &scriptedDecider{},
		Observe: func(context.Context) (actionloop.Observation, error) {
			return actionloop.Observation{}, errors.New("no telemetry")
		},
	}.Run(context.Background(), "目标")
	if err == nil || !strings.Contains(err.Error(), "no telemetry") {
		t.Fatalf("err = %v", err)
	}
}

// --- the record --------------------------------------------------------------

// Every round is recorded as structured data, including the menu it was chosen
// from: "chose the only option" and "chose one of nine" are different decisions.
func TestEveryRoundIsRecordedWithItsCandidatesAndVerdict(t *testing.T) {
	var calls []string
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "observe_scene", Reason: "先看清"},
		{Tool: "navigation.navigate", Reason: "走过去"},
		{Done: true, Reason: "到了"},
	}}
	var watched []actionloop.Round
	// A clock that advances by a known step, so "the duration was recorded" is a
	// claim the test can check rather than one it hopes a fast call will satisfy.
	tick := time.Date(2026, 9, 17, 12, 0, 0, 0, time.UTC)
	now := func() time.Time { tick = tick.Add(10 * time.Millisecond); return tick }
	outcome, err := actionloop.Loop{
		Tools: []actionloop.Tool{
			readOnlyTool("observe_scene", &calls), readOnlyTool("navigation.navigate", &calls),
		},
		Decider: decider, Observe: loopingObserver(), Now: now,
		Record: func(round actionloop.Round) { watched = append(watched, round) },
	}.Run(context.Background(), "去厨房")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(watched) != len(outcome.Rounds) {
		t.Fatalf("watched %d rounds, outcome has %d", len(watched), len(outcome.Rounds))
	}
	if len(outcome.Rounds) != 3 {
		t.Fatalf("rounds = %+v", outcome.Rounds)
	}
	first := outcome.Rounds[0]
	if len(first.Candidates) != 2 {
		t.Fatalf("candidates = %v, want the full menu", first.Candidates)
	}
	if first.Reason != "先看清" {
		t.Fatalf("reason = %q", first.Reason)
	}
	if first.Verdict != actionloop.VerdictSatisfied {
		t.Fatalf("verdict = %q", first.Verdict)
	}
	if first.DispatchAt.IsZero() {
		t.Fatalf("the dispatch instant was not recorded: %+v", first)
	}
	if first.Duration <= 0 {
		t.Fatalf("the call duration was not recorded: %+v", first)
	}
	if outcome.Rounds[2].Verdict != actionloop.VerdictDone {
		t.Fatalf("the last round is not the model's completion: %+v", outcome.Rounds[2])
	}
}

// Two tools with one name is a wiring mistake, and it would make the record
// ambiguous about which one ran.
func TestDuplicateToolNamesAreRefused(t *testing.T) {
	var calls []string
	_, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("a", &calls), readOnlyTool("a", &calls)},
		Decider: &scriptedDecider{}, Observe: loopingObserver(),
	}.Run(context.Background(), "目标")
	if err == nil {
		t.Fatal("two tools sharing a name were accepted")
	}
}

func TestADeciderAndAnObserverAreRequired(t *testing.T) {
	if _, err := (&actionloop.Loop{Observe: loopingObserver()}).Run(context.Background(), "g"); err == nil {
		t.Fatal("a loop with no decider ran")
	}
	if _, err := (&actionloop.Loop{Decider: &scriptedDecider{}}).Run(context.Background(), "g"); err == nil {
		t.Fatal("a loop with no observer ran")
	}
}

// --- the approved scope ------------------------------------------------------

// physicalTool builds a call that moves the robot and records that it ran.
func physicalTool(name string, calls *[]string) actionloop.Tool {
	return actionloop.Tool{
		Name: name, SafetyLevel: skills.SafetyPhysical, MutatesWorld: true,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			*calls = append(*calls, name)
			return actionloop.Result{Success: true}, nil
		},
	}
}

// The rule: a physical action the operator never approved does not run, however
// willing the approver is.
//
// Scope and approval answer different questions. "May I approve this call" is not
// "was this kind of thing approved", and a model that can widen its own remit by
// asking for something the operator never saw has no remit at all.
func TestAPhysicalCallOutsideTheApprovedScopeNeverRuns(t *testing.T) {
	var calls []string
	tools := []actionloop.Tool{
		physicalTool("manipulation.pick", &calls),
		physicalTool("manipulation.place", &calls),
	}
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "manipulation.place", Reason: "顺手也放一下"},
		{Tool: "manipulation.pick", Reason: "那就拿这个"},
	}}
	approvals := 0
	outcome, err := actionloop.Loop{
		Tools: tools, Decider: decider, Observe: loopingObserver(),
		// Only the pick was approved.
		Scope: actionloop.ScopeOf("manipulation.pick"),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) {
			approvals++
			return true, nil
		},
	}.Run(context.Background(), "把杯子放进盘子")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(calls) != 0 {
		t.Fatalf("calls = %v: an unapproved physical action ran", calls)
	}
	if approvals != 0 {
		t.Fatalf("the approver was consulted %d times for an out-of-scope call", approvals)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v, want escalation", outcome)
	}
	// The proposal is reported, not just the refusal: the operator needs to see
	// what the model wanted in order to decide about it.
	last := outcome.Rounds[len(outcome.Rounds)-1]
	if last.Verdict != actionloop.VerdictOutsideScope {
		t.Fatalf("verdict = %q, want %q", last.Verdict, actionloop.VerdictOutsideScope)
	}
	if last.Tool != "manipulation.place" {
		t.Fatalf("the refused tool was not named: %+v", last)
	}
	if last.Reason != "顺手也放一下" {
		t.Fatalf("the model's reason was not carried into the proposal: %+v", last)
	}
	if !strings.Contains(outcome.Reason, "manipulation.place") {
		t.Fatalf("reason = %q, want it to name the action awaiting approval", outcome.Reason)
	}
	// And it stops rather than asking the model again: choosing a different
	// in-scope tool instead would be working around the operator.
	if len(decider.requests) != 1 {
		t.Fatalf("the decider was asked %d times; the loop must stop for re-approval", len(decider.requests))
	}
}

// A physical call inside the scope still needs its approval: the scope says the
// action was contemplated, not that this instance of it was approved.
func TestInsideTheScopeApprovalIsStillRequired(t *testing.T) {
	var calls []string
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "manipulation.pick"}}}
	outcome, _ := actionloop.Loop{
		Tools:   []actionloop.Tool{physicalTool("manipulation.pick", &calls)},
		Decider: decider, Observe: loopingObserver(),
		Scope: actionloop.ScopeOf("manipulation.pick"),
		// No approver at all.
	}.Run(context.Background(), "拿起杯子")
	if len(calls) != 0 {
		t.Fatalf("calls = %v: being in scope is not being approved", calls)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v", outcome)
	}
	if outcome.Rounds[0].Verdict == actionloop.VerdictOutsideScope {
		t.Fatalf("an in-scope call was reported as out of scope: %+v", outcome.Rounds[0])
	}
}

// A deployment that never declared a scope has approved nothing, so no physical
// action runs. A missing scope read as unbounded would make the declaration
// meaningless exactly where it matters.
func TestAMissingScopeRefusesEveryPhysicalCall(t *testing.T) {
	var calls []string
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "manipulation.pick"}}}
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{physicalTool("manipulation.pick", &calls)},
		Decider: decider, Observe: loopingObserver(),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
	}.Run(context.Background(), "拿起杯子")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(calls) != 0 {
		t.Fatalf("calls = %v: no scope means nothing was approved", calls)
	}
	if len(outcome.Rounds) == 0 || outcome.Rounds[0].Verdict != actionloop.VerdictOutsideScope {
		t.Fatalf("rounds = %+v", outcome.Rounds)
	}
}

// And an empty scope is an empty scope, not a nil one.
func TestAnEmptyScopeAdmitsNothing(t *testing.T) {
	var calls []string
	decider := &scriptedDecider{decisions: []actionloop.Decision{{Tool: "manipulation.pick"}}}
	_, err := actionloop.Loop{
		Tools:   []actionloop.Tool{physicalTool("manipulation.pick", &calls)},
		Decider: decider, Observe: loopingObserver(),
		Scope:   actionloop.ScopeOf(),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
	}.Run(context.Background(), "拿起杯子")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if len(calls) != 0 {
		t.Fatalf("calls = %v", calls)
	}
}

// Read-only and local calls are not scoped: they change nothing in the world, and
// a loop that cannot look cannot decide.
func TestLookingIsNotScoped(t *testing.T) {
	var calls []string
	decider := &scriptedDecider{decisions: []actionloop.Decision{
		{Tool: "observe_scene"}, {Tool: "navigation.get_pose"}, {Done: true},
	}}
	tools := []actionloop.Tool{
		readOnlyTool("observe_scene", &calls),
		{Name: "navigation.get_pose", SafetyLevel: skills.SafetyLocal,
			Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				calls = append(calls, "navigation.get_pose")
				return actionloop.Result{Success: true}, nil
			}},
	}
	outcome, err := actionloop.Loop{
		Tools: tools, Decider: decider, Observe: loopingObserver(),
		Scope: actionloop.ScopeOf("manipulation.pick"),
	}.Run(context.Background(), "先看一眼")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if strings.Join(calls, ",") != "observe_scene,navigation.get_pose" {
		t.Fatalf("calls = %v: looking must not require an approval scope", calls)
	}
	if !outcome.Completed {
		t.Fatalf("outcome = %+v", outcome)
	}
}

// --- Calls: "the loop returned" is not "something was called" ----------------

func TestCallsCountsTheCallsThatWereDispatched(t *testing.T) {
	var calls []string
	outcome, err := actionloop.Loop{
		Tools: []actionloop.Tool{readOnlyTool("observe_scene", &calls), readOnlyTool("read_pose", &calls)},
		Decider: &scriptedDecider{decisions: []actionloop.Decision{
			{Tool: "observe_scene"}, {Tool: "read_pose"}, {Done: true},
		}},
		Observe: loopingObserver(),
	}.Run(context.Background(), "先看一眼")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if outcome.Calls != 2 {
		t.Fatalf("Calls = %d, want 2 (rounds = %+v)", outcome.Calls, outcome.Rounds)
	}
}

// A run that only ever refused must not report a call. A caller that used "no
// error" as the signal would report a blocked run as an action taken, which for
// this system means telling an operator a robot moved when it did not.
func TestABlockedRunReportsNoCalls(t *testing.T) {
	var calls []string
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", &calls)},
		Decider: &scriptedDecider{decisions: []actionloop.Decision{{Blocked: "做不到"}}},
		Observe: loopingObserver(),
	}.Run(context.Background(), "先看一眼")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if outcome.Calls != 0 {
		t.Fatalf("Calls = %d on a blocked run", outcome.Calls)
	}
	if calls != nil {
		t.Fatalf("calls = %v", calls)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v", outcome)
	}
}

// "Done" with nothing behind it is the model's claim, and it is not a call.
func TestDeclaringDoneWithoutCallingAnythingReportsNoCalls(t *testing.T) {
	var calls []string
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", &calls)},
		Decider: &scriptedDecider{decisions: []actionloop.Decision{{Done: true}}},
		Observe: loopingObserver(),
	}.Run(context.Background(), "先看一眼")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if outcome.Calls != 0 {
		t.Fatalf("Calls = %d, want 0", outcome.Calls)
	}
}

// The completion reason is the decider's own sentence when it gave one.
//
// A fixed sentence here said "the model declared the goal met" for every
// completion, including ones the deterministic single-tool decider decided on a
// deployment with no model at all. It reached the task ledger as the reason for a
// recovery, where it read as a model's judgement — a claim about who decided, made
// by a layer that cannot know.
func TestTheCompletionReasonComesFromTheDecider(t *testing.T) {
	outcome, err := actionloop.Loop{
		Tools: []actionloop.Tool{readOnlyTool("observe_scene", new([]string))},
		Decider: &scriptedDecider{decisions: []actionloop.Decision{
			{Tool: "observe_scene"},
			{Done: true, Reason: "唯一声明的工具已成功调用，没有别的工具可选"},
		}},
		Observe: loopingObserver(),
	}.Run(context.Background(), "先看一眼")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if outcome.Reason != "唯一声明的工具已成功调用，没有别的工具可选" {
		t.Fatalf("reason = %q", outcome.Reason)
	}
}

// And when the decider gives no reason, the fallback names the decider rather than
// a model — the loop knows it has a decider and not what kind.
func TestTheCompletionReasonFallbackDoesNotClaimAModel(t *testing.T) {
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{readOnlyTool("observe_scene", new([]string))},
		Decider: &scriptedDecider{decisions: []actionloop.Decision{{Done: true}}},
		Observe: loopingObserver(),
	}.Run(context.Background(), "先看一眼")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if strings.Contains(outcome.Reason, "模型") {
		t.Fatalf("reason = %q: the loop claimed a model decided this", outcome.Reason)
	}
	if outcome.Reason == "" {
		t.Fatal("no completion reason was recorded")
	}
}

// A refused scope check happens before any dispatch, so it is not a call either.
func TestARefusedScopeCheckReportsNoCalls(t *testing.T) {
	var calls []string
	tool := readOnlyTool("observe_scene", &calls)
	tool.SafetyLevel = skills.SafetyPhysical
	outcome, err := actionloop.Loop{
		Tools:   []actionloop.Tool{tool},
		Decider: &scriptedDecider{decisions: []actionloop.Decision{{Tool: "observe_scene"}}},
		Observe: loopingObserver(),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
	}.Run(context.Background(), "先看一眼")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if outcome.Calls != 0 {
		t.Fatalf("Calls = %d on an out-of-scope refusal", outcome.Calls)
	}
	if calls != nil {
		t.Fatalf("calls = %v", calls)
	}
}
