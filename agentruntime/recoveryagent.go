package agentruntime

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// RecoveryAgentVersion identifies this agent's behaviour.
const RecoveryAgentVersion = "1"

// The recovery agent: it investigates a diagnosed problem and proposes what to do.
//
// # It proposes; it does not act
//
// The type holds no execution port, no task service and no robot client. It
// cannot run a recovery action even if it decided to — the same structural rule
// the observing agent follows, for the same reason: "an agent that only advises"
// should be true of the type, not of its current configuration. Execution belongs
// to the runtime, behind the permission gate, so there is one place to audit.
//
// # Why a model is involved at all
//
// A fault that declares its own remedy can be handled by a table. The failures
// that matter are the ones the table does not cover: a combination nobody
// anticipated, a map that disagrees with what the robot sees, a step that failed
// for a reason no rule names. For those, a proposer that can reason across the
// evidence is worth having — and it is safe to use precisely because it can only
// propose, from a catalog, under approval.
//
// # Why the investigation is recorded
//
// Every read, every rule, every model call and every decision becomes a step in
// the Trail, and the trail is published with the plan. A conclusion whose road is
// not recorded cannot be reviewed, and an operator asked to approve a recovery
// action is entitled to see why it was chosen.
type RecoveryAgent struct {
	// Catalog is what a plan may be made of. Required.
	Catalog *RecoveryCatalog

	// ReadFacts gathers the evidence an investigation starts from. Optional:
	// without it the agent still plans, from the finding alone.
	ReadFacts func(ctx context.Context, taskID string) (RecoveryFacts, []TrailStep, error)

	// Model, when set, is asked to choose the steps. Without one the agent uses
	// the deterministic route only — which is the default, because enabling a
	// model changes what the system will propose and that should be a decision.
	Model RecoveryPlanner

	// Publish emits the plan. A nil sink means the agent investigates and says
	// nothing, which is the behaviour when the runtime is not running.
	Publish func(ctx context.Context, event agentcontract.Event)
	// RememberPlan hands the plan to the store the console reads.
	//
	// A plan for a robot-level finding has no ledger row to sit in — the ledger is
	// per task, and an emergency stop belongs to no task — so it is kept where the
	// finding itself is kept. Without this the plan is produced and displayed
	// nowhere, which is what happened the first time this ran live.
	RememberPlan func(trigger string, plan RunnerAlertPlan)

	// Now is the evaluation clock. Tests set it; production leaves it nil.
	Now func() time.Time

	mu sync.Mutex
	// proposals remembers the last plan per trigger, so a persistent condition is
	// not re-planned every tick. Re-planning on every evaluation would also mean
	// re-asking a model every tick, which is both expensive and noisy.
	proposals map[string]time.Time
	stopped   bool
}

// RecoveryFacts is the evidence an investigation starts from.
//
// It is deliberately a plain struct rather than a live handle on the stores: the
// agent must not be able to reach anything it did not declare, and a value cannot
// be used to run a query nobody recorded.
type RecoveryFacts struct {
	// TaskID is the task under investigation.
	TaskID string
	// TaskState is the task's current lifecycle state, as read.
	TaskState string
	// AbnormalTasks are tasks whose recorded ending was not a success.
	AbnormalTasks []string
	// UncertainSteps are steps whose physical outcome is unknown. Their presence
	// is decisive: recovery must not touch the world until these are reconciled.
	UncertainSteps []agentcontract.StepRecord
	// ReconciliationUnavailable means the system could not establish whether
	// this task has physical steps whose outcome is unknown.
	//
	// It is not the same as "there are none", and the difference is the whole
	// point. A store that cannot answer, or a read that failed, leaves the
	// question open; reading an empty list as an answer would turn "I could not
	// check" into "the world is settled", which is exactly the mistake the
	// closed-loop contract forbids. Recovery therefore treats it like a known
	// unknown and stops, because the cost of stopping is a delay while the cost
	// of proceeding is a second physical action on a world that may have already
	// moved.
	ReconciliationUnavailable bool
	// ReconciliationError says why the question could not be answered, so an
	// escalation can tell an operator what to fix rather than only that
	// something is wrong.
	ReconciliationError string
	// Faults are the robot's own current faults, by code.
	FaultCodes []string
	// FaultSeverity is the robot's headline severity, as it reported it.
	FaultSeverity string
	// RecommendedActions are the operator instructions the robot itself gave.
	RecommendedActions []string
}

