package console_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The alert endpoint is what the warning banner is drawn from, and it had no test
// at all — which is how it came to show one component's recovery plan against
// eleven different component failures. Every part was self-consistent; only the
// composition was wrong. These tests are about the composition.

type alertView struct {
	ID        string `json:"id"`
	Code      string `json:"code"`
	Component string `json:"component"`
	Active    bool   `json:"active"`
	Recovery  *struct {
		PlanID         string `json:"planId"`
		Verdict        string `json:"verdict"`
		Diagnosis      string `json:"diagnosis"`
		EscalateReason string `json:"escalateReason"`
		Steps          []struct {
			Action           string `json:"action"`
			Risk             string `json:"risk"`
			RequiresApproval bool   `json:"requiresApproval"`
		} `json:"steps"`
	} `json:"recovery"`
	Investigation *struct {
		TrailID   string `json:"trailId"`
		Trigger   string `json:"trigger"`
		StepCount int    `json:"stepCount"`
		Steps     []struct {
			Sequence int    `json:"sequence"`
			Kind     string `json:"kind"`
			Name     string `json:"name"`
			Error    string `json:"error"`
			Source   struct {
				Kind  string `json:"kind"`
				Table string `json:"table"`
				Query string `json:"query"`
			} `json:"sourceDetail"`
		} `json:"steps"`
	} `json:"investigation"`
}

type alertsResponse struct {
	ActiveCount  int         `json:"activeCount"`
	Alerts       []alertView `json:"alerts"`
	RunnerAlerts []alertView `json:"runnerAlerts"`
	Supervision  any         `json:"supervision"`
}

// alertsServer builds the console over a stack with no pre-existing tasks. The
// findings and their plans are supplied directly, so the test is about the
// projection rather than about an observer rediscovering them.
func alertsServer(t *testing.T) (*httptest.Server, *agentruntime.AlertStore, *localapp.App) {
	t.Helper()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	app := localapp.New(service, agent.NewRunner(nil, nil, nil), memory.NewQueue[string](4))
	alerts := agentruntime.NewAlertStore(0)
	app.WithRunnerAlerts(alerts.Alerts).
		WithRunnerAlertPlan(alerts.PlanFor).
		WithSupervision(func() tasks.SupervisionStatus {
			return tasks.SupervisionStatus{Enabled: true, Observing: true}
		})
	server := httptest.NewServer(console.NewServer(service, app).Handler())
	t.Cleanup(server.Close)
	return server, alerts, app
}

func getAlerts(t *testing.T, server *httptest.Server) alertsResponse {
	t.Helper()
	response, err := http.Get(server.URL + "/v1/agent/alerts")
	if err != nil {
		t.Fatalf("get alerts: %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", response.StatusCode)
	}
	var decoded alertsResponse
	if err := json.NewDecoder(response.Body).Decode(&decoded); err != nil {
		t.Fatalf("decode alerts: %v", err)
	}
	return decoded
}

// finding builds one robot-level finding, the shape the observer records.
func finding(code, component, message string, severity string) agentruntime.Finding {
	return agentruntime.Finding{
		Code: code, Component: component, Message: message, Severity: severity,
		RecommendedActions: []string{"打开执行记录核对"},
	}
}

// identityOf derives a finding's identity the way production derives it: from the
// finding itself. Writing the string out by hand here would let the test and the
// code drift apart, which is the failure this whole file is about.
func identityOf(code, component string) string {
	return finding(code, component, "", "warning").Identity()
}

// planFor builds a stored plan as the recovery agent would store it.
func planFor(t *testing.T, trigger, planID, diagnosis string) agentruntime.RunnerAlertPlan {
	t.Helper()
	return agentruntime.RunnerAlertPlan{
		PlanID: planID, Verdict: "PLAN", Diagnosis: diagnosis, Source: "deterministic",
		Steps: []agentruntime.RunnerAlertPlanStep{{
			Order: 1, Action: "execution.read-history", Summary: "读取执行记录",
			Risk: "read_only", RequiresApproval: false,
		}},
		Investigation: &agentruntime.Trail{
			ID: "trail-" + planID, Trigger: trigger,
			Steps: []agentruntime.TrailStep{{
				Sequence: 1, Kind: agentruntime.TrailQuery, Name: "step_runs.read",
				Source:  agentruntime.DataSource{Kind: "sqlite", Table: "step_runs", Query: "task_id = t1"},
				Summary: "读取该任务的执行记录",
				Rows:    3,
			}},
		},
	}
}

