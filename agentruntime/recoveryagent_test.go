package agentruntime_test

import (
	"context"
	"errors"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// The recovery agent proposes and never acts, plans only from a catalog, and
// records every step of its investigation.
//
// The tests are grouped by which of those three claims they hold up. The third
// is the newest requirement and the one with the least prior art here: an
// operator being asked to approve a recovery action is entitled to see the road
// to it, so the trail is asserted field by field rather than "it is non-empty".

// recoveryFinding mirrors a real ANOMALY_COMPONENT_FAULT finding, including the
// robot's own fault code in Facts.
//
// The Facts matter: a finding is named in the AGENT vocabulary
// (ANOMALY_COMPONENT_FAULT) while the recovery catalog is keyed by what actually
// failed (NAV_MAP_NOT_READY). A test helper that omitted the fault code would
// exercise a finding shape that never occurs, and would pass while the planner
// matched nothing.
func recoveryFinding() agentruntime.Finding {
	return agentruntime.Finding{
		TaskID: "task-1", Code: "ANOMALY_COMPONENT_FAULT", Severity: agentruntime.SeverityCritical,
		Component: "chassis", Message: "chassis 报告 NAV_MAP_NOT_READY：no active map is loaded",
		Evidence: []string{"obs-1"},
		Facts: map[string]any{
			"faultCode": "NAV_MAP_NOT_READY", "moduleId": "chassis",
			"faultSeverity": "blocked", "remedy": "operator_assist",
		},
	}
}

// --- claim 1: it proposes, it does not act ----------------------------------

// The structural half: no field is a way to reach the robot.
//
// It is an allowlist so a new field fails here and forces the question, which is
// the same guard the observing agent uses.
func TestRecoveryAgentHoldsNoExecutionPort(t *testing.T) {
	allowed := map[string]bool{
		"Catalog":   true, // the list of what may be proposed
		"ReadFacts": true, // a read-only evidence source
		"Model":     true, // produces a plan, not an action
		"Publish":   true, // emits events
		// Writes the plan into the store the console reads. It carries values, not
		// authority: it cannot reach the robot, and a plan for a robot-level
		// finding has no ledger row to sit in.
		"RememberPlan": true,
		"Now":          true,
		"mu":           true,
		"proposals":    true,
		"stopped":      true,
	}
	fields := reflect.TypeOf(agentruntime.NewRecoveryAgent(nil)).Elem()
	for index := 0; index < fields.NumField(); index++ {
		field := fields.Field(index)
		if !allowed[field.Name] {
			t.Fatalf("RecoveryAgent has a new field %q of type %s.\n"+
				"This agent proposes; it must hold no execution port.", field.Name, field.Type)
		}
	}
}

func TestRecoveryAgentRefusesToExecute(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	if _, err := agent.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: "task-1"}); !errors.Is(err, agentcontract.ErrNotExecutable) {
		t.Fatalf("execute error = %v, want ErrNotExecutable", err)
	}
}

func TestRecoveryAgentIsReadOnly(t *testing.T) {
	permission := agentruntime.NewRecoveryAgent(nil).Permissions()
	if !permission.ReadOnly || permission.MayMutateWorld() || permission.MayMutateTaskState() {
		t.Fatalf("recovery agent permission = %#v, want read-only", permission)
	}
}

// --- claim 2: only the catalog ---------------------------------------------

// A plan naming something outside the catalog is refused, and the refusal ends
// the plan rather than being skipped: a proposer whose model of what is allowed
// is wrong should not have the rest of its reasoning acted on.
func TestAPlanOutsideTheCatalogIsRefused(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Diagnosis: "尝试一个不存在的动作", Confidence: 0.9,
		Steps: []agentcontract.RecoveryStep{
			{Action: "nav.read-map"},
			{Action: "robot.levitate"}, // not in the catalog
		},
	}}
	published := capturePlans(agent)

	plan := agent.Recover(context.Background(), recoveryFinding())
	if plan == nil {
		t.Fatal("no plan published")
	}
	if plan.Verdict != agentcontract.VerdictEscalate {
		t.Fatalf("verdict = %q, want ESCALATE when the plan was invalid", plan.Verdict)
	}
	if !strings.Contains(plan.EscalateReason, "robot.levitate") {
		t.Fatalf("escalation does not name the refused action: %q", plan.EscalateReason)
	}
	if len(plan.Steps) != 0 {
		t.Fatalf("steps = %#v, want none: a partly-invalid plan is not partially executed", plan.Steps)
	}
	_ = published
}