// RecoveryPlanner chooses recovery steps. It is the seam a model is installed at.
//
// A planner receives the facts and the trail so far, and returns steps drawn from
// the catalog. It may return no steps and a reason, which is the honest answer
// when it cannot help.
type RecoveryPlanner interface {
	Plan(ctx context.Context, request RecoveryRequest) (RecoveryPlan, []TrailStep, error)
}

// RecoveryRequest is what a planner is asked with.
type RecoveryRequest struct {
	// Diagnosis is the finding being recovered from, in one line.
	Diagnosis string
	// Category is the closed-loop failure class, when one was established.
	Category string
	Facts    RecoveryFacts
	// Catalog is what the planner may choose from. The never-automatic entries
	// are present so the planner can see they exist and are refused.
	Catalog []RecoveryAction
	// Trail is what has been established so far.
	Trail *Trail
}

// RecoveryPlan is a planner's answer.
type RecoveryPlan struct {
	// Diagnosis restates or refines what is wrong.
	Diagnosis string
	// Confidence in [0,1].
	Confidence float64
	// Steps are catalog action ids in order, each with the proposer's reason.
	Steps []agentcontract.RecoveryStep
	// Escalate asks for a person instead of a plan, with the reason.
	Escalate       bool
	EscalateReason string
}

var _ agentcontract.Agent = (*RecoveryAgent)(nil)

// NewRecoveryAgent creates the recovery agent over a catalog.
func NewRecoveryAgent(catalog *RecoveryCatalog) *RecoveryAgent {
	if catalog == nil {
		catalog = DefaultRecoveryCatalog()
	}
	return &RecoveryAgent{Catalog: catalog, proposals: map[string]time.Time{}}
}

// SetPublish installs the bus this agent publishes its plans on.
//
// It is called by the runtime during start, before any event is delivered. A
// proposer that is never given a bus would still investigate and still record
// its trail, and no operator would ever see the plan — the failure mode that
// looks exactly like "there was nothing to recover from".
func (a *RecoveryAgent) SetPublish(publish func(ctx context.Context, event agentcontract.Event)) {
	a.Publish = publish
}

// Name is the stable configuration and event-attribution identity.
func (a *RecoveryAgent) Name() string    { return RecoveryAgentName }
func (a *RecoveryAgent) Version() string { return RecoveryAgentVersion }

// RecoveryAgentName is the stable configuration identity.
const RecoveryAgentName = "recovery"

// Capabilities declares diagnosis and escalation.
//
// It does not declare task.execution: this agent never executes. Declaring what
// it cannot do is as useful as declaring what it can, because the runtime's
// permission gate reads this to refuse a mutation request it might otherwise
// have to judge.
func (a *RecoveryAgent) Capabilities() []agentcontract.Capability {
	return []agentcontract.Capability{
		agentcontract.CapabilityObservation,
		agentcontract.CapabilityDiagnosis,
		agentcontract.CapabilityEscalation,
	}
}

// Subscriptions are the findings and the task activity this agent reasons over.
func (a *RecoveryAgent) Subscriptions() []string {
	return []string{
		agentcontract.TopicOpsAll,
		agentcontract.TopicTaskAll,
		agentcontract.TopicActionAll,
		agentcontract.TopicStateAll,
	}
}

// Permissions declares read-only. Recovery actions are proposed here and
// executed through the runtime's permission gate, so this agent has no reason to
// hold authority of its own.
func (a *RecoveryAgent) Permissions() agentcontract.Permission {
	return agentcontract.ReadOnlyPermission()
}

