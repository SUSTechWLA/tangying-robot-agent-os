package main

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

type closureObserver struct {
	observer *agentruntime.OpsAgent
	bus      *agentruntime.AgentRuntime
	sub      agentruntime.Subscription
	now      time.Time
	stale    bool
}

func newClosureObserver(t *testing.T, journal *recoveryInvestigationJournal, capacity int) *closureObserver {
	t.Helper()
	f := &closureObserver{observer: agentruntime.NewOpsAgent(), bus: agentruntime.New(), now: time.Now().UTC(), stale: true}
	t.Cleanup(f.bus.Close)
	var err error
	f.sub, err = f.bus.Subscribe("blocked-recovery-consumer", []string{agentcontract.TopicOpsAnomalyDetected, agentcontract.TopicOpsAnomalyCleared}, capacity)
	if err != nil {
		t.Fatal(err)
	}
	f.observer.Now = func() time.Time { return f.now }
	f.observer.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		at := f.now
		if f.stale {
			at = at.Add(-time.Minute)
		}
		return telemetry.Snapshot{RobotID: "robot", ObservedAt: at}, nil
	}
	f.observer.PersistClear = journal.closed
	f.observer.SetPublish(f.bus.Publish)
	f.observer.RunnerAlerts = agentruntime.NewAlertStore(time.Minute)
	f.observer.RunnerAlerts.SetClock(func() time.Time { return f.now })
	if err := f.observer.OnEvent(context.Background(), agentcontract.Event{Topic: agentcontract.TopicTaskStarted, TaskID: "task"}); err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *closureObserver) receive(t *testing.T) agentcontract.Event {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	event, err := f.sub.Receive(ctx)
	if err != nil {
		t.Fatal(err)
	}
	return event
}

func (f *closureObserver) observe(stale bool) {
	f.now = f.now.Add(time.Second)
	f.stale = stale
	f.observer.Observe(context.Background())
}

func TestRecoveryReopeningSurvivesPriorityOvertakeAndClearEviction(t *testing.T) {
	for _, capacity := range []int{8, 1} {
		name := "high-reopen-overtakes-low-clear"
		if capacity == 1 {
			name = "low-clear-evicted"
		}
		t.Run(name, func(t *testing.T) {
			store, service, _ := investigationFixture(t)
			journal := newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
			f := newClosureObserver(t, journal, capacity)
			f.observe(true)
			firstEvent := f.receive(t)
			first, ok := findingFromEvent(firstEvent)
			if !ok {
				t.Fatalf("invalid original finding: %+v", firstEvent)
			}
			reserved := reserveInvestigation(t, journal, first, true)
			// The recovery consumer is now blocked. Ops evaluates a real clear
			// then recurrence two seconds later, inside both former cooldowns.
			f.observe(false)
			f.observe(true)
			reopened := f.receive(t)
			if reopened.Topic != agentcontract.TopicOpsAnomalyDetected || reopened.Priority != agentcontract.PriorityHigh {
				t.Fatalf("bus lost normal priority ordering: %+v", reopened)
			}
			second, ok := findingFromEvent(reopened)
			if !ok {
				t.Fatalf("invalid reopened finding: %+v", reopened)
			}
			// A fresh journal proves the successful boundary is in the DB, not
			// a subscriber callback or private observer memory.
			journal = newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
			next := reserveInvestigation(t, journal, second, true)
			if next.InvestigationID == reserved.InvestigationID {
				t.Fatal("reopening reused the consumed reservation")
			}
			reserveInvestigation(t, journal, second, false)
			if capacity == 8 {
				cleared := f.receive(t)
				if cleared.Topic != agentcontract.TopicOpsAnomalyCleared || !cleared.OccurredAt.Before(reopened.OccurredAt) {
					t.Fatalf("pending clear chronology lost: %+v", cleared)
				}
				if err := journal.closed(context.Background(), cleared); err != nil {
					t.Fatal(err)
				}
				reserveInvestigation(t, journal, second, false)
			} else if f.bus.Dropped() == 0 {
				t.Fatal("test did not evict its low-priority clear")
			}
		})
	}
}