// A never-automatic action stays in the catalog so it can be refused by name,
// with the catalog's own stated reason rather than a generic refusal.
func TestANeverAutomaticActionIsRefusedByName(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Diagnosis: "想代按急停复位", Confidence: 0.8,
		Steps: []agentcontract.RecoveryStep{{Action: "estop.release"}},
	}}

	plan := agent.Recover(context.Background(), recoveryFinding())
	if plan.Verdict != agentcontract.VerdictEscalate {
		t.Fatalf("verdict = %q, want ESCALATE", plan.Verdict)
	}
	// The reason must be the catalog's, which explains WHY rather than only that
	// it is forbidden.
	if !strings.Contains(plan.EscalateReason, "急停") || !strings.Contains(plan.EscalateReason, "物理上做不到") {
		t.Fatalf("escalation reason = %q, want the catalog's own explanation", plan.EscalateReason)
	}
}

// A repeated action would run twice, which for a write is a second physical
// action nobody asked for.
func TestADuplicateStepIsDropped(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Diagnosis: "重复动作", Confidence: 0.9,
		Steps: []agentcontract.RecoveryStep{
			{Action: "nav.read-map"}, {Action: "nav.read-map"},
		},
	}}
	plan := agent.Recover(context.Background(), recoveryFinding())

	count := 0
	for _, step := range plan.Steps {
		if step.Action == "nav.read-map" {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("nav.read-map appears %d times, want 1", count)
	}
}

// Every proposed step carries its own approval requirement, so a reader of the
// plan does not have to look it up to know what they are being asked to approve.
func TestEveryStepCarriesItsApprovalRequirement(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Diagnosis: "需要写动作", Confidence: 0.9,
		Steps: []agentcontract.RecoveryStep{{Action: "map.re-survey"}},
	}}
	plan := agent.Recover(context.Background(), recoveryFinding())

	var write *agentcontract.RecoveryStep
	for index := range plan.Steps {
		if plan.Steps[index].Action == "map.re-survey" {
			write = &plan.Steps[index]
		}
	}
	if write == nil {
		t.Fatalf("the write action is missing from the plan: %#v", plan.Steps)
	}
	if !write.RequiresApproval {
		t.Fatal("a bounded write must be marked as needing approval")
	}
	for _, step := range plan.Steps {
		if step.Risk == string(agentruntime.RiskReadOnly) && step.RequiresApproval {
			t.Fatalf("read-only step %s is marked as needing approval", step.Action)
		}
	}
}

// --- claim 3: the investigation is recorded ---------------------------------

// The trail must name the stores it read, with the real table names, so an
// operator can check the claim against the schema. "the task store" is not
// reviewable; "sqlite:task_events" is.
func TestTheTrailNamesEveryStoreItRead(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.ReadFacts = func(context.Context, string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		return agentruntime.RecoveryFacts{
				TaskID: "task-1", TaskState: "RECOVERABLE_FAILURE",
				FaultCodes: []string{"NAV_MAP_NOT_READY"}, FaultSeverity: "blocked",
			}, []agentruntime.TrailStep{
				{
					Kind: agentruntime.TrailQuery, Name: "tasks.read",
					Summary: "读取任务当前状态",
					Source:  agentruntime.DataSource{Kind: "sqlite", Table: "tasks", Query: "id = task-1"},
					Rows:    1, Findings: map[string]any{"state": "RECOVERABLE_FAILURE"},
				},
				{
					Kind: agentruntime.TrailQuery, Name: "step_runs.read",
					Summary: "读取该任务的执行记录",
					Source:  agentruntime.DataSource{Kind: "sqlite", Table: "step_runs", Query: "task_id = task-1"},
					Rows:    3,
				},
			}, nil
	}

	plan := agent.Recover(context.Background(), recoveryFinding())
	trail := plan.Trail
	if trail == nil {
		t.Fatal("the plan carries no investigation trail")
	}
	steps, _ := trail["steps"].([]any)
	if len(steps) < 4 {
		t.Fatalf("trail has %d steps, want at least the catalog read, the two store reads and the decision", len(steps))
	}

	// Every query step must name a store and a table.
	sawTables := map[string]bool{}
	for _, raw := range steps {
		step, _ := raw.(map[string]any)
		if step["kind"] != string(agentruntime.TrailQuery) {
			continue
		}
		detail, ok := step["sourceDetail"].(map[string]any)
		if !ok {
			t.Fatalf("a query step does not name its store: %#v", step)
		}
		if detail["kind"] == nil || detail["table"] == nil {
			t.Fatalf("a query step does not name kind and table: %#v", detail)
		}
		sawTables[detail["table"].(string)] = true
		if step["summary"] == nil || step["summary"] == "" {
			t.Fatalf("a query step has no summary: %#v", step)
		}
	}
	for _, want := range []string{"tasks", "step_runs"} {
		if !sawTables[want] {
			t.Fatalf("the trail does not record reading %s; tables seen: %#v", want, sawTables)
		}
	}

	// The human-readable rendering must be present too, because the console shows
	// that string rather than reassembling one from the detail map.
	found := false
	for _, raw := range steps {
		step, _ := raw.(map[string]any)
		if source, ok := step["source"].(string); ok && strings.Contains(source, "step_runs") {
			found = true
		}
	}
	if !found {
		t.Fatal("no step rendered its source as a readable string")
	}
}

