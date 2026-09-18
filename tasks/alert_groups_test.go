package tasks_test

import (
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func at(minute int) time.Time {
	return time.Date(2026, 9, 18, 10, minute, 0, 0, time.UTC)
}

func finding(id, task, severity string, active bool) tasks.AgentAlert {
	return tasks.AgentAlert{
		ID: id, TaskID: task, Code: strings.SplitN(id, "@", 2)[0],
		Severity: severity, Active: active, DetectedAt: at(0),
	}
}

// --- one problem is one row -------------------------------------------------

// The same problem across tasks is one problem. This is the whole point: the
// reference deployment showed 276 rows over 29 tasks and eight distinct problems,
// and an operator shown 276 rows reads none of them.
func TestTheSameProblemAcrossTasksIsOneGroup(t *testing.T) {
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{
		finding("ANOMALY_ABNORMAL_TASK@task", "task-a", "warning", true),
		finding("ANOMALY_ABNORMAL_TASK@task", "task-b", "warning", true),
		finding("ANOMALY_ABNORMAL_TASK@task", "task-c", "warning", true),
	})
	if len(groups) != 1 {
		t.Fatalf("groups = %d, want 1", len(groups))
	}
	if groups[0].Count != 3 || groups[0].TaskCount != 3 {
		t.Fatalf("group = %+v", groups[0])
	}
}

// The drill-down sample is bounded, because a group whose task list is hundreds of
// entries has the same problem as the flat list it replaced.
func TestTheTaskSampleIsBounded(t *testing.T) {
	alerts := make([]tasks.AgentAlert, 0, 40)
	for index := 0; index < 40; index++ {
		alerts = append(alerts, finding("X@y", "task-"+string(rune('a'+index%26))+string(rune('0'+index/26)), "warning", true))
	}
	groups := tasks.GroupAgentAlerts(alerts)
	if len(groups[0].Tasks) > 5 {
		t.Fatalf("task sample = %d entries", len(groups[0].Tasks))
	}
	if groups[0].TaskCount < 27 {
		t.Fatalf("task count = %d: the count must be the real one, not the sample's", groups[0].TaskCount)
	}
}

// --- one problem is one finding, however many stages it passed through -------

// A finding, its hypothesis and its escalation are one problem at three stages.
// They used to arrive as three rows, so an operator read the same sentence three
// times with different decorations.
func TestTheThreeStagesShareOneRow(t *testing.T) {
	detected := finding("ANOMALY_UNVERIFIED_MUTATION@execution", "task-a", "critical", true)
	detected.Stage = tasks.StageDetected
	hypothesis := finding("ANOMALY_UNVERIFIED_MUTATION@execution", "task-a", "", true)
	hypothesis.Stage = tasks.StageHypothesis
	escalated := finding("ANOMALY_UNVERIFIED_MUTATION@execution", "task-a", "", true)
	escalated.Stage = tasks.StageEscalated

	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{detected, hypothesis, escalated})
	if len(groups) != 1 {
		t.Fatalf("groups = %d, want 1", len(groups))
	}
	if groups[0].Stage != tasks.StageEscalated {
		t.Fatalf("stage = %q: the furthest stage is the one that matters", groups[0].Stage)
	}
}

// --- handling: is a person needed -------------------------------------------

// A plan of read-only steps is work the system does alone. Saying so is the
// difference between a list and a worklist.
func TestAReadOnlyPlanIsAutomatic(t *testing.T) {
	alert := finding("X@y", "task-a", "warning", true)
	alert.Recovery = &tasks.RecoveryView{
		Verdict: "PLAN",
		Steps:   []tasks.RecoveryStepView{{Action: "observe.re-read", RequiresApproval: false}},
	}
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{alert})
	if groups[0].Handling != tasks.HandlingAutomatic {
		t.Fatalf("handling = %q (%s)", groups[0].Handling, groups[0].Why)
	}
	if groups[0].Why == "" {
		t.Fatal("no reason was given for the handling")
	}
}

// A plan with a step that moves the robot is not automatic, and must not be
// presented as if it were.
func TestAPlanNeedingApprovalIsNotAutomatic(t *testing.T) {
	alert := finding("X@y", "task-a", "warning", true)
	alert.Recovery = &tasks.RecoveryView{
		Verdict: "PLAN",
		Steps: []tasks.RecoveryStepView{
			{Action: "observe.re-read", RequiresApproval: false},
			{Action: "map.activate", RequiresApproval: true},
		},
	}
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{alert})
	if groups[0].Handling != tasks.HandlingApproval {
		t.Fatalf("handling = %q", groups[0].Handling)
	}
	if !strings.Contains(groups[0].Why, "map.activate") {
		t.Fatalf("why = %q: the step that needs approval must be named", groups[0].Why)
	}
}