func TestRecoveryOldQueuedDetectionCannotConsumeReopeningEpisode(t *testing.T) {
	store, service, _ := investigationFixture(t)
	journal := newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
	f := newClosureObserver(t, journal, 8)
	f.observe(true)
	firstEvent := f.receive(t)
	first, ok := findingFromEvent(firstEvent)
	if !ok {
		t.Fatal("initial finding invalid")
	}
	reserveInvestigation(t, journal, first, true)
	// A standing-condition report at t60 is queued while the consumer is
	// occupied. Its original episode was already reserved at t0.
	f.now = firstEvent.OccurredAt.Add(59 * time.Second)
	f.observe(true)
	f.observe(false) // t61: synchronously persists the clear before queueing low.
	f.now = firstEvent.OccurredAt.Add(99 * time.Second)
	f.observe(true) // t100: an actual recurrence; no consumer has drained yet.
	oldEvent := f.receive(t)
	if oldEvent.Topic != agentcontract.TopicOpsAnomalyDetected || !oldEvent.OccurredAt.Equal(firstEvent.OccurredAt.Add(time.Minute)) {
		t.Fatalf("test did not retain old high-priority report: %+v", oldEvent)
	}
	// Read through a fresh journal as after a restart. The envelope timestamp
	// must survive adaptation; neither payload claims nor consume-time is used.
	journal = newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
	oldEvent.Payload["reportedAt"] = f.now.Add(time.Hour)
	oldEvent.Payload["detectedAt"] = f.now.Add(time.Hour)
	old, ok := findingFromEvent(oldEvent)
	if !ok || !old.ReportedAt.Equal(oldEvent.OccurredAt) {
		t.Fatal("reported time did not come from the original envelope")
	}
	reserveInvestigation(t, journal, old, false)
	_, boundary, err := journal.loadClosure(context.Background(), old.TaskID, old.Identity())
	if err != nil || boundary.EventID == "" {
		t.Fatalf("missing durable boundary: %+v %v", boundary, err)
	}
	for _, uncertainTime := range []time.Time{{}, boundary.OccurredAt} {
		ambiguous := old
		ambiguous.ReportedAt = uncertainTime
		reserveInvestigation(t, journal, ambiguous, false)
	}
	reopenedEvent := f.receive(t)
	if reopenedEvent.Topic != agentcontract.TopicOpsAnomalyDetected || !reopenedEvent.OccurredAt.Equal(firstEvent.OccurredAt.Add(100*time.Second)) {
		t.Fatalf("actual recurrence missing: %+v", reopenedEvent)
	}
	reopened, ok := findingFromEvent(reopenedEvent)
	if !ok {
		t.Fatal("reopened finding invalid")
	}
	reserveInvestigation(t, journal, reopened, true)
	// Arbitrarily newer report time still cannot mint a third pass.
	reopened.ReportedAt = reopened.ReportedAt.Add(time.Hour)
	reserveInvestigation(t, journal, reopened, false)
	cleared := f.receive(t)
	if cleared.Topic != agentcontract.TopicOpsAnomalyCleared {
		t.Fatal("ordinary low-priority delivery changed")
	}
}

func TestRecoveryFailedClearRetainsOriginalFactAndActiveReopening(t *testing.T) {
	store, service, _ := investigationFixture(t)
	journal := newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
	f := newClosureObserver(t, journal, 8)
	var published []agentcontract.Event
	f.bus.SetEventSink(func(_ context.Context, event agentcontract.Event) { published = append(published, event) })
	f.observe(true)
	initial, ok := findingFromEvent(f.receive(t))
	if !ok {
		t.Fatal("missing initial finding")
	}
	reserveInvestigation(t, journal, initial, true)
	blocked := true
	var attempted agentcontract.Event
	f.observer.PersistClear = func(ctx context.Context, event agentcontract.Event) error {
		if attempted.ID == "" {
			attempted = event
		}
		if event.ID != attempted.ID || !event.OccurredAt.Equal(attempted.OccurredAt) {
			t.Fatal("retry rewrote the original clear identity or observation time")
		}
		if blocked {
			return errors.New("disk unavailable")
		}
		return journal.closed(ctx, event)
	}
	f.observe(false) // Real clear, but its persistence fails.
	if health := f.observer.Health(context.Background()); health.ReasonCode != "OPS_CLEAR_NOT_PERSISTED" || health.Status != agentcontract.HealthDegraded {
		t.Fatalf("missing degraded state: %+v", health)
	}
	f.observe(true) // The issue is already active again while persistence fails.
	reserveInvestigation(t, journal, initial, false)
	for _, event := range published {
		if event.Topic == agentcontract.TopicOpsAnomalyCleared {
			t.Fatal("failed durable edge was published as cleared")
		}
	}
	for _, alert := range f.observer.RunnerAlerts.Alerts() {
		if alert.Code == agentruntime.AnomalyTelemetryStale && !alert.Active {
			t.Fatal("pending historical clear hid the current active alert")
		}
	}
	blocked = false
	f.observe(true) // Persist old fact before re-reporting the current recurrence.
	reopened := f.receive(t)
	if reopened.Topic != agentcontract.TopicOpsAnomalyDetected {
		t.Fatalf("reopen was swallowed by cooldown: %+v", reopened)
	}
	cleared := f.receive(t)
	if cleared.ID != attempted.ID || !cleared.OccurredAt.Equal(attempted.OccurredAt) || !cleared.OccurredAt.Before(reopened.OccurredAt) {
		t.Fatalf("old clear represented as current resolution: clear=%+v reopen=%+v", cleared, reopened)
	}
	second, ok := findingFromEvent(reopened)
	if !ok {
		t.Fatal("reopened finding invalid")
	}
	reserveInvestigation(t, journal, second, true)
	if health := f.observer.Health(context.Background()); health.ReasonCode == "OPS_CLEAR_NOT_PERSISTED" {
		t.Fatal("successful persistence did not recover observer health")
	}
	active := false
	for _, alert := range f.observer.RunnerAlerts.Alerts() {
		if alert.Code == agentruntime.AnomalyTelemetryStale && alert.Active {
			active = true
		}
	}
	if !active {
		t.Fatal("current telemetry alert disappeared after historical clear persisted")
	}
}

func TestRecoveryClearJournalRejectsUnregisteredIdentity(t *testing.T) {
	store, service, _ := investigationFixture(t)
	journal := newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
	for _, field := range []string{"agent", "version", "protocol", "id"} {
		event := investigationClear(failedInvestigationFinding(), "clear", time.Now())
		switch field {
		case "agent":
			event.Agent = "model"
		case "version":
			event.AgentVersion = "unknown"
		case "protocol":
			event.ProtocolVersion = "unknown"
		case "id":
			event.ID = ""
		}
		if err := journal.closed(context.Background(), event); err == nil {
			t.Fatalf("unbound %s accepted", field)
		}
	}
}