// A step that failed is recorded, not omitted: an investigation that hid its
// failures would look more certain than it was.
func TestAFailedReadIsRecordedInTheTrail(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.ReadFacts = func(context.Context, string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		return agentruntime.RecoveryFacts{}, nil, errors.New("database is locked")
	}
	plan := agent.Recover(context.Background(), recoveryFinding())

	steps, _ := plan.Trail["steps"].([]any)
	found := false
	for _, raw := range steps {
		step, _ := raw.(map[string]any)
		if text, ok := step["error"].(string); ok && strings.Contains(text, "database is locked") {
			found = true
		}
	}
	if !found {
		t.Fatalf("a failed read was not recorded: %#v", steps)
	}
}

// A planner that failed is recorded too, because the plan that follows was formed
// without it and the reader must be able to see that.
func TestAFailedPlannerIsRecordedAndFallsBack(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.Model = stubPlanner{err: errors.New("model unreachable")}
	plan := agent.Recover(context.Background(), recoveryFinding())

	steps, _ := plan.Trail["steps"].([]any)
	found := false
	for _, raw := range steps {
		step, _ := raw.(map[string]any)
		if step["kind"] == string(agentruntime.TrailModel) {
			if text, ok := step["error"].(string); ok && strings.Contains(text, "model unreachable") {
				found = true
			}
		}
	}
	if !found {
		t.Fatalf("the failed planner was not recorded: %#v", steps)
	}
	// And the plan still exists, from the deterministic route.
	if plan.Verdict != agentcontract.VerdictPlan {
		t.Fatalf("verdict = %q, want a plan from the deterministic fallback", plan.Verdict)
	}
	if len(plan.Steps) == 0 {
		t.Fatal("the fallback produced no steps")
	}
}

// The catalog the proposer chose from is published with the plan: a plan can only
// be reviewed against the options that existed when it was made.
func TestTheTrailRecordsWhatTheProposerWasAllowedToChooseFrom(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	plan := agent.Recover(context.Background(), recoveryFinding())

	if plan.Catalog == nil {
		t.Fatal("the plan does not publish the catalog it chose from")
	}
	actions, _ := plan.Catalog["actions"].([]any)
	if len(actions) == 0 {
		t.Fatalf("catalog payload has no actions: %#v", plan.Catalog)
	}
	// The never-automatic entries must be visible, so a reader can see that the
	// refusal was a decision rather than an absence.
	sawRefusal := false
	for _, raw := range actions {
		action, _ := raw.(map[string]any)
		if action["refusal"] != nil && action["refusal"] != "" {
			sawRefusal = true
		}
	}
	if !sawRefusal {
		t.Fatal("the published catalog shows no refused action, so a reader cannot see the boundary")
	}
}

// The source tells a reader whether a table produced the plan or a model did.
func TestTheTrailNamesTheProposer(t *testing.T) {
	deterministic := agentruntime.NewRecoveryAgent(nil)
	if plan := deterministic.Recover(context.Background(), recoveryFinding()); plan.Source != "deterministic" {
		t.Fatalf("source = %q, want deterministic", plan.Source)
	}

	withModel := agentruntime.NewRecoveryAgent(nil)
	withModel.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Diagnosis: "模型建议", Confidence: 0.7,
		Steps: []agentcontract.RecoveryStep{{Action: "map.read-conflicts"}},
	}}
	if plan := withModel.Recover(context.Background(), recoveryFinding()); plan.Source != "deterministic+model" {
		t.Fatalf("source = %q, want deterministic+model", plan.Source)
	}
}

