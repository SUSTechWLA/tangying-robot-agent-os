package sqlite

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var _ tasks.Repository = (*Store)(nil)
var _ middleware.ExecutionStore = (*Store)(nil)

func TestExecutionRecordSurvivesReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	record := middleware.StepRecord{TaskID: "task-1", StepID: "pick", IdempotencyKey: "pick-1"}
	if err := store.MarkStepStarted(context.Background(), record); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	status, err := reopened.StepStatus(context.Background(), "task-1", "pick")
	if err != nil || status != middleware.StepStarted {
		t.Fatalf("status = %s, err = %v", status, err)
	}
}

// The clearing path for an unknown outcome.
//
// An unconfirmed physical step blocks readiness and forbids retrying it, and
// both are correct. What was missing was the other end: the operator who went and
// looked had nowhere to record what they saw, so the block never lifted and the
// report stopped being read. These tests pin that a person can end the wait, and
// that nothing else can.

func reconcileFixture(t *testing.T) (*Store, middleware.StepRecord) {
	t.Helper()
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	record := middleware.StepRecord{
		TaskID: "task-1", StepID: "pick", Capability: "manipulation.pick", SafetyLevel: "physical",
	}
	if err := store.MarkStepStarted(context.Background(), record); err != nil {
		t.Fatal(err)
	}
	return store, record
}

func TestAPersonsConclusionEndsTheWaitOnAnUnknownOutcome(t *testing.T) {
	store, record := reconcileFixture(t)
	ctx := context.Background()
	if err := store.ReconcileStep(ctx, record, middleware.StepReconciliation{
		Outcome: middleware.StepOutcomeNeverActed, Actor: "console-session:abc",
		Note: "夹爪是空的，杯子和任务开始时一样在桌上", RecordedAt: time.Now().UTC(),
	}); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	runs, err := store.ListStepRuns(ctx, record.TaskID)
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) != 1 || runs[0].Reconciled == nil {
		t.Fatalf("runs = %#v", runs)
	}
	got := runs[0].Reconciled
	if got.Outcome != middleware.StepOutcomeNeverActed || got.Actor != "console-session:abc" || got.Note == "" {
		t.Fatalf("the record does not say who concluded what and why: %#v", got)
	}
	// The step's own status still says the outcome was never recorded. That
	// remains true, and the record keeps saying it: what changed is that a person
	// has been and looked.
	status, err := store.StepStatus(ctx, record.TaskID, record.StepID)
	if err != nil {
		t.Fatal(err)
	}
	if status != middleware.StepStarted {
		t.Fatalf("status = %q, want the original %q", status, middleware.StepStarted)
	}
}

// An anonymous conclusion is not a conclusion. The decision that lets work
// continue is the one a later reader has to be able to disagree with, and
// "somebody" is not an answer.
func TestAConclusionNeedsAPersonAndAReason(t *testing.T) {
	store, record := reconcileFixture(t)
	ctx := context.Background()
	for name, reconciliation := range map[string]middleware.StepReconciliation{
		"no actor":         {Outcome: middleware.StepOutcomeHappened, Note: "看过了"},
		"no note":          {Outcome: middleware.StepOutcomeHappened, Actor: "console-session:abc"},
		"no outcome":       {Actor: "console-session:abc", Note: "看过了"},
		"invented outcome": {Outcome: middleware.StepOutcome("PROBABLY"), Actor: "console-session:abc", Note: "大概"},
	} {
		if err := store.ReconcileStep(ctx, record, reconciliation); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	runs, err := store.ListStepRuns(ctx, record.TaskID)
	if err != nil {
		t.Fatal(err)
	}
	if runs[0].Reconciled != nil {
		t.Fatal("a refused conclusion was written anyway")
	}
}

// The second person to look has to see that somebody already decided, rather
// than silently replacing their judgement with their own.
func TestAConclusionIsWrittenOnce(t *testing.T) {
	store, record := reconcileFixture(t)
	ctx := context.Background()
	first := middleware.StepReconciliation{
		Outcome: middleware.StepOutcomeHappened, Actor: "console-session:abc",
		Note: "杯子在收纳盘里", RecordedAt: time.Now().UTC(),
	}
	if err := store.ReconcileStep(ctx, record, first); err != nil {
		t.Fatal(err)
	}
	if err := store.ReconcileStep(ctx, record, middleware.StepReconciliation{
		Outcome: middleware.StepOutcomeAbandoned, Actor: "console-session:def",
		Note: "改主意了", RecordedAt: time.Now().UTC(),
	}); err == nil {
		t.Fatal("a second conclusion overwrote the first without saying so")
	}
	runs, err := store.ListStepRuns(ctx, record.TaskID)
	if err != nil {
		t.Fatal(err)
	}
	if runs[0].Reconciled.Outcome != middleware.StepOutcomeHappened {
		t.Fatalf("the first conclusion was replaced: %#v", runs[0].Reconciled)
	}
}

// A step that never started has no unknown outcome to reconcile, so accepting a
// conclusion for it would create the appearance of a decision about nothing.
func TestAStepThatWasNeverStartedCannotBeReconciled(t *testing.T) {
	store, _ := reconcileFixture(t)
	err := store.ReconcileStep(context.Background(), middleware.StepRecord{TaskID: "task-1", StepID: "never-ran"},
		middleware.StepReconciliation{
			Outcome: middleware.StepOutcomeHappened, Actor: "console-session:abc",
			Note: "看过了", RecordedAt: time.Now().UTC(),
		})
	if err == nil {
		t.Fatal("a conclusion was accepted for a step with no record")
	}
}