// An escalation is a person's job, and the reason is the proposer's own sentence.
func TestAnEscalationIsAHumanJob(t *testing.T) {
	alert := finding("X@y", "task-a", "critical", true)
	alert.Recovery = &tasks.RecoveryView{Verdict: "ESCALATE", EscalateReason: "需要人重新标定工作台"}
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{alert})
	if groups[0].Handling != tasks.HandlingHuman {
		t.Fatalf("handling = %q", groups[0].Handling)
	}
	if !strings.Contains(groups[0].Why, "重新标定") {
		t.Fatalf("why = %q", groups[0].Why)
	}
}

// With no plan at all, a person is needed — and the group says so rather than
// implying the system is on it.
func TestNoPlanIsAHumanJob(t *testing.T) {
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{finding("X@y", "task-a", "warning", true)})
	if groups[0].Handling != tasks.HandlingHuman {
		t.Fatalf("handling = %q", groups[0].Handling)
	}
}

// --- ordering is a review order, not a log order ----------------------------

// Active first, then the ones needing a person, then severity, then reach. "Needs
// a person" outranks severity because it is why somebody opened the screen.
func TestNeedingAPersonOutranksSeverity(t *testing.T) {
	automatic := finding("A@x", "task-a", "critical", true)
	automatic.Recovery = &tasks.RecoveryView{
		Verdict: "PLAN",
		Steps:   []tasks.RecoveryStepView{{Action: "observe.re-read"}},
	}
	human := finding("B@y", "task-b", "warning", true)
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{automatic, human})
	if groups[0].Identity != "B@y" {
		t.Fatalf("order = %v: a warning needing a person was ranked below a critical one that does not",
			[]string{groups[0].Identity, groups[1].Identity})
	}
}

// A resolved problem sorts below an active one whatever its severity.
func TestActiveOutranksResolved(t *testing.T) {
	resolved := finding("A@x", "task-a", "critical", false)
	active := finding("B@y", "task-b", "info", true)
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{resolved, active})
	if groups[0].Identity != "B@y" {
		t.Fatalf("order = %v", []string{groups[0].Identity, groups[1].Identity})
	}
}

// A group is active when any of its reports is: one task resolving does not
// resolve the problem for the others.
func TestAGroupStaysActiveWhileAnyTaskStillShowsIt(t *testing.T) {
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{
		finding("A@x", "task-a", "warning", false),
		finding("A@x", "task-b", "warning", true),
	})
	if !groups[0].Active {
		t.Fatal("a problem still present in one task was reported as resolved")
	}
}

// The newest wording is the current one: a problem described differently as it
// developed should read as it does now.
func TestTheNewestReportSuppliesTheWording(t *testing.T) {
	older := finding("A@x", "task-a", "warning", true)
	older.Message, older.DetectedAt = "第一次的说法", at(0)
	newer := finding("A@x", "task-b", "warning", true)
	newer.Message, newer.DetectedAt = "后来的说法", at(9)
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{older, newer})
	if groups[0].Message != "后来的说法" {
		t.Fatalf("message = %q", groups[0].Message)
	}
	if !groups[0].LastSeen.Equal(at(9)) || !groups[0].FirstSeen.Equal(at(0)) {
		t.Fatalf("window = %v..%v", groups[0].FirstSeen, groups[0].LastSeen)
	}
}

// A finding with no identity is still counted rather than dropped: the console
// having a blank row is a smaller failure than the console silently losing one.
func TestAFindingWithNoIdentityIsStillCounted(t *testing.T) {
	groups := tasks.GroupAgentAlerts([]tasks.AgentAlert{
		{ID: "", Code: "SOMETHING", Severity: "warning", Active: true, DetectedAt: at(0)},
	})
	if len(groups) != 1 || groups[0].Identity != "SOMETHING" {
		t.Fatalf("groups = %+v", groups)
	}
}

// A finding about the robot leads every finding about its work, whatever the
// severity — everything below it was measured through a robot that may not be
// working. The flat projection already ordered by this rule and grouping must not
// lose it.
func TestARobotWideFindingLeadsTheList(t *testing.T) {
	taskFinding := finding("A@x", "task-a", "critical", true)
	robotFinding := finding("ANOMALY_SAFETY_STOP@estop", "", "info", true)
	robotFinding.RobotWide = true

	for _, order := range [][]tasks.AgentAlert{
		{taskFinding, robotFinding},
		{robotFinding, taskFinding},
	} {
		groups := tasks.GroupAgentAlerts(order)
		if groups[0].Identity != "ANOMALY_SAFETY_STOP@estop" {
			t.Fatalf("order = %v: an info-level fault on the robot was listed below a critical one about a task",
				[]string{groups[0].Identity, groups[1].Identity})
		}
	}
}
