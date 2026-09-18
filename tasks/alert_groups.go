package tasks

import (
	"sort"
	"strings"
	"time"
)

// Handling says what the system can do about a problem without a person.
//
// It is the answer to the question an operator actually opens this screen with:
// "which of these do I have to deal with?" A list that answers "here are 276
// findings" has not answered it, and a person who cannot get that answer in one
// look stops looking at all.
type Handling string

const (
	// HandlingAutomatic: every proposed step is read-only, so the automatic pass
	// can run it. The operator's job is to read the result, not to approve it.
	HandlingAutomatic Handling = "automatic"
	// HandlingApproval: a proposed step changes the robot. The system will not do
	// it alone, and will not pretend it can.
	HandlingApproval Handling = "approval"
	// HandlingHuman: nothing the catalog offers applies, so a person has to work
	// out what to do.
	HandlingHuman Handling = "human"
)

// AlertGroup is one problem, across every task it affects.
//
// # Why this type exists
//
// The per-task projection is correct and stays: two tasks failing the same way are
// two pieces of work, and collapsing them would hide which work. But it is not a
// review surface. The reference deployment reached 276 active findings over 29
// tasks, and the distinct problems behind them numbered eight. An operator shown
// 276 rows reads none of them; an operator shown eight learns in one screen that
// one of them is a broken perception path and another is a stack of unknown
// outcomes waiting on a decision.
//
// Grouping is the whole difference between a log and a worklist.
type AlertGroup struct {
	// Identity is the problem: `code@component`. Every task it appears in shares
	// it, which is exactly what makes it a group rather than a row.
	Identity string `json:"identity"`
	Code     string `json:"code"`
	Severity string `json:"severity"`
	// Stage is the furthest any of its reports got: detected, hypothesis,
	// escalated. The furthest one is shown because an escalation is a stronger
	// statement than the detection it grew out of.
	Stage string `json:"stage"`
	// Message is the most recent wording, so the group reads as the problem does
	// now rather than as it was first described.
	Message string `json:"message,omitempty"`
	// Active is true when the problem still describes the world in at least one
	// task. A group with no active member is history.
	Active bool `json:"active"`
	// TaskCount and Count are the two numbers that make the list reviewable: how
	// much work this touches, and how many reports it took to say so.
	TaskCount int `json:"taskCount"`
	Count     int `json:"count"`
	// Tasks is a bounded sample for drill-down. It is capped because a group whose
	// task list is hundreds of entries has the same problem as the flat list.
	Tasks []string `json:"tasks,omitempty"`
	// FirstSeen and LastSeen bound the problem in time.
	FirstSeen time.Time `json:"firstSeen"`
	LastSeen  time.Time `json:"lastSeen"`
	// RecommendedActions and the retry prohibition are lifted from the newest
	// report, because they are what the operator acts on.
	RecommendedActions      []string `json:"recommendedActions,omitempty"`
	AutomaticRetryForbidden bool     `json:"automaticRetryForbidden,omitempty"`
	MissingEvidence         []string `json:"missingEvidence,omitempty"`
	// Recovery is the plan proposed for this problem, when one exists.
	Recovery *RecoveryView `json:"recovery,omitempty"`
	// Investigation is the newest member's recorded road to its conclusion —
	// which stores were read, which tables, which rules ran.
	//
	// Grouping must not cost the operator the ability to check the reasoning.
	// The trail belongs to one task rather than to the group, so the newest
	// member's is carried and the tasks list stays alongside it: a reader who
	// needs a particular task's trail can open that task, and a reader asking
	// "how did it reach this conclusion" gets an answer without leaving the row.
	Investigation *InvestigationView `json:"investigation,omitempty"`
	// RobotWide is true when the problem is about the robot itself rather than
	// about one of its tasks — an emergency stop, a component fault, no telemetry.
	//
	// Such a problem leads the list whatever its severity, because everything below
	// it was measured through a robot that may not be working. The flat projection
	// already ordered by this rule; grouping must not lose it.
	RobotWide bool `json:"robotWide,omitempty"`
	// Handling says whether a person is needed, and is derived from the plan
	// rather than from an opinion about the problem.
	Handling Handling `json:"handling"`
	// Why explains the handling in one sentence, so the operator can disagree with
	// it. A status without a reason is a status nobody can audit.
	Why string `json:"why,omitempty"`
}

// maxGroupTasks bounds the drill-down sample.
const maxGroupTasks = 5