// TestEachComponentFailureIsShownItsOwnPlan is the defect, as an assertion.
//
// Two components failed the same way in one evaluation. A plan was made for each,
// but the console looked both up by the failure code, so both alerts were
// rendered with whichever plan happened to be stored last — including a diagnosis
// naming a component the alert was not about. An operator reading that would act
// on the wrong problem.
func TestEachComponentFailureIsShownItsOwnPlan(t *testing.T) {
	server, alerts, _ := alertsServer(t)
	alerts.Record([]agentruntime.Finding{
		finding("ANOMALY_ACTION_FAILED", "navigation.navigate", "navigation.navigate 失败：NAV_ROTATION_LIMIT", "critical"),
		finding("ANOMALY_ACTION_FAILED", "observe_scene", "observe_scene 失败：NAV_ROTATION_LIMIT", "warning"),
	})
	alerts.RememberPlan(
		identityOf("ANOMALY_ACTION_FAILED", "navigation.navigate"),
		planFor(t, "ANOMALY_ACTION_FAILED@navigation.navigate", "plan-nav", "navigation.navigate 失败：NAV_ROTATION_LIMIT"),
	)
	alerts.RememberPlan(
		identityOf("ANOMALY_ACTION_FAILED", "observe_scene"),
		planFor(t, "ANOMALY_ACTION_FAILED@observe_scene", "plan-observe", "observe_scene 失败：NAV_ROTATION_LIMIT"),
	)

	decoded := getAlerts(t, server)
	if len(decoded.RunnerAlerts) != 2 {
		t.Fatalf("runner alerts = %d, want 2: %#v", len(decoded.RunnerAlerts), decoded.RunnerAlerts)
	}
	byComponent := map[string]alertView{}
	for _, alert := range decoded.RunnerAlerts {
		byComponent[alert.Component] = alert
	}
	nav, ok := byComponent["navigation.navigate"]
	if !ok {
		t.Fatalf("the navigation finding is missing: %#v", byComponent)
	}
	observe, ok := byComponent["observe_scene"]
	if !ok {
		t.Fatalf("the observe finding is missing: %#v", byComponent)
	}
	if nav.Recovery == nil || observe.Recovery == nil {
		t.Fatal("a finding with a plan was shown without it")
	}
	if nav.Recovery.PlanID != "plan-nav" {
		t.Fatalf("navigation alert shows plan %q, want its own", nav.Recovery.PlanID)
	}
	if observe.Recovery.PlanID != "plan-observe" {
		t.Fatalf("observe alert shows plan %q, want its own", observe.Recovery.PlanID)
	}
	// The diagnosis is what an operator reads first, so it has to be about the
	// alert it is attached to.
	if nav.Recovery.Diagnosis == observe.Recovery.Diagnosis {
		t.Fatalf("two different failures were given one diagnosis: %q", nav.Recovery.Diagnosis)
	}
}

// TestThePlanIsShownWithTheReadsBehindIt is the traceability requirement on the
// surface an operator actually looks at: which store, which table, and what came
// back.
func TestThePlanIsShownWithTheReadsBehindIt(t *testing.T) {
	server, alerts, _ := alertsServer(t)
	alerts.Record([]agentruntime.Finding{
		finding("ANOMALY_ACTION_FAILED", "observe_scene", "observe_scene 失败", "warning"),
	})
	identity := identityOf("ANOMALY_ACTION_FAILED", "observe_scene")
	alerts.RememberPlan(identity, planFor(t, identity, "plan-observe", "observe_scene 失败"))

	decoded := getAlerts(t, server)
	if len(decoded.RunnerAlerts) != 1 {
		t.Fatalf("runner alerts = %d, want 1", len(decoded.RunnerAlerts))
	}
	alert := decoded.RunnerAlerts[0]
	if alert.Investigation == nil || len(alert.Investigation.Steps) == 0 {
		t.Fatal("the plan was shown without the investigation that produced it")
	}
	step := alert.Investigation.Steps[0]
	if step.Source.Table != "step_runs" || step.Source.Kind != "sqlite" {
		t.Fatalf("the investigation does not name the store it read: %#v", step.Source)
	}
	if step.Source.Query == "" {
		t.Fatal("the investigation does not name the selection it applied")
	}
	if alert.Investigation.Trigger != identity {
		t.Fatalf("investigation trigger = %q, want the finding's identity", alert.Investigation.Trigger)
	}
}

// TestAnAlertWithoutAPlanIsShownWithoutOne pins the other direction. Filling the
// panel with an empty plan would be worse than leaving it out: it reads as "a
// plan was made and it is empty", which is a claim nobody made.
func TestAnAlertWithoutAPlanIsShownWithoutOne(t *testing.T) {
	server, alerts, _ := alertsServer(t)
	alerts.Record([]agentruntime.Finding{
		finding("ANOMALY_TELEMETRY_STALE", "robot", "telemetry 已过期", "warning"),
	})

	decoded := getAlerts(t, server)
	if len(decoded.RunnerAlerts) != 1 {
		t.Fatalf("runner alerts = %d, want 1", len(decoded.RunnerAlerts))
	}
	if decoded.RunnerAlerts[0].Recovery != nil {
		t.Fatal("an alert nothing has planned for was shown with a recovery plan")
	}
	if decoded.ActiveCount != 1 {
		t.Fatalf("activeCount = %d, want 1", decoded.ActiveCount)
	}
}

