package main

import (
	"context"
	"errors"
	"path/filepath"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/autorecovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func investigationFixture(t *testing.T) (*sqlite.Store, *tasks.Service, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "investigation.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	if err := store.Create(context.Background(), &tasks.Task{ID: "task", State: taskgraph.StateRecoverableFailure, CurrentRevision: 3,
		Events: []tasks.TaskEvent{{Sequence: 1, Type: "CAPABILITY_FAILED"}}}); err != nil {
		t.Fatal(err)
	}
	return store, tasks.NewService(store, nil), path
}

func failedInvestigationFinding() agentruntime.Finding {
	return agentruntime.Finding{TaskID: "task", Code: agentruntime.AnomalyActionFailed, Component: "navigation.status",
		Binding: agentcontract.RecoveryBinding{TaskID: "task", TaskRevision: 3, RobotID: "robot", StepID: "step", CommandID: "command", SourceEventID: "failed-event", AnomalyEventID: "anomaly-1"}}
}

func reserveInvestigation(t *testing.T, journal *recoveryInvestigationJournal, finding agentruntime.Finding, want bool) agentruntime.Finding {
	t.Helper()
	admitted, err := journal.reserve(context.Background(), &finding)
	if err != nil || admitted != want {
		t.Fatalf("reservation = %v, %v; want %v", admitted, err, want)
	}
	if admitted && finding.InvestigationID == "" {
		t.Fatal("reservation lost its local episode ID")
	}
	return finding
}

func TestInvestigationExactSourceIsDurableEvenWithoutACompletion(t *testing.T) {
	store, service, path := investigationFixture(t)
	journal := newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
	finding := failedInvestigationFinding()
	reserved := reserveInvestigation(t, journal, finding, true)
	// Simulate a crash after reservation but before planning or execution.
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	journal = newRecoveryInvestigationJournal(tasks.NewService(reopened, nil), reopened, agentruntime.DefaultRecoveryCatalog())
	finding.Binding.AnomalyEventID = "new-observer-report"
	finding.Binding.PlanEventID = "another-plan"
	finding.Facts = map[string]any{"ageSeconds": 800}
	reserveInvestigation(t, journal, finding, false)
	checkpoint, exists, err := reopened.LatestCheckpoint(context.Background(), reserved.InvestigationID)
	if err != nil || !exists || checkpoint.Version != 1 {
		t.Fatalf("reservation checkpoint = %+v, %v, %v", checkpoint, exists, err)
	}
	if err := journal.closed(context.Background(), investigationClear(finding, "clear", time.Now())); err != nil {
		t.Fatal(err)
	}
	reserveInvestigation(t, journal, finding, false) // A clear edge never cancels an exact UNKNOWN source.
	for _, field := range []string{"task", "revision", "robot", "step", "command", "source"} {
		next := finding
		switch field {
		case "task":
			next.TaskID, next.Binding.TaskID = "other-task", "other-task"
		case "revision":
			next.Binding.TaskRevision++
		case "robot":
			next.Binding.RobotID = "other-robot"
		case "step":
			next.Binding.StepID = "other-step"
		case "command":
			next.Binding.CommandID = "other-command"
		case "source":
			next.Binding.SourceEventID = "other-source"
		}
		reserveInvestigation(t, journal, next, true)
		reserveInvestigation(t, journal, next, false)
	}
}

func TestInvestigationConcurrentProcessesReserveOnlyOnePass(t *testing.T) {
	store, service, path := investigationFixture(t)
	second, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	journals := []*recoveryInvestigationJournal{newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog()), newRecoveryInvestigationJournal(tasks.NewService(second, nil), second, agentruntime.DefaultRecoveryCatalog())}
	var admitted atomic.Int32
	var wg sync.WaitGroup
	for i := range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			finding := failedInvestigationFinding()
			ok, _ := journals[i%2].reserve(context.Background(), &finding)
			// Lock contention may fail closed; it must never grant a second pass.
			if ok {
				admitted.Add(1)
			}
		}()
	}
	wg.Wait()
	if admitted.Load() != 1 {
		t.Fatalf("concurrent passes = %d", admitted.Load())
	}
	reserveInvestigation(t, journals[1], failedInvestigationFinding(), false)
}