// Health reports whether the agent can do its job.
func (a *RecoveryAgent) Health(_ context.Context) agentcontract.Health {
	now := a.now().UTC()
	a.mu.Lock()
	stopped := a.stopped
	a.mu.Unlock()
	if stopped {
		return agentcontract.Health{
			Status: agentcontract.HealthStopped, ReasonCode: "AGENT_SHUTDOWN", CheckedAt: now,
		}
	}
	if a.Catalog == nil || len(a.Catalog.Proposable()) == 0 {
		return agentcontract.Health{
			Status: agentcontract.HealthUnhealthy, ReasonCode: "RECOVERY_CATALOG_EMPTY",
			Detail: map[string]string{
				"reason": "without a catalog there is nothing this agent may propose",
			},
			CheckedAt: now,
		}
	}
	if len(a.Catalog.Actions()) == len(a.Catalog.Proposable()) {
		// No entry is refused outright, which means nobody has written down what
		// this robot will never do by itself.
		return agentcontract.Health{
			Status: agentcontract.HealthDegraded, ReasonCode: "RECOVERY_CATALOG_HAS_NO_REFUSALS",
			Detail: map[string]string{
				"reason": "the catalog names no action as never-automatic",
			},
			CheckedAt: now,
		}
	}
	return agentcontract.Health{Status: agentcontract.HealthHealthy, CheckedAt: now}
}

// Execute always refuses: this agent proposes, it does not execute.
func (a *RecoveryAgent) Execute(context.Context, agentcontract.ExecuteRequest) (agentcontract.ExecutionOutcome, error) {
	return agentcontract.ExecutionOutcome{}, agentcontract.ErrNotExecutable
}

// OnEvent is a no-op. Recovery is planned on demand, from a finding, rather than
// accumulated from a stream: an investigation needs the state at the moment it
// runs, and stale accumulated fragments would make its trail misleading.
func (a *RecoveryAgent) OnEvent(context.Context, agentcontract.Event) error { return nil }

// Shutdown stops the agent. It is idempotent and cancels nothing.
func (a *RecoveryAgent) Shutdown(context.Context) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.stopped = true
	return nil
}