// --- reconciliation outranks every plan -------------------------------------

// An unknown physical outcome means the world may already have changed. Nothing
// that changes it may run until that is established, and this rule outranks a
// model's plan.
//
// The form changed and the property did not. It used to escalate with no steps at
// all; that refused the model's re-survey (correct) and also refused the read-only
// observation that performs the reconciliation (not correct), so a system that
// could detect an unreconciled state could never leave it without a person. The
// assertions below are therefore about *what kind* of step survives rather than
// how many: the model's mutating step must be gone, and the reconciliation read
// must be present, because the contract asks for evidence — it does not ask for a
// person to hold the thermometer.
func TestReconciliationOutranksAModelPlan(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.ReadFacts = func(context.Context, string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		return agentruntime.RecoveryFacts{
			TaskID: "task-1",
			UncertainSteps: []agentcontract.StepRecord{
				{TaskID: "task-1", StepID: "pick", Status: "STARTED"},
			},
		}, nil, nil
	}
	agent.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Diagnosis: "模型想直接重扫地图", Confidence: 0.95,
		Steps: []agentcontract.RecoveryStep{{Action: "map.re-survey"}},
	}}

	plan := agent.Recover(context.Background(), recoveryFinding())

	// The model's mutating proposal is gone, and gone because the model was never
	// consulted — not because something filtered its output afterwards.
	for _, step := range plan.Steps {
		if step.Action == "map.re-survey" {
			t.Fatalf("the model's mutating step survived reconciliation: %#v", plan.Steps)
		}
	}
	if plan.Source != "deterministic" {
		t.Fatalf("source = %q: the model was consulted before reconciliation", plan.Source)
	}
	// What remains is the read-only reconciliation, and it is actionable rather
	// than escalated, because taking that read is what the contract asks for.
	if plan.Verdict != agentcontract.VerdictPlan {
		t.Fatalf("verdict = %q, want PLAN carrying read-only reconciliation steps", plan.Verdict)
	}
	if len(plan.Steps) == 0 {
		t.Fatal("no reconciliation step was proposed; the system cannot leave the unreconciled state")
	}
	for _, step := range plan.Steps {
		if step.RequiresApproval {
			t.Fatalf("step %s requires approval; reconciliation must be something the system can do alone", step.Action)
		}
		if step.Risk != string(agentruntime.RiskReadOnly) {
			t.Fatalf("step %s has risk %q, want read_only", step.Action, step.Risk)
		}
	}
	if !strings.Contains(plan.Diagnosis, "对账") {
		t.Fatalf("diagnosis = %q, want it to name reconciliation", plan.Diagnosis)
	}
}

// When the catalog has no read-only match there is genuinely nothing the system
// can do alone, and that is still an escalation.
func TestReconciliationWithoutAReadOnlyMatchStillEscalates(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.ReadFacts = func(context.Context, string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		return agentruntime.RecoveryFacts{
			TaskID: "task-1",
			UncertainSteps: []agentcontract.StepRecord{
				{TaskID: "task-1", StepID: "pick", Status: "STARTED"},
			},
		}, nil, nil
	}
	agent.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Confidence: 0.95,
		Steps:      []agentcontract.RecoveryStep{{Action: "map.re-survey"}},
	}}

	// A finding whose shapes match nothing in the catalog.
	finding := recoveryFinding()
	finding.Code = "ANOMALY_SOMETHING_UNHEARD_OF"
	finding.Component = "nowhere"
	finding.Facts = nil

	plan := agent.Recover(context.Background(), finding)
	if plan.Verdict != agentcontract.VerdictEscalate {
		t.Fatalf("verdict = %q, want ESCALATE", plan.Verdict)
	}
	if len(plan.Steps) != 0 {
		t.Fatalf("steps = %#v, want none", plan.Steps)
	}
}

