package recoveryexec_test

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
)

// scripted decider: returns pre-set decisions in order.
type scripted struct {
	decisions []actionloop.Decision
	requests  []actionloop.Request
	calls     int
}

func (d *scripted) Decide(_ context.Context, request actionloop.Request) (actionloop.Decision, error) {
	d.requests = append(d.requests, request)
	index := d.calls
	d.calls++
	if index >= len(d.decisions) {
		return actionloop.Decision{Done: true, Reason: "做完了"}, nil
	}
	return d.decisions[index], nil
}

// serviceTool builds a robot-service tool that records being called.
func serviceTool(name string, level skills.SafetyLevel, mutates bool, calls *[]string) recoveryexec.Tool {
	return recoveryexec.Tool{
		Name: name, Description: "机器人服务 " + name, Parameters: []string{"parameters"},
		SafetyLevel: level, MutatesWorld: mutates,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			*calls = append(*calls, name)
			return actionloop.Result{Success: true}, nil
		},
	}
}

func observer() recoveryexec.Observer {
	return observerFunc(func(context.Context, string) (actionloop.Observation, error) {
		return actionloop.Observation{Summary: "底盘未加载地图", EvidenceIDs: []string{"fault-1"}}, nil
	})
}

type observerFunc func(context.Context, string) (actionloop.Observation, error)

func (f observerFunc) Observe(ctx context.Context, taskID string) (actionloop.Observation, error) {
	return f(ctx, taskID)
}

// verified is a Verify that always sees the effect.
func verified() recoveryexec.Verify {
	return func(context.Context, agentruntime.RecoveryAction, actionloop.Outcome) (recoveryexec.Verdict, error) {
		return recoveryexec.Verdict{Verified: true, Detail: "地图已启用"}, nil
	}
}

// action finds a real catalog entry by id, so the tests exercise the shipped
// declarations rather than a fixture that could drift from them.
func action(t *testing.T, id string) agentruntime.RecoveryAction {
	t.Helper()
	for _, candidate := range agentruntime.DefaultRecoveryCatalog().Actions() {
		if candidate.ID == id {
			return candidate
		}
	}
	t.Fatalf("no catalog action %q", id)
	return agentruntime.RecoveryAction{}
}

// --- the happy path ----------------------------------------------------------

// An approved bounded write runs the tool the catalog declares for it, and the
// verdict comes from looking afterwards.
func TestAnApprovedActionRunsItsDeclaredToolAndIsVerified(t *testing.T) {
	var calls []string
	decider := &scripted{decisions: []actionloop.Decision{
		{Tool: "mapping.activate", Arguments: map[string]any{"mapId": "scan-1"}, Reason: "加载已保存的地图"},
		{Done: true, Reason: "已加载"},
	}}
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			// Loading a map changes the robot's state, not the physical world. The
			// closure gate is about motion; what confirms a state change is reading
			// the state back, which is the Verifier's job below.
			"mapping.activate": serviceTool("mapping.activate", skills.SafetyLocal, false, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: decider,
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{
		Action: action(t, "map.activate"), PlanID: "plan-1", TaskID: "task-1",
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !result.Executed {
		t.Fatalf("result = %+v", result)
	}
	if strings.Join(calls, ",") != "mapping.activate" {
		t.Fatalf("calls = %v", calls)
	}
	if !result.Verified {
		t.Fatalf("verification was not recorded: %+v", result)
	}
	if result.Verification != "地图已启用" {
		t.Fatalf("verification = %q", result.Verification)
	}
	// The decision record travels with the result, so a reader sees why this tool
	// rather than the alternatives.
	if len(result.Rounds) < 2 || result.Rounds[0].Reason != "加载已保存的地图" {
		t.Fatalf("rounds = %+v", result.Rounds)
	}
}

// --- the ordering is the safety argument -------------------------------------

// A never-automatic action never reaches a tool, whatever anyone approves.
func TestANeverAutomaticActionIsRefusedBeforeAnythingElse(t *testing.T) {
	var calls []string
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{},
		Observer: observer(), Verify: verified(),
		Decider: &scripted{},
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
	}
	for _, id := range []string{"estop.release", "hardware.replug", "calibration.change"} {
		result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, id)})
		if err != nil {
			t.Fatalf("%s: execute: %v", id, err)
		}
		if result.Executed {
			t.Fatalf("%s ran", id)
		}
		// The refusal is the catalog's own sentence: it is the one the plan showed
		// the operator, and a second wording here would be a second answer.
		if result.Reason != action(t, id).Refusal {
			t.Fatalf("%s reason = %q, want the catalog's refusal", id, result.Reason)
		}
	}
	if calls != nil {
		t.Fatalf("calls = %v", calls)
	}
}