// Recover investigates a diagnosed problem and publishes a plan.
//
// It returns the plan it published, or nil when it published nothing because the
// condition was already planned recently.
func (a *RecoveryAgent) Recover(ctx context.Context, finding Finding) *agentcontract.RecoveryPlanPayload {
	a.mu.Lock()
	if a.stopped {
		a.mu.Unlock()
		return nil
	}
	a.mu.Unlock()
	if a.Catalog == nil {
		return nil
	}

	// The trigger is the finding's identity, not its code. A code identifies a
	// kind of problem; the identity identifies the problem, and two components
	// failing the same way are two problems with two plans. Keying on the code
	// alone gave every one of them the plan made for the first.
	trigger := finding.Identity()
	if finding.Code == "" {
		trigger = "unknown"
	}
	if !a.shouldPlan(finding.TaskID, trigger) {
		return nil
	}

	trail := &Trail{
		ID: trailID(finding.TaskID, trigger), TaskID: finding.TaskID,
		Trigger: trigger, StartedAt: a.now().UTC(),
	}
	trail.Append(TrailStep{
		Kind: TrailDecision, Name: "recovery.start",
		Summary: "开始调查：" + finding.Message,
		Findings: map[string]any{
			"code": finding.Code, "severity": finding.Severity,
			"category": finding.Category, "component": finding.Component,
			// What the observer already established, so a reader sees the finding
			// this investigation started from rather than having to cross-reference.
			"observedEvidence": finding.Evidence,
			"retryForbidden":   finding.AutomaticRetryForbidden,
		},
	})

	// The catalog is recorded before any choice is made: a plan is only
	// reviewable against the options that existed when it was formed.
	trail.Append(TrailStep{
		Kind: TrailDecision, Name: "catalog.read",
		Summary: fmt.Sprintf("读取恢复动作目录：%d 条可提议，%d 条永不自动",
			len(a.Catalog.Proposable()), len(a.Catalog.Actions())-len(a.Catalog.Proposable())),
		Source: dataSources.RecoveryCatalog,
		Rows:   len(a.Catalog.Actions()),
		Findings: map[string]any{
			"proposable":     catalogIDs(a.Catalog.Proposable()),
			"neverAutomatic": catalogIDs(a.neverAutomatic()),
		},
	})

	facts, err := a.readFacts(ctx, finding.TaskID, trail)
	if err != nil {
		trail.Append(TrailStep{
			Kind: TrailQuery, Name: "facts.read", Error: err.Error(),
			Summary: "读取事实失败；调查在缺少证据的情况下继续",
		})
	}

	plan, plannerSteps, planErr := a.choosePlan(ctx, finding, facts, trail)
	for _, step := range plannerSteps {
		trail.Append(step)
	}
	if planErr != nil {
		// A planner that failed is recorded, not hidden: the plan that follows
		// was formed without it, and a reader must be able to see that.
		trail.Append(TrailStep{
			Kind: TrailModel, Name: "planner", Error: planErr.Error(),
			Summary: "规划器不可用，改用确定性路线",
		})
	}

	validated, refusals := a.validate(plan, trail)

	trail.Append(TrailStep{
		Kind: TrailDecision, Name: "recovery.decide",
		Summary: describeVerdict(plan, validated, refusals),
		Findings: map[string]any{
			"verdict": verdictOf(plan), "stepCount": len(validated),
			"refused": refusals,
		},
	})
	trail.EndedAt = a.now().UTC()

	// A refusal is decisive. If the planner asked for something the catalog
	// forbids, the plan does not proceed with the rest: the proposer's model of
	// what is allowed is wrong, and acting on the remainder of its reasoning
	// would be acting on a plan that was partly nonsense.
	// The plan is attributed to the task under investigation; when the finding is
	// robot-wide, the task read from the facts is used instead, so the plan still
	// has a home in the ledger.
	planTaskID := finding.TaskID
	if planTaskID == "" {
		planTaskID = facts.TaskID
	}
	payload := &agentcontract.RecoveryPlanPayload{
		PlanID:     "plan-" + trail.ID,
		Trigger:    trigger,
		Diagnosis:  diagnosisOf(plan, finding),
		Confidence: plan.Confidence,
		Steps:      validated,
		Source:     a.sourceName(),
		ProposedAt: a.now().UTC(),
		Trail:      trail.Encode(),
		Catalog:    a.Catalog.Encode(),
	}
	switch {
	case plan.Escalate || len(refusals) > 0:
		payload.Verdict = agentcontract.VerdictEscalate
		payload.EscalateReason = escalateReason(plan, refusals)
		// A refused plan is not partially executed. The proposer asked for
		// something the catalog forbids, which means its model of what is allowed
		// is wrong, and the remainder of its reasoning is not something to act on.
		// The steps are kept out of the payload but remain visible in the trail,
		// so a reader can see what was proposed and why it stopped.
		payload.Steps = nil
	case len(validated) == 0:
		payload.Verdict = agentcontract.VerdictEscalate
		payload.EscalateReason = "没有找到可执行的恢复动作；需要人工判断"
	default:
		payload.Verdict = agentcontract.VerdictPlan
	}

	a.publish(ctx, planTaskID, *payload)
	if a.RememberPlan != nil {
		a.RememberPlan(trigger, planToStore(*payload, trail))
	}
	return payload
}

// planToStore converts a published plan into the stored shape.
func planToStore(payload agentcontract.RecoveryPlanPayload, trail *Trail) RunnerAlertPlan {
	steps := make([]RunnerAlertPlanStep, 0, len(payload.Steps))
	for _, step := range payload.Steps {
		steps = append(steps, RunnerAlertPlanStep{
			Order: step.Order, Action: step.Action, Summary: step.Summary,
			Risk: step.Risk, RequiresApproval: step.RequiresApproval, Why: step.Why,
		})
	}
	stored := RunnerAlertPlan{
		PlanID: payload.PlanID, Verdict: string(payload.Verdict),
		Diagnosis: payload.Diagnosis, Confidence: payload.Confidence,
		Source: payload.Source, EscalateReason: payload.EscalateReason,
		Steps: steps, Investigation: trail,
	}
	// The refusals are the visible boundary: they are carried so a console can
	// show what the system will never do, not only what it proposes.
	for _, action := range refusedActions(payload.Catalog) {
		stored.Refused = append(stored.Refused, action)
	}
	return stored
}