// A store that cannot answer the reconciliation question is not an answer.
//
// This is the same rule as above, applied to the case that used to slip through
// it: the execution store was unreachable or could not list a task's steps, the
// reader returned no uncertain steps, and "I could not check" was therefore
// indistinguishable from "the world is settled". A store outage must not be able
// to switch off the one rule that stops a robot from redoing a step that may
// already have happened.
func TestRecoveryStopsWhenTheReconciliationQuestionCannotBeAnswered(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.ReadFacts = func(context.Context, string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		return agentruntime.RecoveryFacts{
			TaskID: "task-1",
			// No uncertain steps: the read found nothing because it never ran.
			ReconciliationUnavailable: true,
			ReconciliationError:       "step runs are not reachable",
		}, nil, nil
	}
	agent.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Diagnosis: "模型想直接重试那一步", Confidence: 0.95,
		Steps: []agentcontract.RecoveryStep{{Action: "task.retry-step"}},
	}}

	plan := agent.Recover(context.Background(), recoveryFinding())
	if plan.Verdict != agentcontract.VerdictEscalate {
		t.Fatalf("verdict = %q, want ESCALATE: an unanswerable question is not a settled world", plan.Verdict)
	}
	if len(plan.Steps) != 0 {
		t.Fatalf("steps = %#v, want none while the world's state is unestablished", plan.Steps)
	}
	// The escalation has to name what could not be read. "Something went wrong"
	// sends an operator looking through the whole stack; the failing read sends
	// them to the store that is down.
	if !strings.Contains(plan.EscalateReason, "step runs are not reachable") {
		t.Fatalf("escalation reason = %q, want the failed read named", plan.EscalateReason)
	}
	// And the reasoning is in the trail, so a reader can see that the plan was
	// stopped by an unanswerable question rather than by finding a problem.
	found := false
	for _, step := range plan.Trail["steps"].([]any) {
		record, _ := step.(map[string]any)
		if record["name"] == "rule.reconcile-unknown" {
			found = true
		}
	}
	if !found {
		t.Fatalf("the trail does not record which rule stopped the plan: %#v", plan.Trail)
	}
}

// --- verdicts are first-class -------------------------------------------------

// "I cannot fix this" is an answer, not a failure. A planner that says so must
// have its reason carried through verbatim.
func TestEscalationCarriesThePlannersReason(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.Model = stubPlanner{plan: agentruntime.RecoveryPlan{
		Escalate: true, Confidence: 0.9,
		EscalateReason: "工作台高度与标定不符，需要人重新标定工作台",
	}}
	plan := agent.Recover(context.Background(), recoveryFinding())
	if plan.Verdict != agentcontract.VerdictEscalate {
		t.Fatalf("verdict = %q, want ESCALATE", plan.Verdict)
	}
	if !strings.Contains(plan.EscalateReason, "重新标定工作台") {
		t.Fatalf("escalation reason = %q, want the planner's own reason", plan.EscalateReason)
	}
}

// A finding with no matching shape still produces a plan if the catalog names any
// read-only action for it — but when nothing matches, the honest answer is to ask
// for a person rather than to invent a step.
func TestNoUsableActionEscalatesRatherThanInventing(t *testing.T) {
	catalog, err := agentruntime.NewRecoveryCatalog(agentruntime.RecoveryAction{
		ID: "only.write", Summary: "唯一的动作是写操作", Risk: agentruntime.RiskBoundedWrite,
	})
	if err != nil {
		t.Fatal(err)
	}
	agent := agentruntime.NewRecoveryAgent(catalog)
	// Nothing matched from the catalog's shapes, and the model is absent, so the
	// deterministic route has no read-only step to offer.
	plan := agent.Recover(context.Background(), recoveryFinding())
	if plan.Verdict != agentcontract.VerdictEscalate {
		t.Fatalf("verdict = %q, want ESCALATE when no action applies", plan.Verdict)
	}
	if len(plan.Steps) != 0 {
		t.Fatalf("steps = %#v, want none", plan.Steps)
	}
}