// An action whose declared tools are not present is not executable, and it says
// which one is missing rather than failing at the far end.
func TestAnActionWhoseToolIsMissingSaysWhichOne(t *testing.T) {
	executor := recoveryexec.Executor{
		// map.activate declares mapping.activate; this registry has nothing.
		Registry: recoveryexec.MapRegistry{},
		Observer: observer(), Verify: verified(), Decider: &scripted{},
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) {
			t.Fatal("a person was asked to approve an action that cannot run")
			return false, nil
		},
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{
		Action: action(t, "map.activate"),
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if result.Executed {
		t.Fatal("an action with no available tool ran")
	}
	if !strings.Contains(result.Reason, "mapping.activate") {
		t.Fatalf("reason = %q, want it to name the missing tool", result.Reason)
	}
	found := false
	for _, step := range result.Trail {
		if step.Name == "tools.missing" {
			found = true
		}
	}
	if !found {
		t.Fatalf("the trail does not record the missing tool: %+v", result.Trail)
	}
}

// A bounded write without an approver does not run: a deployment that cannot ask
// has not been answered.
func TestABoundedWriteWithoutAnApproverDoesNotRun(t *testing.T) {
	var calls []string
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.activate": serviceTool("mapping.activate", skills.SafetyLocal, false, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: &scripted{},
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.activate")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if result.Executed || calls != nil {
		t.Fatalf("an unapproved bounded write ran: %+v calls=%v", result, calls)
	}
	if !strings.Contains(result.Reason, "批准") {
		t.Fatalf("reason = %q", result.Reason)
	}
}

// And a refusal stops it.
func TestARefusedApprovalStopsTheAction(t *testing.T) {
	var calls []string
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.activate": serviceTool("mapping.activate", skills.SafetyLocal, false, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: &scripted{},
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return false, nil },
	}
	result, _ := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.activate")})
	if result.Executed || calls != nil {
		t.Fatalf("a refused action ran: %+v calls=%v", result, calls)
	}
}

// A read-only action runs without asking. It changes nothing, so there is nothing
// for approval to protect.
func TestAReadOnlyActionRunsWithoutApproval(t *testing.T) {
	var calls []string
	decider := &scripted{decisions: []actionloop.Decision{
		{Tool: "mapping.status"}, {Done: true},
	}}
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.status": serviceTool("mapping.status", skills.SafetyReadOnly, false, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: decider,
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.read-status")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !result.Executed || strings.Join(calls, ",") != "mapping.status" {
		t.Fatalf("result = %+v calls = %v", result, calls)
	}
}

// --- the scope is the approved action, not the model's imagination ------------

// A tool outside the action's declaration is not available to the decider, even
// when the deployment offers it and the decider asks for it.
//
// "Load a saved map" approved `mapping.activate`. It did not approve starting a
// fresh survey, and the model choosing one would be widening the operator's
// consent on its own.
//
// What this test proves is that the out-of-scope tool is never *offered* — the
// first and better line, because a model cannot choose what it cannot see. The
// executor's separate `Scope` bound is deliberately redundant here (the tool list
// is already exactly the declared set), so removing it does not fail this test;
// the mechanism itself is proven in internal/actionloop, where a wider tool list
// and a narrower scope are exercised together.
func TestAToolOutsideTheActionIsRefused(t *testing.T) {
	var calls []string
	decider := &scripted{decisions: []actionloop.Decision{
		{Tool: "mapping.start", Reason: "干脆重新扫一张"},
		{Tool: "mapping.activate", Reason: "那就加载"},
	}}
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.activate": serviceTool("mapping.activate", skills.SafetyLocal, false, &calls),
			// Available on this robot, and not approved for this action.
			"mapping.start": serviceTool("mapping.start", skills.SafetyLocal, true, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: decider,
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
	}
	if _, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.activate")}); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if strings.Join(calls, ",") != "mapping.activate" {
		t.Fatalf("calls = %v: a tool outside the action ran", calls)
	}
	// The model was never offered the out-of-scope tool, which is the stronger
	// form of the same rule: it cannot choose what it cannot see.
	for _, round := range decider.requests[0].Tools {
		if round.Name == "mapping.start" {
			t.Fatal("the out-of-scope tool was offered to the decider")
		}
	}
}

// --- the verdict comes from looking ------------------------------------------

// A recovery that ran but did not take effect is not a success, and it is not
// reported as one.
func TestAnActionThatRanWithoutEffectIsNotVerified(t *testing.T) {
	var calls []string
	decider := &scripted{decisions: []actionloop.Decision{{Tool: "mapping.activate"}, {Done: true}}}
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.activate": serviceTool("mapping.activate", skills.SafetyLocal, false, &calls),
		},
		Observer: observer(), Decider: decider,
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
		Verify: func(context.Context, agentruntime.RecoveryAction, actionloop.Outcome) (recoveryexec.Verdict, error) {
			return recoveryexec.Verdict{Verified: false, Detail: "地图仍未启用"}, nil
		},
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.activate")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !result.Executed {
		t.Fatalf("the action did not run: %+v", result)
	}
	if result.Verified {
		t.Fatalf("an action with no observed effect was verified: %+v", result)
	}
	if result.Verification != "地图仍未启用" {
		t.Fatalf("verification = %q", result.Verification)
	}
}

