package sqlite

import (
	"context"
	"errors"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestTaskSummarySurvivesReopenAndDoesNotDecodeHistory(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "summary.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "failed", State: taskgraph.StateRecoverableFailure, CurrentRevision: 7,
		Events: []tasks.TaskEvent{{Sequence: 1, Type: "CAPABILITY_FAILED", Payload: map[string]any{"source": strings.Repeat("x", 2<<20)}},
			{Sequence: 2, Type: "ops.recovery_executed"}, {Sequence: 3, Type: "agent.health_changed"}}}
	if err := store.Create(ctx, task); err != nil {
		t.Fatal(err)
	}
	full, err := store.Get(ctx, task.ID)
	if err != nil || len(full.Events) != 3 || full.Events[0].Payload["source"] != task.Events[0].Payload["source"] {
		t.Fatalf("full source unavailable: %v", err)
	}
	before, err := store.GetSummary(ctx, task.ID)
	if err != nil || before.State != taskgraph.StateRecoverableFailure || before.CurrentRevision != 7 || before.EventSequence != 3 || before.SourceSequence != 1 {
		t.Fatalf("summary = %+v, %v", before, err)
	}
	// Invalid archived JSON proves this path reads scalars, not the growing blob.
	if _, err := store.db.ExecContext(ctx, `UPDATE task_events SET payload_json = ? WHERE task_id = ? AND sequence = 1`, []byte("invalid source JSON"), task.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := store.db.ExecContext(ctx, `UPDATE tasks SET plan_json = ? WHERE id = ?`, []byte("invalid plan JSON"), task.ID); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	rows, err := store.ListSummaries(ctx)
	if err != nil || len(rows) != 1 || !reflect.DeepEqual(before, rows[0]) {
		t.Fatalf("reopened summary = %+v, %v", rows, err)
	}
	if _, err := store.Get(ctx, task.ID); err == nil {
		t.Fatal("full Get silently hid invalid source")
	}
	if _, err := store.GetSummary(ctx, "missing"); !errors.Is(err, tasks.ErrTaskNotFound) {
		t.Fatalf("missing = %v", err)
	}
}

func TestTaskSummaryBusinessWatermarkIgnoresObserverOutput(t *testing.T) {
	ctx := context.Background()
	store, err := Open(filepath.Join(t.TempDir(), "summary.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	task := &tasks.Task{ID: "task", State: taskgraph.StateExecuting}
	if err := store.Create(ctx, task); err != nil {
		t.Fatal(err)
	}
	for index, kind := range []string{"STATE_CHANGED", "ops.anomaly_detected", "ops.recovery_plan", "ops.recovery_executed", "agent.health_changed", "TOOL_ACTIVITY"} {
		if err := store.UpdateWithEvent(ctx, task, tasks.TaskEvent{Type: kind}); err != nil {
			t.Fatal(err)
		}
		row, err := store.GetSummary(ctx, task.ID)
		want := uint64(1)
		if index == 5 {
			want = 6
		}
		if err != nil || row.EventSequence != uint64(index+1) || row.SourceSequence != want {
			t.Fatalf("after %s: %+v, %v", kind, row, err)
		}
	}
}