// GroupAgentAlerts folds per-task findings into one row per problem.
//
// The input is the flat projection; nothing here re-reads the ledger. That keeps
// the two views consistent by construction — a grouped row can never disagree
// with the rows it was built from, because it was built from them.
func GroupAgentAlerts(alerts []AgentAlert) []AlertGroup {
	order := make([]string, 0)
	byIdentity := map[string]*AlertGroup{}
	for _, alert := range alerts {
		identity := strings.TrimSpace(alert.ID)
		if identity == "" {
			// A finding with no identity cannot be grouped, and inventing one
			// would merge unrelated problems. It is kept under its code so it is
			// still counted rather than silently dropped.
			identity = strings.TrimSpace(alert.Code)
		}
		group, seen := byIdentity[identity]
		if !seen {
			group = &AlertGroup{Identity: identity, Code: alert.Code, FirstSeen: alert.DetectedAt}
			byIdentity[identity] = group
			order = append(order, identity)
		}
		group.Count++
		if alert.Severity != "" && alertSeverityRank(alert.Severity) > alertSeverityRank(group.Severity) {
			group.Severity = alert.Severity
		}
		if stageRank(alert.Stage) > stageRank(group.Stage) {
			group.Stage = alert.Stage
		}
		if alert.Active {
			group.Active = true
		}
		if alert.RobotWide {
			group.RobotWide = true
		}
		// The newest report is the current wording, and its advice is the advice.
		if !alert.DetectedAt.Before(group.LastSeen) {
			group.LastSeen = alert.DetectedAt
			if alert.Message != "" {
				group.Message = alert.Message
			}
			if alert.Code != "" {
				group.Code = alert.Code
			}
			if len(alert.RecommendedActions) > 0 {
				group.RecommendedActions = alert.RecommendedActions
			}
			group.AutomaticRetryForbidden = alert.AutomaticRetryForbidden
			if len(alert.MissingEvidence) > 0 {
				group.MissingEvidence = alert.MissingEvidence
			}
			if alert.Recovery != nil {
				group.Recovery = alert.Recovery
			}
			if alert.Investigation != nil {
				group.Investigation = alert.Investigation
			}
		}
		if alert.DetectedAt.Before(group.FirstSeen) || group.FirstSeen.IsZero() {
			group.FirstSeen = alert.DetectedAt
		}
		if alert.TaskID != "" && !containsString(group.Tasks, alert.TaskID) {
			group.TaskCount++
			if len(group.Tasks) < maxGroupTasks {
				group.Tasks = append(group.Tasks, alert.TaskID)
			}
		}
	}

	groups := make([]AlertGroup, 0, len(order))
	for _, identity := range order {
		group := byIdentity[identity]
		group.Handling, group.Why = handlingFor(group.Recovery)
		groups = append(groups, *group)
	}
	sort.SliceStable(groups, func(i, j int) bool {
		// Active first, then the ones needing a person, then severity, then how
		// much work they touch. "Needs a person" outranks severity because it is
		// the reason somebody opened this screen.
		if groups[i].Active != groups[j].Active {
			return groups[i].Active
		}
		// A finding about the robot leads every finding about its work.
		if groups[i].RobotWide != groups[j].RobotWide {
			return groups[i].RobotWide
		}
		if left, right := handlingRank(groups[i].Handling), handlingRank(groups[j].Handling); left != right {
			return left > right
		}
		if left, right := alertSeverityRank(groups[i].Severity), alertSeverityRank(groups[j].Severity); left != right {
			return left > right
		}
		if groups[i].TaskCount != groups[j].TaskCount {
			return groups[i].TaskCount > groups[j].TaskCount
		}
		return groups[i].Identity < groups[j].Identity
	})
	return groups
}

// handlingFor decides whether a person is needed, from the plan and nothing else.
//
// The rule is the same one the automatic pass enforces, read from the other side:
// a plan whose every step is read-only is work the system can do alone, and a plan
// containing any step that requires approval is work it cannot. Deriving it here
// rather than asking the operator to work it out is what turns the list into a
// queue of decisions.
func handlingFor(recovery *RecoveryView) (Handling, string) {
	if recovery == nil {
		return HandlingHuman,
			"还没有针对这个问题的恢复计划；需要人判断，或者等自动调查给出结论"
	}
	if recovery.Verdict == "ESCALATE" {
		reason := strings.TrimSpace(recovery.EscalateReason)
		if reason == "" {
			reason = "目录里没有适用的恢复动作"
		}
		return HandlingHuman, reason
	}
	if len(recovery.Steps) == 0 {
		return HandlingHuman, "计划里没有可执行的步骤"
	}
	for _, step := range recovery.Steps {
		if step.RequiresApproval {
			return HandlingApproval,
				"计划里有会改动机器人的步骤（" + step.Action + "），系统不会自行执行"
		}
	}
	return HandlingAutomatic,
		"计划里的步骤全部只读，系统可以自行执行；这里只需要复核结果"
}

func stageRank(stage string) int {
	switch stage {
	case StageEscalated:
		return 2
	case StageHypothesis:
		return 1
	default:
		return 0
	}
}

func handlingRank(handling Handling) int {
	switch handling {
	case HandlingHuman:
		return 2
	case HandlingApproval:
		return 1
	default:
		return 0
	}
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