func investigationClear(finding agentruntime.Finding, id string, at time.Time) agentcontract.Event {
	return agentcontract.Event{ID: id, Topic: agentcontract.TopicOpsAnomalyCleared, TaskID: finding.TaskID, OccurredAt: at,
		Agent: agentruntime.OpsAgentName, AgentVersion: agentruntime.OpsAgentVersion, ProtocolVersion: agentcontract.EventProtocolV1,
		Payload: map[string]any{"code": finding.Code, "component": finding.Component}}
}

func TestInvestigationStandingConditionUsesBusinessWatermarkAndClearEpoch(t *testing.T) {
	store, service, _ := investigationFixture(t)
	journal := newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
	finding := agentruntime.Finding{TaskID: "task", Code: agentruntime.AnomalyAbnormalTask,
		Facts: map[string]any{"ageSeconds": 10, "taskCount": 1, "uncertainSteps": "b,a", "timestamp": "old"}}
	first := reserveInvestigation(t, journal, finding, true)
	for i := range 3 {
		finding.Facts["ageSeconds"], finding.Facts["timestamp"], finding.Facts["taskCount"] = 99+i, time.Now(), i+2
		finding.Facts["uncertainSteps"] = "a,b"
		finding.Evidence = []string{"new-frame"}
		if _, err := service.AppendEvent(context.Background(), "task", tasks.TaskEvent{Type: "ops.recovery_executed"}); err != nil {
			t.Fatal(err)
		}
		reserveInvestigation(t, journal, finding, false)
	}
	if _, err := service.AppendEvent(context.Background(), "task", tasks.TaskEvent{Type: "TOOL_ACTIVITY", Payload: map[string]any{"status": "FAILED"}}); err != nil {
		t.Fatal(err)
	}
	second := reserveInvestigation(t, journal, finding, true)
	if first.InvestigationID == second.InvestigationID {
		t.Fatal("new business source did not create a new episode")
	}
	if second.Binding != (agentcontract.RecoveryBinding{}) {
		t.Fatal("unknown binding was synthesized from task index")
	}
	reserveInvestigation(t, journal, finding, false)
	at := time.Now().UTC()
	clear := investigationClear(finding, "clear-1", at)
	if err := journal.closed(context.Background(), clear); err != nil {
		t.Fatal(err)
	}
	journal = newRecoveryInvestigationJournal(service, store, agentruntime.DefaultRecoveryCatalog())
	finding.ReportedAt = at.Add(time.Millisecond)
	reserveInvestigation(t, journal, finding, true)
	if err := journal.closed(context.Background(), clear); err != nil {
		t.Fatal(err)
	}
	if err := journal.closed(context.Background(), investigationClear(finding, "stale-clear", at.Add(-time.Second))); err != nil {
		t.Fatal(err)
	}
	reserveInvestigation(t, journal, finding, false)
	if err := journal.closed(context.Background(), investigationClear(finding, "clear-2", at.Add(time.Second))); err != nil {
		t.Fatal(err)
	}
	finding.ReportedAt = at.Add(2 * time.Second)
	reserveInvestigation(t, journal, finding, true)
}

func TestInvestigationCatalogChangeAllowsOneNewPass(t *testing.T) {
	store, service, _ := investigationFixture(t)
	old := agentruntime.DefaultRecoveryCatalog()
	finding := failedInvestigationFinding()
	reserveInvestigation(t, newRecoveryInvestigationJournal(service, store, old), finding, true)
	actions := old.Actions()
	for left, right := 0, len(actions)-1; left < right; left, right = left+1, right-1 {
		actions[left], actions[right] = actions[right], actions[left]
	}
	reordered, err := agentruntime.NewRecoveryCatalog(actions...)
	if err != nil {
		t.Fatal(err)
	}
	reserveInvestigation(t, newRecoveryInvestigationJournal(service, store, reordered), finding, false)
	actions = append(actions, agentruntime.RecoveryAction{ID: "observe.new-read", Risk: agentruntime.RiskReadOnly, Tools: []string{"new.read"}})
	changed, err := agentruntime.NewRecoveryCatalog(actions...)
	if err != nil {
		t.Fatal(err)
	}
	reserveInvestigation(t, newRecoveryInvestigationJournal(service, store, changed), finding, true)
	reserveInvestigation(t, newRecoveryInvestigationJournal(service, store, changed), finding, false)
}

type failedReservationStore struct{ eventlog.Store }

func (s failedReservationStore) Commit(context.Context, eventlog.CommitRequest) error {
	return errors.New("disk reservation failed")
}

type investigationExecutor struct{ calls int }