// A finding whose shapes match nothing in the catalog escalates rather than
// inventing a step.
//
// This is the honest answer and it is common: a failure nobody has catalogued is
// exactly the case a person should see. A planner that always produced a step
// would be producing steps it cannot justify.
func TestAnUnmatchedShapeEscalates(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	finding := recoveryFinding()
	finding.Code = "ANOMALY_NOBODY_HAS_SEEN"
	finding.Facts = map[string]any{"faultCode": "SOME_UNCATALOGUED_FAULT"}

	plan := agent.Recover(context.Background(), finding)
	if plan.Verdict != agentcontract.VerdictEscalate {
		t.Fatalf("verdict = %q, want ESCALATE for an uncatalogued failure", plan.Verdict)
	}
	if len(plan.Steps) != 0 {
		t.Fatalf("steps = %#v, want none", plan.Steps)
	}
	// The trail must show what was searched, so a reader can see that the
	// escalation came from an absence and not from a failure to look.
	steps, _ := plan.Trail["steps"].([]any)
	found := false
	for _, raw := range steps {
		step, _ := raw.(map[string]any)
		if step["name"] != "route.deterministic" {
			continue
		}
		findings, _ := step["findings"].(map[string]any)
		if searched, ok := findings["searchedShapes"].([]string); ok && len(searched) > 0 {
			found = true
		}
	}
	if !found {
		t.Fatalf("the trail does not record which shapes were searched: %#v", steps)
	}
}

// A standing condition is planned once, not every tick: re-planning would re-ask
// a model and republish a plan an operator has already read.
func TestAStandingConditionIsPlannedOnce(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	now := time.Unix(1000, 0).UTC()
	agent.Now = func() time.Time { return now }

	if first := agent.Recover(context.Background(), recoveryFinding()); first == nil {
		t.Fatal("the first plan was not produced")
	}
	if second := agent.Recover(context.Background(), recoveryFinding()); second != nil {
		t.Fatal("the same condition was planned again inside the cooldown")
	}
	now = now.Add(3 * time.Minute)
	if third := agent.Recover(context.Background(), recoveryFinding()); third == nil {
		t.Fatal("the condition was not re-planned after the cooldown")
	}
}

// --- health ------------------------------------------------------------------

func TestHealthReportsAnEmptyOrUnboundedCatalog(t *testing.T) {
	empty, err := agentruntime.NewRecoveryCatalog()
	if err != nil {
		t.Fatal(err)
	}
	if health := agentruntime.NewRecoveryAgent(empty).Health(context.Background()); health.Status != agentcontract.HealthUnhealthy {
		t.Fatalf("health with an empty catalog = %#v, want UNHEALTHY", health)
	}

	// A catalog that refuses nothing means nobody wrote down what this robot will
	// never do by itself.
	unbounded, err := agentruntime.NewRecoveryCatalog(agentruntime.RecoveryAction{
		ID: "a", Summary: "只读", Risk: agentruntime.RiskReadOnly,
	})
	if err != nil {
		t.Fatal(err)
	}
	if health := agentruntime.NewRecoveryAgent(unbounded).Health(context.Background()); health.Status != agentcontract.HealthDegraded {
		t.Fatalf("health with no refusals = %#v, want DEGRADED", health)
	}

	if health := agentruntime.NewRecoveryAgent(nil).Health(context.Background()); health.Status != agentcontract.HealthHealthy {
		t.Fatalf("health of the default catalog = %#v, want HEALTHY", health)
	}
}

func TestShutdownIsIdempotentAndStopsPlanning(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	if err := agent.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := agent.Shutdown(context.Background()); err != nil {
		t.Fatalf("second shutdown: %v", err)
	}
	if plan := agent.Recover(context.Background(), recoveryFinding()); plan != nil {
		t.Fatal("a stopped agent still planned")
	}
	if health := agent.Health(context.Background()); health.Status != agentcontract.HealthStopped {
		t.Fatalf("health after shutdown = %#v, want STOPPED", health)
	}
}

// --- helpers -----------------------------------------------------------------

type stubPlanner struct {
	plan  agentruntime.RecoveryPlan
	steps []agentruntime.TrailStep
	err   error
}

func (p stubPlanner) Plan(context.Context, agentruntime.RecoveryRequest) (agentruntime.RecoveryPlan, []agentruntime.TrailStep, error) {
	return p.plan, p.steps, p.err
}

func capturePlans(agent *agentruntime.RecoveryAgent) []agentcontract.Event {
	var published []agentcontract.Event
	agent.Publish = func(_ context.Context, event agentcontract.Event) {
		published = append(published, event)
	}
	return published
}

// sortedIDs is a small helper for diagnostics in failure messages.
func sortedIDs(values []string) []string {
	sorted := append([]string(nil), values...)
	sort.Strings(sorted)
	return sorted
}