// refusedActions reads the never-automatic entries out of the published catalog.
func refusedActions(catalog map[string]any) []RunnerAlertRefusal {
	actions, ok := catalog["actions"].([]any)
	if !ok {
		return nil
	}
	refused := make([]RunnerAlertRefusal, 0)
	for _, raw := range actions {
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		reason, _ := entry["refusal"].(string)
		if reason == "" {
			continue
		}
		id, _ := entry["id"].(string)
		summary, _ := entry["summary"].(string)
		refused = append(refused, RunnerAlertRefusal{Action: id, Summary: summary, Reason: reason})
	}
	return refused
}

// readFacts gathers the starting evidence, recording each read as a trail step.
func (a *RecoveryAgent) readFacts(ctx context.Context, taskID string, trail *Trail) (RecoveryFacts, error) {
	if a.ReadFacts == nil {
		trail.Append(TrailStep{
			Kind: TrailQuery, Name: "facts.read",
			Summary: "未配置事实来源；本次调查只依据发现本身",
		})
		return RecoveryFacts{TaskID: taskID}, nil
	}
	facts, steps, err := a.ReadFacts(ctx, taskID)
	for _, step := range steps {
		trail.Append(step)
	}
	return facts, err
}

// choosePlan decides between the deterministic route and the model, recording
// which was taken and why.
func (a *RecoveryAgent) choosePlan(ctx context.Context, finding Finding, facts RecoveryFacts, trail *Trail) (RecoveryPlan, []TrailStep, error) {
	// Reconciliation comes first, and it is not a choice.
	//
	// An unknown physical outcome means the world may already have changed. No
	// recovery action may run until that is established, because every action
	// assumes a starting state. This rule outranks any plan, including one a
	// model produces.
	//
	// "Could not establish" is included deliberately. A read that failed and a
	// read that found nothing are different answers, and only one of them is an
	// answer. Treating the failed read as "nothing to reconcile" would let a
	// store outage silently disable the one rule that protects a robot from
	// being told to redo a step that already happened.
	if len(facts.UncertainSteps) > 0 || finding.AutomaticRetryForbidden {
		return RecoveryPlan{
				Diagnosis:      "有物理动作结果未知；在对账之前不能执行任何恢复动作",
				Confidence:     1,
				Escalate:       true,
				EscalateReason: "先观测机器人当前姿态与目标物体位置完成对账，再决定是否恢复",
			}, []TrailStep{{
				Kind: TrailRule, Name: "rule.reconcile-first",
				Summary: "检出结果未知的物理动作；按闭环契约，恢复动作在对账前一律不执行",
				Findings: map[string]any{
					"uncertainSteps": len(facts.UncertainSteps),
					"retryForbidden": finding.AutomaticRetryForbidden,
				},
			}}, nil
	}
	if facts.ReconciliationUnavailable {
		return RecoveryPlan{
				Diagnosis:      "无法核实该任务是否存在结果未知的物理动作；在对账之前不能执行任何恢复动作",
				Confidence:     1,
				Escalate:       true,
				EscalateReason: "恢复执行记录的读取能力后重新调查：" + facts.ReconciliationError,
			}, []TrailStep{{
				Kind: TrailRule, Name: "rule.reconcile-unknown",
				Summary: "执行记录读不到，因此“是否结果未知”本身是未知的；按闭环契约不得据此执行任何恢复动作",
				Findings: map[string]any{
					"reconciliationUnavailable": true,
					"error":                     facts.ReconciliationError,
				},
			}}, nil
	}

	// The deterministic route: read-only actions known to help with this shape.
	// It always applies, because looking before acting is never wrong.
	//
	// The shapes come from the whole finding, not just its code. A finding is
	// named in the AGENT vocabulary (`ANOMALY_COMPONENT_FAULT`) while the catalog
	// is keyed by what actually failed (`NAV_MAP_NOT_READY`), so matching on the
	// code alone finds nothing — the two vocabularies exist at different layers on
	// purpose, and the recovery agent is the place they meet.
	shapes := shapesOf(finding)
	lookFirst := make([]RecoveryAction, 0)
	seenAction := map[string]bool{}
	for _, shape := range shapes {
		for _, action := range a.Catalog.ForShapes(shape) {
			if seenAction[action.ID] {
				continue
			}
			seenAction[action.ID] = true
			lookFirst = append(lookFirst, action)
		}
	}
	sort.SliceStable(lookFirst, func(i, j int) bool { return lookFirst[i].ID < lookFirst[j].ID })
	deterministic := RecoveryPlan{
		Diagnosis:  finding.Message,
		Confidence: 1,
		Steps:      stepsFromActions(lookFirst, "先确认现状再决定：该动作只读，不改动机器人"),
	}

	if a.Model == nil {
		return deterministic, []TrailStep{{
			Kind: TrailDecision, Name: "route.deterministic",
			Summary: fmt.Sprintf("未配置规划模型；按目录中与该故障形状匹配的只读动作给出计划（%d 步）",
				len(deterministic.Steps)),
			Findings: map[string]any{
				"searchedShapes": shapes, "steps": len(deterministic.Steps),
			},
		}}, nil
	}

	plan, steps, err := a.Model.Plan(ctx, RecoveryRequest{
		Diagnosis: finding.Message, Category: finding.Category,
		Facts: facts, Catalog: a.Catalog.Actions(), Trail: trail,
	})
	if err != nil {
		return deterministic, steps, err
	}
	// The model's plan is what gets proposed when it answers; the deterministic
	// look-first steps are kept in front of it, because observing before acting
	// is the one thing that is always right and a model may have skipped it.
	prefixed := append([]agentcontract.RecoveryStep(nil), deterministic.Steps...)
	for _, step := range plan.Steps {
		step.Order = len(prefixed) + 1
		prefixed = append(prefixed, step)
	}
	plan.Steps = prefixed
	if plan.Diagnosis == "" {
		plan.Diagnosis = finding.Message
	}
	return plan, steps, nil
}