func (e *investigationExecutor) Execute(_ context.Context, request recoveryexec.Request) (recoveryexec.Result, error) {
	e.calls++
	return recoveryexec.Result{ActionID: request.Action.ID, Executed: false, Verified: false}, errors.New("diagnostic provider unavailable")
}

func TestInvestigationGatePrecedesReadsAndDoesNotLoseNewEpisodeToCooldown(t *testing.T) {
	store, service, _ := investigationFixture(t)
	catalog := agentruntime.DefaultRecoveryCatalog()
	recovery := agentruntime.NewRecoveryAgent(catalog)
	reads := 0
	recovery.ReadFacts = func(context.Context, string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		reads++
		return agentruntime.RecoveryFacts{}, nil, nil
	}
	executor := &investigationExecutor{}
	automatic := &autorecovery.Supervisor{Catalog: catalog, Executor: executor}
	finding := agentruntime.Finding{TaskID: "task", Code: agentruntime.AnomalyTelemetryStale}
	broken := newRecoveryInvestigationJournal(service, failedReservationStore{Store: store}, catalog)
	if err := investigateRecovery(context.Background(), broken, recovery, automatic, finding); err == nil {
		t.Fatal("reservation error was hidden")
	}
	if reads != 0 || executor.calls != 0 {
		t.Fatalf("failed reserve dispatched reads=%d calls=%d", reads, executor.calls)
	}
	journal := newRecoveryInvestigationJournal(service, store, catalog)
	for range 4 {
		if err := investigateRecovery(context.Background(), journal, recovery, automatic, finding); err != nil {
			t.Fatal(err)
		}
	}
	if reads != 1 || executor.calls != 1 {
		t.Fatalf("failed diagnostic repeated: reads=%d calls=%d", reads, executor.calls)
	}
	if _, err := service.AppendEvent(context.Background(), "task", tasks.TaskEvent{Type: "STATE_CHANGED"}); err != nil {
		t.Fatal(err)
	}
	if err := investigateRecovery(context.Background(), journal, recovery, automatic, finding); err != nil {
		t.Fatal(err)
	}
	if reads != 2 || executor.calls != 2 {
		t.Fatalf("new episode swallowed by old cooldown: reads=%d calls=%d", reads, executor.calls)
	}
	// The old unreserved advisory API retains its cooldown and is not gated by
	// automatic-pass reservations, as are direct operator execution requests.
	if plan := recovery.Recover(context.Background(), finding); plan == nil {
		t.Fatal("explicit advisory planning was blocked by an automatic reservation")
	}
	if plan := recovery.Recover(context.Background(), finding); plan != nil {
		t.Fatal("legacy cooldown disappeared")
	}
}

type historyIndexOnlyStore struct{ *tasks.MemoryStore }

func (*historyIndexOnlyStore) List(context.Context) ([]*tasks.Task, error) {
	return nil, errors.New("full history list called")
}
func (*historyIndexOnlyStore) Get(context.Context, string) (*tasks.Task, error) {
	return nil, errors.New("full history get called")
}

func TestObserverIndexDoesNotHideFailedTasksOrLoadTheirSource(t *testing.T) {
	store := tasks.NewMemoryStore()
	for id, state := range map[string]taskgraph.TaskState{"failed": taskgraph.StateRecoverableFailure, "active": taskgraph.StateExecuting, "done": taskgraph.StateSucceeded} {
		if err := store.Create(context.Background(), &tasks.Task{ID: id, State: state}); err != nil {
			t.Fatal(err)
		}
	}
	history := taskHistory{service: tasks.NewService(&historyIndexOnlyStore{store}, nil)}
	ids, err := history.TaskIDs(context.Background())
	if err != nil || !reflect.DeepEqual(ids, []string{"active", "done", "failed"}) {
		t.Fatalf("IDs = %v, %v", ids, err)
	}
	ids, err = history.Abnormal(context.Background())
	if err != nil || !reflect.DeepEqual(ids, []string{"failed"}) {
		t.Fatalf("abnormal IDs = %v, %v", ids, err)
	}
	facts, trail, err := recoveryFactsReader(history.service, nil, nil)(context.Background(), "failed")
	if err != nil || facts.TaskState != string(taskgraph.StateRecoverableFailure) || !facts.ReconciliationUnavailable {
		t.Fatalf("indexed task state or unknown physical guard lost: %+v, %v; trail=%+v", facts, err, trail)
	}
}