// A plan must carry the task it is about, or it goes nowhere.
//
// The durable ledger is per task, and the bridge that persists agent events drops
// an event with no task id. A plan published without one is produced, stored
// nowhere and replayed by nobody — which is exactly what happened the first time
// this was run against a live stack.
func TestAPlanIsAttributedToItsTask(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.ReadFacts = func(context.Context, string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		return agentruntime.RecoveryFacts{TaskID: "task-from-facts"}, nil, nil
	}
	var events []agentcontract.Event
	agent.Publish = func(_ context.Context, event agentcontract.Event) {
		events = append(events, event)
	}

	agent.Recover(context.Background(), recoveryFinding())
	if len(events) != 1 {
		t.Fatalf("published %d events, want 1", len(events))
	}
	if events[0].TaskID != "task-1" {
		t.Fatalf("plan task id = %q, want the task under investigation", events[0].TaskID)
	}
	if events[0].Topic != agentcontract.TopicOpsRecoveryPlan {
		t.Fatalf("topic = %q", events[0].Topic)
	}
}

// A robot-wide finding has no task of its own; the task read from the facts is
// used instead, so the plan still has a home in the ledger rather than being
// dropped.
func TestARobotWidePlanBorrowsTheTaskFromTheFacts(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	agent.ReadFacts = func(context.Context, string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		return agentruntime.RecoveryFacts{TaskID: "task-from-facts"}, nil, nil
	}
	var events []agentcontract.Event
	agent.Publish = func(_ context.Context, event agentcontract.Event) {
		events = append(events, event)
	}

	finding := recoveryFinding()
	finding.TaskID = ""
	agent.Recover(context.Background(), finding)
	if len(events) != 1 || events[0].TaskID != "task-from-facts" {
		t.Fatalf("events = %#v, want the plan attributed to the task from the facts", events)
	}
}

// Two components failing the same way are two problems, and each gets its own
// plan.
//
// The plan used to be keyed on the failure code alone, so the first plan made for
// a code was the plan shown for every later occurrence of it — across different
// components, in one evaluation. The cooldown then suppressed the rest, so the
// system appeared to have considered all eleven failures while it had reasoned
// about one.
func TestTwoComponentsFailingTheSameWayGetTheirOwnPlans(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	var triggers []string
	agent.Publish = func(_ context.Context, event agentcontract.Event) {
		trigger, _ := event.Payload["trigger"].(string)
		triggers = append(triggers, trigger)
	}

	navigation := recoveryFinding()
	navigation.Component = "navigation.navigate"
	navigation.Message = "navigation.navigate 失败：NAV_ROTATION_LIMIT"
	observer := recoveryFinding()
	observer.Component = "observe_scene"
	observer.Message = "observe_scene 失败：NAV_ROTATION_LIMIT"

	first := agent.Recover(context.Background(), navigation)
	second := agent.Recover(context.Background(), observer)

	if first == nil || second == nil {
		t.Fatalf("one of two distinct findings went unplanned: %#v %#v", first, second)
	}
	if first.PlanID == second.PlanID {
		t.Fatalf("two components were given one plan: %q", first.PlanID)
	}
	if first.Trigger == second.Trigger {
		t.Fatalf("two components were filed under one trigger: %q", first.Trigger)
	}
	if len(triggers) != 2 {
		t.Fatalf("published %d plans, want one per finding: %v", len(triggers), triggers)
	}
	if first.Trigger != "ANOMALY_COMPONENT_FAULT@navigation.navigate" {
		t.Fatalf("trigger = %q, want the finding's identity", first.Trigger)
	}
}

// The same finding reported twice inside the cooldown is one investigation.
//
// The rule above must not turn into "plan everything every time": a standing
// condition that is re-found on the next tick is the same condition, and
// re-planning it would re-ask a model and re-announce a plan an operator has
// already read.
func TestTheSameFindingIsNotPlannedTwiceInsideTheCooldown(t *testing.T) {
	agent := agentruntime.NewRecoveryAgent(nil)
	published := 0
	agent.Publish = func(context.Context, agentcontract.Event) { published++ }

	finding := recoveryFinding()
	if agent.Recover(context.Background(), finding) == nil {
		t.Fatal("the first report of a finding was not planned")
	}
	if second := agent.Recover(context.Background(), finding); second != nil {
		t.Fatal("a standing condition was planned again inside the cooldown")
	}
	if published != 1 {
		t.Fatalf("published %d plans for one condition, want 1", published)
	}
}
