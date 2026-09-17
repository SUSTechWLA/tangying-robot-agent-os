package agentruntime

import (
	"sort"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

// Anomaly rule codes. They are the stable identity of a finding: the console,
// a hypothesis and an escalation all refer to an anomaly by these, so they are
// part of the interface rather than an implementation detail.
const (
	// AnomalyComponentFault means the robot reports a module fault that takes a
	// capability away. The fault ledger already decided this; the observer only
	// names it in the agent vocabulary.
	AnomalyComponentFault = "ANOMALY_COMPONENT_FAULT"
	// AnomalySafetyStop means the robot is emergency stopped or latched.
	AnomalySafetyStop = "ANOMALY_SAFETY_STOP"
	// AnomalyActionFailed means a dispatched action reported a failure code.
	AnomalyActionFailed = "ANOMALY_ACTION_FAILED"
	// AnomalyUnverifiedMutation means a physical step has no confirmed outcome.
	// It is the single most important finding this agent produces, and the only
	// one whose advice includes a prohibition.
	AnomalyUnverifiedMutation = "ANOMALY_UNVERIFIED_MUTATION"
	// AnomalyStepLatency means a step phase exceeded its budget.
	AnomalyStepLatency = "ANOMALY_STEP_LATENCY"
	// AnomalyTelemetryStale means the observation the observer is reasoning
	// about is too old to reason about.
	AnomalyTelemetryStale = "ANOMALY_TELEMETRY_STALE"
	// AnomalyRepeatedFailure means the same step failed more than once.
	AnomalyRepeatedFailure = "ANOMALY_REPEATED_FAILURE"
	// AnomalyAbnormalTask means a task ended abnormally but nothing more
	// specific could be established about it.
	//
	// It exists so that "we know this ended badly" is never reported as silence.
	// The alternative — staying quiet because the step-level detail is missing —
	// makes an unexplained failure indistinguishable from a healthy task, which
	// is the one confusion a supervisor must never create.
	AnomalyAbnormalTask = "ANOMALY_ABNORMAL_TASK"
)

// Severity levels. These are the observer's own scale, describing how much a
// finding should interrupt a person. They are deliberately separate from the
// robot's fault severity vocabulary (info/degraded/blocked/safety), which is
// preserved verbatim in the finding's component and code.
const (
	SeverityInfo     = "info"
	SeverityWarning  = "warning"
	SeverityCritical = "critical"
)

// DefaultTelemetryMaxAge is how old a telemetry snapshot may be before the
// observer refuses to reason about it. Reasoning about a stale observation is
// how an operator is told about a problem that has already been fixed, or
// misses one that has appeared since.
const DefaultTelemetryMaxAge = 10 * time.Second

// DefaultStepLatencyBudget is the phase duration above which a step is reported
// as slow. It is a documented threshold rather than a learned baseline: this
// version has no history to learn from, and inventing a statistical baseline
// would make the finding unreproducible.
const DefaultStepLatencyBudget = 30 * time.Second

// maxEvidenceRefs bounds how many evidence references one finding carries. A
// finding is a pointer for a human, not a dump of everything observed.
const maxEvidenceRefs = 8

// Finding is one thing the observer decided is worth saying.
type Finding struct {
	// TaskID is the task this finding is about, when it is about one.
	//
	// It is carried on the finding rather than read from the agent's "most recent
	// task" because a finding can be about a task the observer never received an
	// event for — which is exactly the restart case. A finding with no task id
	// cannot be recorded against the task it describes, so it would be published
	// to nobody and replayed by nobody.
	TaskID    string
	Code      string
	Severity  string
	Component string
	Message   string
	Evidence  []string
	Facts     map[string]any
	// Category is the closed-loop failure class this finding falls under, when
	// one applies. It is empty for findings that are not failures, such as a
	// slow step.
	Category string
	// RecommendedActions are human actions, in the order to try them.
	RecommendedActions []string
	// MissingEvidence names what could not be established. When a finding cannot
	// be classified, saying what is missing is the honest answer.
	MissingEvidence []string
	// AutomaticRetryForbidden is set only for an unknown physical outcome. It is
	// the one piece of advice that must never be softened downstream.
	AutomaticRetryForbidden bool
	// Confidence is in [0,1], fixed by the rule that produced the finding rather
	// than estimated from the data. A deterministic rule knows how much it
	// knows.
	Confidence float64
	// AffectedComponents broadens Component when a finding implicates more than
	// the thing it was detected on.
	AffectedComponents []string
}

// Identity is the stable identity of this finding: what is wrong, and where.
//
// It is the key every part of the system has to agree on — the alert that is
// raised, the plan that answers it, and the lookup that puts the two together.
// It is derived from one rule rather than re-derived by each of them, because
// the previous version had the alert store key on code-and-component while the
// recovery agent keyed its plan on the code alone. The result was one plan
// displayed against eleven different component failures, each of them reading a
// diagnosis that named a component the alert was not about.
func (f Finding) Identity() string {
	return agentcontract.AnomalyIdentity(f.Code, f.Component)
}

// ObservationInput is everything the rules are allowed to look at.
//
// Every field comes from a system that already exists: the telemetry contract,
// the fault contract, the durable execution records and the latency recorder.
// Nothing here is collected for the observer's benefit, which is what keeps the
// observer from becoming a second, competing source of truth.
type ObservationInput struct {
	// TaskID scopes the observation. An empty task id means robot-wide.
	TaskID string
	// Snapshot is the latest telemetry, including the robot's own fault report.
	Snapshot *telemetry.Snapshot
	// SnapshotReceivedAt is when the snapshot was read. Freshness is judged
	// against this, not against the sensor clock alone.
	SnapshotReceivedAt time.Time
	// UncertainSteps are the durable records of physical steps whose outcome is
	// unknown.
	UncertainSteps []agentcontract.StepRecord
	// AbnormalTasks are tasks whose recorded ending was not a success. They are
	// read from the durable record, so they are visible to a process that
	// started after the failure.
	AbnormalTasks []string
	// FailedActions are the failed tool transitions observed since the last
	// evaluation.
	FailedActions []FailedAction
	// Latency are the measured step phases.
	Latency []LatencyFact
	// Now is the evaluation clock.
	Now time.Time
	// TelemetryMaxAge overrides DefaultTelemetryMaxAge.
	TelemetryMaxAge time.Duration
	// StepLatencyBudget overrides DefaultStepLatencyBudget.
	StepLatencyBudget time.Duration
}

// FailedAction is one failed tool transition.
type FailedAction struct {
	StepID      string
	ToolName    string
	ErrorCode   string
	Occurrences int
}

// LatencyFact is one measured step phase.
type LatencyFact struct {
	StepID     string
	Capability string
	Outcome    string
	Phase      string
	Duration   time.Duration
}

// Evaluate applies every rule and returns the findings in a stable order.
//
// The rules are pure: same input, same output, no I/O, no clock of their own.
// That is what makes them testable by exhaustion, and what makes a finding
// reproducible from a replay — an operator investigating a report gets the same
// answer the observer got.
func Evaluate(input ObservationInput) []Finding {
	now := input.Now
	if now.IsZero() {
		now = time.Now().UTC()
	}
	findings := make([]Finding, 0, 4)
	findings = append(findings, safetyFindings(input)...)
	findings = append(findings, faultFindings(input)...)
	findings = append(findings, uncertainMutationFindings(input)...)
	findings = append(findings, failedActionFindings(input)...)
	findings = append(findings, latencyFindings(input)...)
	findings = append(findings, abnormalTaskFindings(input)...)
	findings = append(findings, staleTelemetryFindings(input, now)...)
	sortFindings(findings)
	return findings
}

// safetyFindings reports an emergency stop. It is checked before anything else
// and reported even when the robot also has module faults, because an emergency
// stop is the fact that explains the others.
func safetyFindings(input ObservationInput) []Finding {
	snapshot := input.Snapshot
	if snapshot == nil {
		return nil
	}
	latched := snapshot.EmergencyStopped
	for _, anomaly := range snapshot.Anomalies {
		if anomaly == "EMERGENCY_STOP_LATCHED" {
			latched = true
		}
	}
	if snapshot.Faults != nil {
		for _, fault := range snapshot.Faults.Faults {
			if fault.Code == "EMERGENCY_STOP_LATCHED" {
				latched = true
			}
		}
	}
	if !latched {
		return nil
	}
	return []Finding{{
		Code: AnomalySafetyStop, Severity: SeverityCritical, Component: "estop",
		Message:  "机器人处于急停状态，所有依赖运动的能力都已不可用",
		Evidence: evidenceRefs(snapshot),
		Facts: map[string]any{
			"emergencyStopped": snapshot.EmergencyStopped,
			"robotId":          snapshot.RobotID,
			"adapter":          snapshot.Adapter,
		},
		// An emergency stop is not a failure class the closed loop can act on:
		// clearing it is a physical act a person performs. Saying so is more
		// useful than mapping it onto a retry class.
		Category:   string(closedloop.Permission),
		Confidence: 1,
		RecommendedActions: []string{
			"确认现场安全后，由人工复位急停按钮",
			"复位后重新观测，确认故障台账已清空再恢复任务",
		},
		AffectedComponents: []string{"estop", "motion"},
	}}
}

// faultFindings reports the robot's own blocking faults.
//
// These are not detected here. The robot published them, the fault contract
// validated them, and this rule only names them in the agent vocabulary so an
// observer, a hypothesis and a console can refer to the same thing. Re-deriving
// a fault the robot already proved would be a second opinion that can disagree.
func faultFindings(input ObservationInput) []Finding {
	snapshot := input.Snapshot
	if snapshot == nil || snapshot.Faults == nil {
		return nil
	}
	blocking := snapshot.Faults.Blocking()
	if len(blocking) == 0 {
		return nil
	}
	findings := make([]Finding, 0, len(blocking))
	components := make([]string, 0, len(blocking))
	for _, fault := range blocking {
		component := fault.ModuleID
		if component == "" {
			component = fault.Kind
		}
		components = append(components, component)
		severity := SeverityWarning
		if fault.Severity == "blocked" || fault.Severity == "safety" {
			severity = SeverityCritical
		}
		// A fault the robot says it can fix itself is news; one that needs a
		// person is the thing to act on. Reporting them at the same severity is
		// how an operator learns to ignore both.
		actions := []string{}
		if fault.Remedy == "operator_assist" || fault.Remedy == "service_required" {
			actions = append(actions, "按机器人给出的处置方式处理："+fault.UserInstruction)
		} else {
			actions = append(actions, "等待机器人自愈后复查故障台账")
		}
		findings = append(findings, Finding{
			Code: AnomalyComponentFault, Severity: severity, Component: component,
			Message:  faultMessage(fault),
			Evidence: evidenceRefs(snapshot),
			Facts: map[string]any{
				"moduleId": fault.ModuleID, "faultKind": fault.Kind, "faultCode": fault.Code,
				"faultSeverity": fault.Severity, "remedy": fault.Remedy, "occurrences": fault.Occurrences,
			},
			Confidence:         1,
			RecommendedActions: actions,
			AffectedComponents: snapshot.Faults.UnavailableCapabilities,
		})
	}
	if len(components) > 1 {
		// Several modules failing at once is more often one shared cause than
		// several independent faults, and the observer should not leave a reader
		// to notice that.
		findings = append(findings, Finding{
			Code: AnomalyComponentFault, Severity: SeverityWarning, Component: "robot",
			Message: "多个模块同时故障，通常指向共同原因（供电、总线或计算）",
			Facts: map[string]any{
				"faultCount": len(blocking), "modules": strings.Join(components, ","),
				"worstSeverity": snapshot.Faults.WorstSeverity(),
			},
			Confidence:         1,
			AffectedComponents: components,
		})
	}
	return findings
}

// uncertainMutationFindings reports physical steps whose outcome is unknown.
//
// This is the finding that matters most, because it is the one that must change
// what happens next: nothing may be retried until the world has been looked at.
// It comes from the durable execution records rather than from events, so it
// survives a restart that lost every in-memory observation.
func uncertainMutationFindings(input ObservationInput) []Finding {
	if len(input.UncertainSteps) == 0 {
		return nil
	}
	// One finding per task. The remediation is "go and look at this robot's
	// world", which is per task; merging several tasks into one finding would
	// report a count without saying which world to reconcile.
	byTask := map[string][]agentcontract.StepRecord{}
	order := make([]string, 0, 2)
	for _, step := range input.UncertainSteps {
		if _, seen := byTask[step.TaskID]; !seen {
			order = append(order, step.TaskID)
		}
		byTask[step.TaskID] = append(byTask[step.TaskID], step)
	}

	findings := make([]Finding, 0, len(order))
	for _, taskID := range order {
		steps := byTask[taskID]
		stepIDs := make([]string, 0, len(steps))
		components := make([]string, 0, len(steps))
		for _, step := range steps {
			stepIDs = append(stepIDs, step.StepID)
			if step.Capability != "" {
				components = append(components, step.Capability)
			}
		}
		evidence := make([]string, 0, maxEvidenceRefs)
		for _, step := range steps {
			if len(evidence) >= maxEvidenceRefs {
				break
			}
			evidence = append(evidence, step.TaskID+"/"+step.StepID)
		}
		missing := []string{}
		if len(evidence) == 0 {
			missing = append(missing, "步骤的执行记录没有证据编号，无法指名要复核的观测")
		}
		findings = append(findings, Finding{
			TaskID: taskID,
			Code:   AnomalyUnverifiedMutation, Severity: SeverityCritical, Component: "execution",
			Message:  "有物理动作已下发但没有确认结果，必须先对账再决定下一步",
			Evidence: evidence,
			Facts: map[string]any{
				"uncertainSteps": strings.Join(stepIDs, ","), "stepCount": len(steps),
			},
			Category:                string(closedloop.UnknownOutcome),
			Confidence:              1,
			AutomaticRetryForbidden: true,
			RecommendedActions: []string{
				"先观测机器人当前姿态与目标物体位置，确认这个动作到底有没有发生",
				"用观测结果对账后再决定继续、重做还是交给人工",
			},
			MissingEvidence:    missing,
			AffectedComponents: components,
		})
	}
	return findings
}

// failedActionFindings reports failed tool transitions and classifies them with
// the existing closed-loop classifier.
//
// The classifier is reused rather than reimplemented: the repository already
// decided that a failure code has exactly one safe recovery action, and an
// observer with its own taxonomy would eventually disagree with the executor
// about whether a retry is allowed.
func failedActionFindings(input ObservationInput) []Finding {
	findings := make([]Finding, 0, len(input.FailedActions))
	for _, action := range input.FailedActions {
		class := closedloop.Classify(action.ErrorCode)
		severity := SeverityWarning
		retryForbidden := false
		actions := []string{}
		missing := []string{}
		switch class {
		case closedloop.UnknownOutcome:
			// An unrecognised or empty code is an unknown physical outcome, not
			// a transient failure. Saying so is the whole point of the rule.
			severity = SeverityCritical
			retryForbidden = true
			actions = append(actions, "不要自动重试：先对账确认这次动作的实际结果")
		case closedloop.Permission, closedloop.Resource:
			actions = append(actions, "检查审批、租约或资源占用状态后再恢复")
		case closedloop.Perception:
			actions = append(actions, "重新观测或搜索目标后再尝试")
		case closedloop.Planning:
			actions = append(actions, "当前计划不可达，需要新的目标或路径而不是重放原命令")
		case closedloop.Validation:
			actions = append(actions, "命令参数或版本不被接受，重放同样参数不会成功")
		case closedloop.Fatal:
			actions = append(actions, "该失败不能通过重试解决，需要人工判断")
		default:
			// "按既有策略重试" used to stand here, pointing at a retry policy that
			// did not exist: the state machine that defined one was never driven,
			// and there is no automatic retry for physical work at all. Advice
			// that names a mechanism the system does not have is worse than no
			// advice, because an operator waits for it.
			//
			// The retry is available and it is a decision: the recovery catalog
			// offers task.retry-step, and it requires approval.
			actions = append(actions, "属于可重试的瞬时故障；系统不会自动重试，请确认后重新下发这一步")
		}
		if action.ErrorCode == "" {
			missing = append(missing, "运行时没有给出错误码，无法确定这次动作是否到达硬件")
		}
		if action.Occurrences > 1 {
			severity = SeverityCritical
		}
		findings = append(findings, Finding{
			Code: AnomalyActionFailed, Severity: severity, Component: componentFor(action),
			Message: failedActionMessage(action, class),
			Facts: map[string]any{
				"toolName": action.ToolName, "stepId": action.StepID,
				"errorCode": action.ErrorCode, "occurrences": action.Occurrences,
				"failureClass": string(class),
			},
			Category:                string(class),
			Confidence:              1,
			AutomaticRetryForbidden: retryForbidden,
			RecommendedActions:      actions,
			MissingEvidence:         missing,
		})
	}
	return findings
}

// latencyFindings reports step phases that exceeded the budget.
func latencyFindings(input ObservationInput) []Finding {
	budget := input.StepLatencyBudget
	if budget <= 0 {
		budget = DefaultStepLatencyBudget
	}
	findings := make([]Finding, 0, len(input.Latency))
	for _, fact := range input.Latency {
		if fact.Duration <= budget {
			continue
		}
		findings = append(findings, Finding{
			Code: AnomalyStepLatency, Severity: SeverityWarning, Component: componentOr(fact.Capability, "execution"),
			Message: "步骤阶段耗时超出预算，可能是环境或设备变慢",
			Facts: map[string]any{
				"stepId": fact.StepID, "capability": fact.Capability, "phase": fact.Phase,
				"outcome": fact.Outcome, "durationMs": fact.Duration.Milliseconds(),
				"budgetMs": budget.Milliseconds(),
			},
			Confidence:         1,
			RecommendedActions: []string{"检查该步骤常用能力的设备状态与网络时延"},
		})
	}
	return findings
}

// abnormalTaskFindings reports tasks that ended abnormally.
//
// It is the floor, not the ceiling: when a more specific rule explains the
// failure this is still reported, because "these tasks ended badly and here is
// the detail we have" is what an operator sweeping a fleet needs. When no
// specific rule fires at all, this is the only thing standing between an
// unexplained failure and a silent supervisor.
func abnormalTaskFindings(input ObservationInput) []Finding {
	if len(input.AbnormalTasks) == 0 {
		return nil
	}
	tasks := append([]string(nil), input.AbnormalTasks...)
	sort.Strings(tasks)
	facts := map[string]any{"taskCount": len(tasks)}
	if len(tasks) <= maxEvidenceRefs {
		facts["taskIds"] = strings.Join(tasks, ",")
	} else {
		// The list is bounded like every other evidence field; the count stays
		// exact so nothing is hidden by the truncation.
		facts["taskIds"] = strings.Join(tasks[:maxEvidenceRefs], ",")
		facts["truncated"] = true
	}
	return []Finding{{
		Code: AnomalyAbnormalTask, Severity: SeverityWarning, Component: "task",
		Message:  "有任务异常结束，需要核对执行记录确认实际到哪一步",
		Evidence: tasks,
		Facts:    facts,
		// No category: the failure class belongs to a failure code, and this
		// finding deliberately does not have one. Guessing a class here would be
		// the exact second taxonomy the classifier reuse exists to prevent.
		Confidence: 1,
		RecommendedActions: []string{
			"打开该任务的执行记录，逐步核对工具调用与证据",
			"确认是否有物理动作结果未知，需要先对账再恢复",
		},
	}}
}

// staleTelemetryFindings reports that the observation is too old to reason
// about. It exists so the other rules' silence is not mistaken for good news: an
// observer with no current observation knows nothing, and must say so.
func staleTelemetryFindings(input ObservationInput, now time.Time) []Finding {
	snapshot := input.Snapshot
	if snapshot == nil {
		return []Finding{{
			Code: AnomalyTelemetryStale, Severity: SeverityWarning, Component: "telemetry",
			Message:    "还没有可用的机器人观测，当前无法判断机器人状态",
			Facts:      map[string]any{"reason": "NO_OBSERVATION"},
			Confidence: 1,
			// Naming what is missing is not the same as telling someone what to
			// do. The stale branch below already carried an action and this one
			// did not, so a task with no observation at all handed an operator a
			// statement of ignorance and no next step. The cause is the same in
			// both branches, so the advice is too.
			RecommendedActions: []string{"检查机器人连接与遥测上报是否仍然正常"},
			MissingEvidence: []string{
				"缺少机器人观测：连接运行时并确认遥测在流动",
			},
		}}
	}
	maxAge := input.TelemetryMaxAge
	if maxAge <= 0 {
		maxAge = DefaultTelemetryMaxAge
	}
	observedAt := snapshot.ObservedAt
	if observedAt.IsZero() {
		observedAt = input.SnapshotReceivedAt
	}
	if observedAt.IsZero() {
		return nil
	}
	age := now.Sub(observedAt)
	if age <= maxAge {
		return nil
	}
	return []Finding{{
		Code: AnomalyTelemetryStale, Severity: SeverityWarning, Component: "telemetry",
		Message: "机器人观测已经过期，基于它做出的判断不可信",
		Facts: map[string]any{
			"ageMs": age.Milliseconds(), "maxAgeMs": maxAge.Milliseconds(),
			"robotId": snapshot.RobotID,
		},
		Confidence:         1,
		RecommendedActions: []string{"检查机器人连接与遥测上报是否仍然正常"},
		MissingEvidence:    []string{"缺少当前观测：上面的其它结论都基于过期数据"},
	}}
}

func componentFor(action FailedAction) string {
	if action.ToolName != "" {
		return action.ToolName
	}
	if action.StepID != "" {
		return action.StepID
	}
	return "execution"
}

func componentOr(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func faultMessage(fault robotcontract.Fault) string {
	message := fault.ModuleID + " 报告 " + fault.Code
	if fault.Detail != "" {
		message += "：" + fault.Detail
	}
	return message
}

func failedActionMessage(action FailedAction, class closedloop.Class) string {
	tool := componentFor(action)
	if action.ErrorCode == "" {
		return tool + " 失败但没有错误码，结果未知"
	}
	return tool + " 失败：" + action.ErrorCode + "（" + string(class) + "）"
}

// evidenceRefs collects the observation identities a finding rests on. A finding
// with no evidence reference cannot be reviewed, so this is always attempted.
func evidenceRefs(snapshot *telemetry.Snapshot) []string {
	if snapshot == nil {
		return nil
	}
	refs := make([]string, 0, 2)
	if snapshot.Reconstruction != nil && snapshot.Reconstruction.ObservationID != "" {
		refs = append(refs, snapshot.Reconstruction.ObservationID)
	}
	for _, entity := range snapshot.Entities {
		if len(refs) >= maxEvidenceRefs {
			break
		}
		if entity.EntityID != "" {
			refs = append(refs, entity.EntityID)
		}
	}
	return refs
}

// sortFindings orders findings so a report is reproducible: severity first,
// then code, then component. Without a fixed order, two runs over the same
// observation would produce reports that differ only in sequence, and a test
// could not assert on them.
func sortFindings(findings []Finding) {
	sort.SliceStable(findings, func(i, j int) bool {
		left, right := severityRank(findings[i].Severity), severityRank(findings[j].Severity)
		if left != right {
			return left > right
		}
		if findings[i].Code != findings[j].Code {
			return findings[i].Code < findings[j].Code
		}
		return findings[i].Component < findings[j].Component
	})
}

func severityRank(severity string) int {
	switch severity {
	case SeverityCritical:
		return 3
	case SeverityWarning:
		return 2
	case SeverityInfo:
		return 1
	default:
		return 0
	}
}

// ExtractErrorCodeForTest exposes the error-code extraction used by the
// failure rule so it can be pinned by a test.
//
// It is exported rather than testing through a published event because the
// interesting property is the parsing itself: which messages yield a code and
// which deliberately yield nothing. Those are different assertions from "the
// observer published something", and testing them through the event path would
// make a parsing failure look like a publishing failure.
//
// The name says it is for tests. It is a deliberate, narrow exception to the
// rule that test-only surface does not live in production packages: the
// alternative is a parser whose edge cases are only exercised indirectly, and
// getting this parser wrong in the lenient direction is what would let a
// possibly-completed physical action be retried.
func ExtractErrorCodeForTest(text string) string { return extractErrorCode(text) }
