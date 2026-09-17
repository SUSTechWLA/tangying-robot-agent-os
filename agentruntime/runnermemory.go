package agentruntime

import (
	"sort"
	"strings"
	"sync"
	"time"
)

// The runner-level alert store.
//
// Findings come in two scopes and only one of them had anywhere to go. A finding
// about a task is written into that task's ledger and shows up in the replay. A
// finding about the *robot* — an emergency stop, a blocked module from the fault
// ledger, a stale observation — belongs to no task, and an earlier version gave it
// an empty task id, which meant it was published to nobody and recorded nowhere.
//
// That is the wrong way round for the findings that matter most: an emergency
// stop is not a task problem, and it is the one thing an operator must hear about
// whether or not a task is running.
//
// So robot-level findings are kept here. It is deliberately small and in-memory:
// this is the live view, not the record. The record of what happened is the
// incident bundles and the event ledger; an alert whose condition has cleared
// should disappear, and a durable store of resolved alerts would only be a second
// history to keep consistent with the first.

// RunnerAlert is one current finding about the robot as a whole.
type RunnerAlert struct {
	// ID is stable for a given condition, so a repeat does not read as new.
	ID       string `json:"id"`
	Code     string `json:"code"`
	Severity string `json:"severity"`
	// Component names what the finding is about: a module id, or "estop", or
	// "telemetry".
	Component string `json:"component"`
	Message   string `json:"message"`
	// DetectedAt is when the condition was last reported.
	DetectedAt time.Time `json:"detectedAt"`
	// Active is false once nothing has re-reported the condition inside its
	// lifetime. A condition that is still true is re-evaluated on every tick, so
	// silence means it went away.
	Active                  bool     `json:"active"`
	RecommendedActions      []string `json:"recommendedActions,omitempty"`
	AutomaticRetryForbidden bool     `json:"automaticRetryForbidden,omitempty"`
	MissingEvidence         []string `json:"missingEvidence,omitempty"`
	EvidenceIDs             []string `json:"evidenceIds,omitempty"`
}

// AlertStore holds the current robot-level findings.
type AlertStore struct {
	mu sync.Mutex
	// plans holds the newest recovery plan per trigger, so a robot-level alert
	// can be shown with the proposal made for it.
	//
	// It lives here rather than in the task ledger because these findings belong
	// to no task: the ledger is per task, and a plan for a robot-wide finding has
	// no ledger row to sit in.
	plans map[string]RunnerAlertPlan
	// findings is keyed by identity. The value is the last report.
	findings map[string]RunnerAlert
	// ttl is how long a finding stays active without being re-reported. It must
	// be longer than the evaluation interval, or a finding would flicker between
	// active and resolved on every tick.
	ttl time.Duration
	now func() time.Time
}

// DefaultAlertTTL is three evaluation intervals: long enough that a finding
// re-reported every tick never flickers, short enough that a cleared condition
// stops being shown promptly.
const DefaultAlertTTL = 3 * DefaultHealthInterval

// NewAlertStore creates an empty store.
func NewAlertStore(ttl time.Duration) *AlertStore {
	if ttl <= 0 {
		ttl = DefaultAlertTTL
	}
	return &AlertStore{
		findings: map[string]RunnerAlert{}, plans: map[string]RunnerAlertPlan{},
		ttl: ttl, now: time.Now,
	}
}

// Record stores the findings from one evaluation.
//
// It replaces the whole set rather than adding to it: an evaluation reports
// everything it can currently see, so a condition that is no longer reported is
// no longer true. Merging would let a fixed fault linger.
func (s *AlertStore) Record(findings []Finding) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now().UTC()
	for _, finding := range findings {
		// Task-scoped findings belong to the task ledger, where the replay can
		// show them next to the work they are about. Duplicating them here would
		// put the same finding in two lists with two lifetimes.
		if finding.TaskID != "" {
			continue
		}
		id := finding.Code + "@" + finding.Component
		s.findings[id] = RunnerAlert{
			ID: id, Code: finding.Code, Severity: finding.Severity,
			Component: finding.Component, Message: finding.Message,
			DetectedAt: now, Active: true,
			RecommendedActions:      append([]string(nil), finding.RecommendedActions...),
			AutomaticRetryForbidden: finding.AutomaticRetryForbidden,
			MissingEvidence:         append([]string(nil), finding.MissingEvidence...),
			EvidenceIDs:             append([]string(nil), finding.Evidence...),
		}
	}
	s.expireLocked(now)
}

// expireLocked marks findings resolved once they stop being re-reported.
func (s *AlertStore) expireLocked(now time.Time) {
	for id, alert := range s.findings {
		if now.Sub(alert.DetectedAt) > s.ttl {
			alert.Active = false
			s.findings[id] = alert
		}
	}
}

// Alerts returns the current findings, active first then by severity.
func (s *AlertStore) Alerts() []RunnerAlert {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expireLocked(s.now().UTC())
	alerts := make([]RunnerAlert, 0, len(s.findings))
	for _, alert := range s.findings {
		alerts = append(alerts, alert)
	}
	sort.SliceStable(alerts, func(i, j int) bool {
		if alerts[i].Active != alerts[j].Active {
			return alerts[i].Active
		}
		left, right := runnerSeverityRank(alerts[i].Severity), runnerSeverityRank(alerts[j].Severity)
		if left != right {
			return left > right
		}
		return alerts[i].ID < alerts[j].ID
	})
	return alerts
}

// SetClock replaces the clock. Tests use it; production leaves it alone.
func (s *AlertStore) SetClock(now func() time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.now = now
}

func runnerSeverityRank(severity string) int {
	switch strings.ToLower(severity) {
	case "critical":
		return 3
	case "warning":
		return 2
	default:
		return 1
	}
}

// RunnerAlertPlan is a recovery plan attached to a robot-level finding.
//
// It mirrors the fields a console shows, including the investigation, because the
// same requirement applies here as for a task finding: an operator asked to
// approve an action must be able to see what was consulted and how it was judged.
type RunnerAlertPlan struct {
	PlanID         string
	Verdict        string
	Diagnosis      string
	Confidence     float64
	Source         string
	EscalateReason string
	Steps          []RunnerAlertPlanStep
	Refused        []RunnerAlertRefusal
	Investigation  *Trail
}

// RunnerAlertPlanStep is one proposed action.
type RunnerAlertPlanStep struct {
	Order            int
	Action           string
	Summary          string
	Risk             string
	RequiresApproval bool
	Why              string
}

// RunnerAlertRefusal is one action the system will not perform, and why.
type RunnerAlertRefusal struct {
	Action  string
	Summary string
	Reason  string
}

// RememberPlan stores the newest plan for a trigger, so the alert it answers can
// be shown with it.
func (s *AlertStore) RememberPlan(trigger string, plan RunnerAlertPlan) {
	if trigger == "" {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.plans == nil {
		s.plans = map[string]RunnerAlertPlan{}
	}
	s.plans[trigger] = plan
}

// PlanFor returns the plan recorded for a trigger.
func (s *AlertStore) PlanFor(trigger string) (RunnerAlertPlan, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	plan, ok := s.plans[trigger]
	return plan, ok
}