// No verifier means no claim. Saying "executed but unverified" is honest; saying
// "recovered" would not be.
func TestWithoutAVerifierTheResultIsUnverifiedRatherThanSuccessful(t *testing.T) {
	var calls []string
	decider := &scripted{decisions: []actionloop.Decision{{Tool: "mapping.activate"}, {Done: true}}}
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.activate": serviceTool("mapping.activate", skills.SafetyLocal, false, &calls),
		},
		Observer: observer(), Decider: decider,
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.activate")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if result.Verified {
		t.Fatal("an action with no verifier was reported as verified")
	}
	if !strings.Contains(result.Verification, "无法确认") {
		t.Fatalf("verification = %q", result.Verification)
	}
}

// --- no decider ---------------------------------------------------------------

// A deployment with no decider does not invent a call. The action ends with a
// sentence rather than an error about a nil interface.
func TestWithoutADeciderNothingRunsAndTheReasonIsStated(t *testing.T) {
	var calls []string
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.status": serviceTool("mapping.status", skills.SafetyReadOnly, false, &calls),
		},
		Observer: observer(), Verify: verified(),
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.read-status")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if calls != nil {
		t.Fatalf("calls = %v: nothing may run without a decider", calls)
	}
	if !strings.Contains(result.Reason, "没有配置决策器") {
		t.Fatalf("reason = %q", result.Reason)
	}
}

// --- the declaration itself --------------------------------------------------

// Every executable action names the tools it may call.
//
// A catalogue entry without tools cannot be executed without inventing a mapping,
// which is exactly what this design refuses to do — so the gap is caught in the
// catalogue rather than at the moment somebody approves it.
func TestEveryExecutableActionDeclaresItsTools(t *testing.T) {
	for _, entry := range agentruntime.DefaultRecoveryCatalog().Actions() {
		if !entry.Executable() {
			if len(entry.Tools) > 0 {
				t.Errorf("%s is never automatic and should not declare tools: %v", entry.ID, entry.Tools)
			}
			continue
		}
		if len(entry.Tools) == 0 {
			t.Errorf("%s is executable but declares no tools", entry.ID)
		}
		for _, tool := range entry.Tools {
			if strings.TrimSpace(tool) == "" {
				t.Errorf("%s declares an empty tool name", entry.ID)
			}
		}
	}
}

// A never-automatic action declares no tools, so there is nothing for a decider
// to reach even if the refusal were bypassed.
func TestNeverAutomaticActionsDeclareNoTools(t *testing.T) {
	catalog := agentruntime.DefaultRecoveryCatalog()
	refused := 0
	for _, entry := range catalog.Actions() {
		if entry.Risk != agentruntime.RiskNeverAutomatic {
			continue
		}
		refused++
		if len(entry.Tools) != 0 {
			t.Errorf("%s is never automatic yet declares tools %v", entry.ID, entry.Tools)
		}
		if entry.Refusal == "" {
			t.Errorf("%s is refused without a reason", entry.ID)
		}
	}
	if refused != 3 {
		t.Fatalf("expected the three named refusals, found %d", refused)
	}
}

// The catalog is the boundary: a decider cannot name an action that is not in it,
// because the action is chosen before the executor is called.
func TestAnActionNotInTheCatalogCannotBeConstructedForExecution(t *testing.T) {
	invented := agentruntime.RecoveryAction{ID: "robot.take_over", Summary: "接管机器人"}
	if invented.Executable() {
		t.Fatal("an invented action reports itself as executable")
	}
	if _, err := agentruntime.NewRecoveryCatalog(invented); err == nil {
		// The catalogue accepts it as an entry, which is fine — what matters is
		// that the executor refuses it, because it carries no tools and no risk.
		t.Log("the catalogue accepted an entry with no risk, as expected")
	}
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{},
		Observer: observer(), Verify: verified(), Decider: &scripted{},
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: invented})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if result.Executed {
		t.Fatal("an action with no risk class ran")
	}
}

// --- multi-tool actions are sequenced by the decider --------------------------