// TestTheBannerCountsWhatIsStillTrue checks the number an operator sees against
// the list beside it, because a badge that disagrees with its own list is read as
// a rendering bug and then ignored.
func TestTheBannerCountsWhatIsStillTrue(t *testing.T) {
	server, alerts, _ := alertsServer(t)
	alerts.Record([]agentruntime.Finding{
		finding("ANOMALY_ACTION_FAILED", "a", "a 失败", "critical"),
		finding("ANOMALY_ACTION_FAILED", "b", "b 失败", "warning"),
	})

	first := getAlerts(t, server)
	if first.ActiveCount != 2 {
		t.Fatalf("activeCount = %d, want 2", first.ActiveCount)
	}

	// A condition that has cleared is no longer reported, and the count has to
	// follow it down. A banner that only ever grows stops being read.
	alerts.Record([]agentruntime.Finding{finding("ANOMALY_ACTION_FAILED", "a", "a 失败", "critical")})
	alerts.Record(nil)
	second := getAlerts(t, server)
	active := 0
	for _, alert := range second.RunnerAlerts {
		if alert.Active {
			active++
		}
	}
	if active != second.ActiveCount {
		t.Fatalf("activeCount = %d but %d entries are active", second.ActiveCount, active)
	}
}

// TestTheAlertResponseReportsSupervision keeps the "is anything watching" flag on
// the same response as the alerts. A deployment with observation switched off
// must not look like a deployment where nothing is wrong.
func TestTheAlertResponseReportsSupervision(t *testing.T) {
	server, _, _ := alertsServer(t)
	decoded := getAlerts(t, server)
	if decoded.Supervision == nil {
		t.Fatal("the alert response does not report whether anything is supervising")
	}
}

// TestTheConsoleSurvivesAFindingWithNoComponent covers the empty-component case,
// which is what a robot-wide finding looks like. It must produce an identity and
// a lookup that still work, rather than a missing plan or a panic.
func TestTheConsoleSurvivesAFindingWithNoComponent(t *testing.T) {
	server, alerts, _ := alertsServer(t)
	alerts.Record([]agentruntime.Finding{
		finding("ANOMALY_ABNORMAL_TASK", "", "有任务异常结束", "warning"),
	})
	identity := identityOf("ANOMALY_ABNORMAL_TASK", "")
	alerts.RememberPlan(identity, planFor(t, identity, "plan-abnormal", "有任务异常结束"))

	decoded := getAlerts(t, server)
	if len(decoded.RunnerAlerts) != 1 {
		t.Fatalf("runner alerts = %d, want 1", len(decoded.RunnerAlerts))
	}
	if decoded.RunnerAlerts[0].Recovery == nil {
		t.Fatalf("a robot-wide finding was shown without its plan: %#v", decoded.RunnerAlerts[0])
	}
	if decoded.RunnerAlerts[0].Recovery.PlanID != "plan-abnormal" {
		t.Fatalf("plan = %q, want the plan filed for the robot-wide identity", decoded.RunnerAlerts[0].Recovery.PlanID)
	}
}

// A plan made about something else is not shown.
//
// The ledger is append-only, so plans written before identities carried the
// component are still in a task's replay. Matching one against an alert by its
// bare code is how one component's plan came to be displayed under another
// component's finding, and a plan borrowed from a different problem is worse than
// no plan at all: it reads as an answer.
func TestAPlanAboutSomethingElseIsNotBorrowed(t *testing.T) {
	server, alerts, app := alertsServer(t)
	alerts.Record([]agentruntime.Finding{
		finding("ANOMALY_ACTION_FAILED", "observe_scene", "observe_scene 失败", "warning"),
	})
	// A plan filed under the bare code: the shape an older version wrote.
	app.WithRunnerAlertPlan(func(trigger string) (agentruntime.RunnerAlertPlan, bool) {
		if trigger == "ANOMALY_ACTION_FAILED" {
			return planFor(t, "ANOMALY_ACTION_FAILED", "plan-legacy", "navigation.navigate 失败"), true
		}
		return agentruntime.RunnerAlertPlan{}, false
	})

	decoded := getAlerts(t, server)
	if len(decoded.RunnerAlerts) != 1 {
		t.Fatalf("runner alerts = %d, want 1", len(decoded.RunnerAlerts))
	}
	if decoded.RunnerAlerts[0].Recovery != nil {
		t.Fatalf("an alert was shown a plan filed under a different identity: %#v",
			decoded.RunnerAlerts[0].Recovery)
	}
}
