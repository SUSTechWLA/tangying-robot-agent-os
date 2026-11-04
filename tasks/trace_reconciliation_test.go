package tasks_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestTraceReconciliationsPersistAcrossSQLiteReopenWithoutClearingUnknown(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "trace-reconciliation.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	for _, status := range []string{"RUNNING", "FAILED"} {
		_, err = service.AppendEvent(ctx, task.ID, tasks.TaskEvent{Type: "TOOL_ACTIVITY", StepID: "navigate", Payload: map[string]any{
			"taskRevision": uint64(1), "robotId": "robot", "stepId": "navigate", "commandId": task.ID + "/navigate",
			"toolName": "navigation.navigate", "activityStatus": status, "mutatesWorld": true, "outcomeUnknown": status == "FAILED",
		}})
		if err != nil {
			t.Fatal(err)
		}
	}
	record := middleware.StepRecord{TaskID: task.ID, StepID: "navigate", Capability: "navigation.navigate", IdempotencyKey: task.ID + "/navigate", SafetyLevel: "physical_motion"}
	if err = store.MarkStepStarted(ctx, record); err != nil {
		t.Fatal(err)
	}
	before, err := service.Trace(ctx, task.ID, store)
	if err != nil {
		t.Fatal(err)
	}
	if !before.Source.ReconciliationAvailable || before.Reconciliations == nil || len(before.Reconciliations) != 0 {
		t.Fatalf("available empty history misrepresented: %+v", before)
	}
	traceHas(t, *before, "STEP_OUTCOME_UNKNOWN")
	conclusion := middleware.StepReconciliation{Outcome: middleware.StepOutcomeAbandoned, Actor: "fixture-operator", Note: "current observation only; failed action remains unknown", RecordedAt: time.Date(2026, 11, 4, 5, 17, 0, 0, time.UTC)}
	if err = store.ReconcileStep(ctx, record, conclusion); err != nil {
		t.Fatal(err)
	}
	after, err := service.Trace(ctx, task.ID, store)
	if err != nil {
		t.Fatal(err)
	}
	if !after.Source.ReconciliationAvailable || len(after.Reconciliations) != 1 || after.Source.SnapshotSHA256 == before.Source.SnapshotSHA256 {
		t.Fatalf("conclusion missing from digest: before=%+v after=%+v", before.Source, after.Source)
	}
	got := after.Reconciliations[0]
	if got.StepID != record.StepID || got.Capability != record.Capability || got.OriginalStatus != string(middleware.StepStarted) || got.Outcome != string(conclusion.Outcome) || got.Actor != conclusion.Actor || got.Note != conclusion.Note || !got.RecordedAt.Equal(conclusion.RecordedAt) {
		t.Fatalf("conclusion provenance changed: %+v", got)
	}
	traceHas(t, *after, "STEP_OUTCOME_UNKNOWN")
	if len(after.Steps) != 1 || after.Steps[0].Status != "FAILED" || !after.Steps[0].OutcomeUnknown {
		t.Fatalf("operator conclusion rewrote execution: %+v", after.Steps)
	}
	beforeEvents, _ := json.Marshal(before.Events)
	afterEvents, _ := json.Marshal(after.Events)
	if string(beforeEvents) != string(afterEvents) || before.State != after.State {
		t.Fatal("read-only history projection changed task")
	}
	if err = store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	restored, err := tasks.NewService(reopened, nil).Trace(ctx, task.ID, reopened)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := json.Marshal(after)
	b, _ := json.Marshal(restored)
	if string(a) != string(b) || restored.Source.SnapshotSHA256 != after.Source.SnapshotSHA256 {
		t.Fatalf("reopen changed trace: before=%s after=%s", a, b)
	}
	unrelated, err := reopened.ListReconciliations(ctx, "other-task")
	if err != nil || len(unrelated) != 0 {
		t.Fatalf("cross-task reconciliation exposed: %+v %v", unrelated, err)
	}
}

type reconciliationTraceStore struct {
	tasks.Repository
	reads   int
	mode    string
	failure error
}

func (s *reconciliationTraceStore) ListReconciliations(context.Context, string) ([]tasks.LocalReconciliation, error) {
	s.reads++
	if s.failure != nil {
		return nil, s.failure
	}
	if s.mode == "empty" {
		return nil, nil
	}
	version := s.reads
	if s.mode == "settles" && version > 2 {
		version = 2
	}
	return []tasks.LocalReconciliation{{StepID: "navigate", OriginalStatus: "STARTED", Outcome: "ABANDONED", Actor: "fixture", Note: fmt.Sprint(version)}}, nil
}

func reconciliationTraceFixture(t *testing.T) (tasks.Repository, string) {
	t.Helper()
	base := tasks.NewMemoryStore()
	task, err := tasks.NewService(base, intent.NewDeterministicParser()).Create(context.Background(), "把红色杯子放进右侧收纳盒", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	return base, task.ID
}

func TestTraceReconciliationChangeRetriesThenReturnsOnlyStableHistory(t *testing.T) {
	base, id := reconciliationTraceFixture(t)
	moving := &reconciliationTraceStore{Repository: base, mode: "settles"}
	result, err := tasks.NewService(moving, nil).Trace(context.Background(), id, nil)
	if err != nil || result == nil || !result.Source.Stable || !result.Source.ReconciliationAvailable || moving.reads != 4 || len(result.Reconciliations) != 1 || result.Reconciliations[0].Note != "2" {
		t.Fatalf("mixed history returned: %+v err=%v reads=%d", result, err, moving.reads)
	}
}

func TestTraceContinuallyChangingReconciliationRefusesMixedSnapshot(t *testing.T) {
	base, id := reconciliationTraceFixture(t)
	moving := &reconciliationTraceStore{Repository: base}
	result, err := tasks.NewService(moving, nil).Trace(context.Background(), id, nil)
	if result != nil || !errors.Is(err, tasks.ErrTraceBusy) || moving.reads != 6 {
		t.Fatalf("mixed reconciliation exposed: %+v %v reads=%d", result, err, moving.reads)
	}
}

func TestTraceDistinguishesUnavailableReconciliationFromVerifiedEmpty(t *testing.T) {
	base, id := reconciliationTraceFixture(t)
	unavailable, err := tasks.NewService(base, nil).Trace(context.Background(), id, nil)
	if err != nil || unavailable.Source.ReconciliationAvailable || unavailable.Reconciliations != nil {
		t.Fatalf("unsupported repository fabricated audit availability: %+v %v", unavailable, err)
	}
	supported := &reconciliationTraceStore{Repository: base, mode: "empty"}
	empty, err := tasks.NewService(supported, nil).Trace(context.Background(), id, nil)
	if err != nil || !empty.Source.ReconciliationAvailable || empty.Reconciliations == nil || len(empty.Reconciliations) != 0 || empty.Source.SnapshotSHA256 == unavailable.Source.SnapshotSHA256 {
		t.Fatalf("available empty history lost: %+v %v", empty, err)
	}
}

func TestTraceReconciliationReadFailureIsNotAnEmptyHistory(t *testing.T) {
	base, id := reconciliationTraceFixture(t)
	cause := errors.New("reconciliation storage unavailable")
	broken := &reconciliationTraceStore{Repository: base, failure: cause}
	result, err := tasks.NewService(broken, nil).Trace(context.Background(), id, nil)
	if result != nil || !errors.Is(err, cause) {
		t.Fatalf("failed audit read accepted: %+v %v", result, err)
	}
}
