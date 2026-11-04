package tasks_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The live observer and a restarted process must agree on every state change.
// In particular, an interrupted execution must not lose the state it left.
func TestLifecycleAuditMatchesObserverAfterSQLiteReopen(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "lifecycle.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	approved, err := service.Approve(ctx, task.ID, " local-owner ")
	if err != nil {
		t.Fatal(err)
	}
	// An admission retry must not overwrite the original approval attribution.
	retry, err := service.Approve(ctx, task.ID, "different-surface")
	if err != nil || len(retry.Events) != len(approved.Events) {
		t.Fatalf("approval retry changed audit history: task=%+v err=%v", retry, err)
	}
	var observed []tasks.TaskEvent
	service.ObserveEvents(func(ctx context.Context, id string, event tasks.TaskEvent) {
		committed, err := service.Get(ctx, id)
		if err != nil {
			t.Fatal(err)
		}
		assertLifecycleEventEqual(t, event, committed.Events[len(committed.Events)-1])
		observed = append(observed, event)
	})
	states := []taskgraph.TaskState{
		taskgraph.StateReady, taskgraph.StateObserving, taskgraph.StatePlanning,
		taskgraph.StateExecuting, taskgraph.StateRecoverableFailure,
	}
	for index, state := range states[1:] {
		if err := service.Transition(ctx, task.ID, state, fmt.Sprintf("transition-%d", index)); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	persisted, err := reopened.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.State != taskgraph.StateRecoverableFailure || len(persisted.Events) != len(states)+1 || len(observed) != len(states)-1 {
		t.Fatalf("incomplete persisted lifecycle: task=%+v observed=%+v", persisted, observed)
	}
	approval := persisted.Events[1]
	if approval.Type != "TASK_APPROVED" || approval.Payload["actor"] != "local-owner" ||
		approval.Payload["revision"] != float64(1) || approval.Payload["aggregateVersion"] != float64(1) ||
		approval.Payload["approvedAt"] != approved.ApprovedAt.Format("2006-01-02T15:04:05.999999999Z07:00") {
		t.Fatalf("approval lost structured authority: %+v", approval)
	}
	for index, event := range persisted.Events[2:] {
		assertLifecycleEventEqual(t, observed[index], event)
		if event.Payload["previousState"] != string(states[index]) || event.Payload["state"] != string(states[index+1]) ||
			event.Payload["revision"] != float64(1) || event.Payload["aggregateVersion"] != float64(1) ||
			event.Message != fmt.Sprintf("transition-%d", index) || event.Sequence != uint64(index+3) {
			t.Fatalf("persisted state event is not reconstructable: %+v", event)
		}
	}
}

type failingLifecycleUpdate struct{ tasks.Repository }

var errLifecycleWrite = errors.New("injected lifecycle write failure")

func (failingLifecycleUpdate) Update(context.Context, *tasks.Task) error { return errLifecycleWrite }

func TestLifecycleAuditDoesNotPublishOrPersistFailedWrites(t *testing.T) {
	ctx := context.Background()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(failingLifecycleUpdate{store}, intent.NewDeterministicParser())
	task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	var notifications int
	service.ObserveEvents(func(context.Context, string, tasks.TaskEvent) { notifications++ })
	if err := service.Transition(ctx, task.ID, taskgraph.StateObserving, "should not commit"); !errors.Is(err, errLifecycleWrite) {
		t.Fatalf("transition write failure hidden: %v", err)
	}
	if _, err := service.Approve(ctx, task.ID, "local-owner"); !errors.Is(err, errLifecycleWrite) {
		t.Fatalf("approval write failure hidden: %v", err)
	}
	persisted, err := store.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.State != taskgraph.StateReady || persisted.Approved || len(persisted.Events) != 1 || notifications != 0 {
		t.Fatalf("uncommitted lifecycle escaped: task=%+v notifications=%d", persisted, notifications)
	}
}

func assertLifecycleEventEqual(t *testing.T, want, got tasks.TaskEvent) {
	t.Helper()
	left, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	right, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(left, right) {
		t.Fatalf("observer and durable event disagree:\nobserver=%s\ndurable=%s", left, right)
	}
}

func TestLifecycleAuditMemoryPayloadCannotBeChangedThroughAliases(t *testing.T) {
	ctx := context.Background()
	store := tasks.NewMemoryStore()
	at := time.Date(2026, 11, 2, 10, 0, 0, 123, time.UTC)
	type typedRows []map[string][]any
	payload := map[string]any{
		"state": "READY", "at": at, "largeSequence": uint64(1<<63 + 1),
		"rows":  typedRows{{"values": {map[string]any{"pose": []float64{1, 2, 3}, "ids": []string{"capture-1"}}}}},
		"array": [1]map[string]any{{"status": "CONFIRMED"}},
		"null":  nil, "nilSlice": []string(nil), "nilMap": map[string]any(nil),
	}
	input := &tasks.Task{ID: "memory-alias", State: taskgraph.StateReady, Events: []tasks.TaskEvent{
		{Sequence: 1, Type: "TOOL_ACTIVITY", Payload: payload},
	}}
	if err := store.Create(ctx, input); err != nil {
		t.Fatal(err)
	}
	mutate := func(p map[string]any) {
		p["state"] = "FAKE"
		row := p["rows"].(typedRows)[0]["values"][0].(map[string]any)
		row["pose"].([]float64)[0] = 999
		row["ids"].([]string)[0] = "fake-capture"
		p["array"].([1]map[string]any)[0]["status"] = "FAKE"
	}
	// Neither the original input, reads, nor List snapshots own the ledger.
	mutate(payload)
	read, err := store.Get(ctx, input.ID)
	if err != nil {
		t.Fatal(err)
	}
	mutate(read.Events[0].Payload)
	listed, err := store.List(ctx)
	if err != nil {
		t.Fatal(err)
	}
	mutate(listed[0].Events[0].Payload)
	final, err := store.Get(ctx, input.ID)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"state": "READY", "at": at, "largeSequence": uint64(1<<63 + 1),
		"rows":  typedRows{{"values": {map[string]any{"pose": []float64{1, 2, 3}, "ids": []string{"capture-1"}}}}},
		"array": [1]map[string]any{{"status": "CONFIRMED"}},
		"null":  nil, "nilSlice": []string(nil), "nilMap": map[string]any(nil),
	}
	if !reflect.DeepEqual(final.Events[0].Payload, want) {
		t.Fatalf("audit payload alias or Go type changed: got=%#v want=%#v", final.Events[0].Payload, want)
	}
}

func TestLifecycleAuditObserverCannotRewriteCommittedFactOrCallerResult(t *testing.T) {
	ctx := context.Background()
	service, task := newTaskService(t)
	service.ObserveEvents(func(_ context.Context, _ string, event tasks.TaskEvent) {
		event.Payload["activityStatus"] = "FAKE"
		event.Payload["evidenceIds"].([]string)[0] = "fake-capture"
	})
	result, err := service.AppendEvent(ctx, task.ID, tasks.TaskEvent{Type: "TOOL_ACTIVITY", Payload: map[string]any{
		"activityStatus": "CONFIRMED", "evidenceIds": []string{"capture-1"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	persisted, err := service.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	for _, view := range []*tasks.Task{result, persisted} {
		payload := view.Events[len(view.Events)-1].Payload
		if payload["activityStatus"] != "CONFIRMED" || !reflect.DeepEqual(payload["evidenceIds"], []string{"capture-1"}) {
			t.Fatalf("observer changed the committed fact: %+v", payload)
		}
	}
}