// A re-survey declares five tools and the model decides which and in what order.
// Nothing in this package knows the right order, which is the point: it is not
// written down here.
func TestAMultiToolActionIsSequencedByTheDecider(t *testing.T) {
	var calls []string
	decider := &scripted{decisions: []actionloop.Decision{
		{Tool: "mapping.start", Reason: "开始扫描"},
		{Tool: "mapping.move", Reason: "走一圈"},
		{Tool: "mapping.finish", Reason: "收尾"},
		{Done: true, Reason: "新地图已产出"},
	}}
	registry := recoveryexec.MapRegistry{}
	for _, name := range []string{"mapping.start", "mapping.move", "mapping.finish", "mapping.stop_motion", "mapping.cancel"} {
		registry[name] = serviceTool(name, skills.SafetyLocal, false, &calls)
	}
	executor := recoveryexec.Executor{
		Registry: registry, Observer: observer(), Verify: verified(), Decider: decider,
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.re-survey")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !result.Executed {
		t.Fatalf("result = %+v", result)
	}
	if strings.Join(calls, ",") != "mapping.start,mapping.move,mapping.finish" {
		t.Fatalf("calls = %v, want the order the decider chose", calls)
	}
	// The abort tools were offered and not chosen, which is the honest record:
	// they were available options, not hidden ones.
	offered := map[string]bool{}
	for _, tool := range decider.requests[0].Tools {
		offered[tool.Name] = true
	}
	if !offered["mapping.cancel"] || !offered["mapping.stop_motion"] {
		t.Fatalf("the abort tools were not offered: %v", offered)
	}
}

// --- clock and cancellation ---------------------------------------------------

func TestExecuteIsBoundedByTheContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	var calls []string
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.status": serviceTool("mapping.status", skills.SafetyReadOnly, false, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: &scripted{},
	}
	if _, err := executor.Execute(ctx, recoveryexec.Request{Action: action(t, "map.read-status")}); !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	if calls != nil {
		t.Fatalf("calls = %v", calls)
	}
}

func TestStepsAreTimestamped(t *testing.T) {
	var calls []string
	decider := &scripted{decisions: []actionloop.Decision{{Tool: "mapping.status"}, {Done: true}}}
	moment := time.Date(2026, 9, 17, 12, 0, 0, 0, time.UTC)
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.status": serviceTool("mapping.status", skills.SafetyReadOnly, false, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: decider,
		Now: func() time.Time { return moment },
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.read-status")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if len(result.Trail) == 0 {
		t.Fatal("no trail was recorded")
	}
	for _, step := range result.Trail {
		if step.At.IsZero() {
			t.Fatalf("a trail step has no timestamp: %+v", step)
		}
	}
}

// --- who approved -------------------------------------------------------------

// A person pressing the button is the approval, and it is recorded as a person's
// act rather than as a port that answered yes.
func TestAnOperatorInitiatedActionDoesNotAskAPort(t *testing.T) {
	var calls []string
	decider := &scripted{decisions: []actionloop.Decision{{Tool: "mapping.activate"}, {Done: true}}}
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.activate": serviceTool("mapping.activate", skills.SafetyLocal, false, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: decider,
		// No approver configured at all.
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{
		Action: action(t, "map.activate"), OperatorApproved: true,
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !result.Executed {
		t.Fatalf("an operator-initiated bounded write did not run: %+v", result)
	}
	// The trail names which of the two happened. "A check passed" would not answer
	// "who approved this", and that question has to be answerable from the record.
	granted := ""
	for _, step := range result.Trail {
		if strings.HasPrefix(step.Name, "approval.") {
			granted = step.Name + " " + step.Detail
		}
	}
	if !strings.Contains(granted, "approval.operator") {
		t.Fatalf("the record does not show that a person decided: %q", granted)
	}
	if !strings.Contains(granted, "操作者") {
		t.Fatalf("the record does not say a person approved: %q", granted)
	}
}

// And an initiator that is not a person still has to ask. A timer or an automatic
// rule has nobody at the keyboard, which is exactly when the port matters.
func TestANonOperatorInitiatedActionStillAsks(t *testing.T) {
	var calls []string
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{
			"mapping.activate": serviceTool("mapping.activate", skills.SafetyLocal, false, &calls),
		},
		Observer: observer(), Verify: verified(), Decider: &scripted{},
		// No approver, and nobody pressed anything.
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.activate")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if result.Executed || calls != nil {
		t.Fatalf("an unapproved bounded write ran: %+v", result)
	}
}

// The flag does not widen anything else: a never-automatic action is still refused
// however it was started.
func TestOperatorInitiationDoesNotOverrideTheCatalog(t *testing.T) {
	executor := recoveryexec.Executor{
		Registry: recoveryexec.MapRegistry{}, Observer: observer(), Verify: verified(), Decider: &scripted{},
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{
		Action: action(t, "estop.release"), OperatorApproved: true,
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if result.Executed {
		t.Fatal("operator initiation bypassed the catalog's refusal")
	}
	if result.Reason != action(t, "estop.release").Refusal {
		t.Fatalf("reason = %q", result.Reason)
	}
}