// validate checks every proposed step against the catalog, collecting refusals.
//
// This is where a plan becomes trustworthy: the proposer's output is not used
// directly, and an action that is not in the catalog, or is never automatic, is
// refused by name with the catalog's own stated reason.
func (a *RecoveryAgent) validate(plan RecoveryPlan, trail *Trail) ([]agentcontract.RecoveryStep, []string) {
	steps := make([]agentcontract.RecoveryStep, 0, len(plan.Steps))
	refusals := make([]string, 0)
	seen := map[string]bool{}
	for _, proposed := range plan.Steps {
		action, ok := a.Catalog.Lookup(proposed.Action)
		if !ok {
			refusals = append(refusals, proposed.Action+"：目录中没有这个动作")
			continue
		}
		if !action.Executable() {
			refusals = append(refusals, proposed.Action+"："+action.Refusal)
			continue
		}
		if seen[action.ID] {
			// A repeated action would run twice, which for a write is a second
			// physical action nobody asked for.
			trail.Append(TrailStep{
				Kind: TrailDecision, Name: "plan.dedupe",
				Summary: "计划里重复出现 " + action.ID + "；只保留第一次",
			})
			continue
		}
		seen[action.ID] = true
		steps = append(steps, agentcontract.RecoveryStep{
			Order: len(steps) + 1, Action: action.ID, Summary: action.Summary,
			Risk: string(action.Risk), RequiresApproval: action.RequiresApproval(),
			Why: proposed.Why,
		})
	}
	return steps, refusals
}

func (a *RecoveryAgent) neverAutomatic() []RecoveryAction {
	refused := make([]RecoveryAction, 0)
	for _, action := range a.Catalog.Actions() {
		if action.Risk == RiskNeverAutomatic {
			refused = append(refused, action)
		}
	}
	return refused
}

// shouldPlan reports whether this trigger should be planned again.
//
// It exists so a standing condition is not re-planned every tick: re-planning
// would re-ask a model, and would republish a plan an operator has already read.
func (a *RecoveryAgent) shouldPlan(taskID, trigger string) bool {
	key := trailID(taskID, trigger)
	a.mu.Lock()
	defer a.mu.Unlock()
	if last, ok := a.proposals[key]; ok && a.now().UTC().Sub(last) < recoveryPlanCooldown {
		return false
	}
	a.proposals[key] = a.now().UTC()
	return true
}

// recoveryPlanCooldown bounds re-planning of a standing condition.
const recoveryPlanCooldown = 2 * time.Minute

// publish emits the plan.
//
// The task id is carried, not omitted. A plan is about a task, and the durable
// ledger is per task: a plan published without one is dropped by the bridge that
// persists agent events, so it would be produced, stored nowhere, and replayed by
// nobody. That mistake was made and found by running it — the plan existed in the
// agent and nowhere else.
func (a *RecoveryAgent) publish(ctx context.Context, taskID string, payload agentcontract.RecoveryPlanPayload) {
	if a.Publish == nil {
		return
	}
	event := agentcontract.Event{
		Topic: agentcontract.TopicOpsRecoveryPlan, TaskID: taskID, Agent: RecoveryAgentName,
		AgentVersion: RecoveryAgentVersion, Priority: agentcontract.PriorityHigh,
		OccurredAt: a.now().UTC(), CorrelationID: taskID, Payload: payload.Encode(),
	}
	a.Publish(ctx, event)
}

func (a *RecoveryAgent) sourceName() string {
	if a.Model == nil {
		return "deterministic"
	}
	return "deterministic+model"
}

func (a *RecoveryAgent) now() time.Time {
	if a.Now != nil {
		return a.Now()
	}
	return time.Now()
}

// shapesOf collects every name a finding offers for matching against the catalog.
//
// It returns them in a fixed order, and the caller sorts the resulting actions, so
// the same finding always produces the same plan. A plan that reorders itself
// between reads cannot be compared with itself.
func shapesOf(finding Finding) []string {
	shapes := make([]string, 0, 4)
	add := func(value string) {
		value = strings.TrimSpace(value)
		if value == "" {
			return
		}
		for _, existing := range shapes {
			if existing == value {
				return
			}
		}
		shapes = append(shapes, value)
	}
	add(finding.Code)
	add(finding.Category)
	add(finding.Component)
	// The facts a finding carries name the concrete failure: the robot's own
	// fault code, the tool that failed, the step. They are read by key rather
	// than by iterating, so a new fact cannot silently change what is matched.
	for _, key := range []string{"faultCode", "moduleId", "toolName", "code", "category", "stepId"} {
		if value, ok := finding.Facts[key].(string); ok {
			add(value)
		}
	}
	return shapes
}

func catalogIDs(actions []RecoveryAction) []string {
	ids := make([]string, 0, len(actions))
	for _, action := range actions {
		ids = append(ids, action.ID)
	}
	sort.Strings(ids)
	return ids
}

func stepsFromActions(actions []RecoveryAction, why string) []agentcontract.RecoveryStep {
	steps := make([]agentcontract.RecoveryStep, 0, len(actions))
	for _, action := range actions {
		steps = append(steps, agentcontract.RecoveryStep{
			Order: len(steps) + 1, Action: action.ID, Summary: action.Summary,
			Risk: string(action.Risk), RequiresApproval: action.RequiresApproval(), Why: why,
		})
	}
	return steps
}

func verdictOf(plan RecoveryPlan) string {
	if plan.Escalate {
		return string(agentcontract.VerdictEscalate)
	}
	return string(agentcontract.VerdictPlan)
}

func diagnosisOf(plan RecoveryPlan, finding Finding) string {
	if plan.Diagnosis != "" {
		return plan.Diagnosis
	}
	return finding.Message
}

func escalateReason(plan RecoveryPlan, refusals []string) string {
	if len(refusals) > 0 {
		return "计划里包含系统不会自动执行的动作：" + strings.Join(refusals, "；")
	}
	if plan.EscalateReason != "" {
		return plan.EscalateReason
	}
	return "需要人工判断"
}

func describeVerdict(plan RecoveryPlan, steps []agentcontract.RecoveryStep, refusals []string) string {
	if len(refusals) > 0 {
		return fmt.Sprintf("计划被拒绝转人工：%d 条动作不被允许", len(refusals))
	}
	if plan.Escalate {
		return "结论：无法自动恢复，转人工"
	}
	return fmt.Sprintf("结论：给出 %d 步恢复计划（其中 %d 步需要批准）",
		len(steps), countApproval(steps))
}

func countApproval(steps []agentcontract.RecoveryStep) int {
	count := 0
	for _, step := range steps {
		if step.RequiresApproval {
			count++
		}
	}
	return count
}
