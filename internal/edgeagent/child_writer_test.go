package edgeagent

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func childWriterFixture(t *testing.T) (childEvents, *sqlite.Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "child.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	child := tasks.Task{ID: "child", Request: "original request", Plan: &orchestration.Bundle{Capabilities: &capability.Plan{RobotID: "robot", Calls: []capability.Call{{Tool: "move", Arguments: map[string]any{"target": "original"}, LegacyIntent: json.RawMessage(`{"destination":"original"}`)}}}}}
	if err := commit(context.Background(), store, "edge-child/child", 0, child, "EDGE_PLAN_CREATED", "parent", nil); err != nil {
		t.Fatal(err)
	}
	return childEvents{store: store, writer: &childWriter{}}, store, path
}

func TestChildWriterDoesNotExposeAliasesAndPreservesLatestStateAcrossReopen(t *testing.T) {
	ctx := context.Background()
	events, store, path := childWriterFixture(t)
	payload := map[string]any{"evidence": map[string]any{"target": "original"}, "exact": uint64(9007199254740993)}
	returned, err := events.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "CAPABILITY_VERIFIED", Payload: payload})
	if err != nil {
		t.Fatal(err)
	}
	if returned != nil {
		t.Fatal("cached write port exposed its private task")
	}
	payload["evidence"].(map[string]any)["target"] = "caller mutation"
	read, err := events.Get(ctx, "child")
	if err != nil {
		t.Fatal(err)
	}
	read.Request = "reader mutation"
	read.Plan.Capabilities.Calls[0].Arguments["target"] = "reader mutation"
	read.Plan.Capabilities.Calls[0].LegacyIntent[2] = 'X'
	read.Events[0].Payload["evidence"].(map[string]any)["target"] = "reader mutation"
	if _, err = events.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "TOOL_ACTIVITY", Payload: map[string]any{"activityStatus": "CONFIRMED"}}); err != nil {
		t.Fatal(err)
	}
	state, _, err := store.LoadState(ctx, "edge-child/child")
	if err != nil {
		t.Fatal(err)
	}
	checkpoint, found, err := store.LatestCheckpoint(ctx, "edge-child/child")
	if err != nil || !found || state.Version != 3 || checkpoint.Version != 2 {
		t.Fatalf("versions state=%d checkpoint=%d found=%v err=%v", state.Version, checkpoint.Version, found, err)
	}
	var checkpointTask tasks.Task
	if err := json.Unmarshal(checkpoint.Data, &checkpointTask); err != nil || len(checkpointTask.Events) != 1 {
		t.Fatalf("legacy checkpoint format changed: %+v err=%v", checkpointTask, err)
	}
	var raw struct {
		Events []struct {
			Payload map[string]json.RawMessage `json:"payload"`
		} `json:"events"`
	}
	if err := json.Unmarshal(state.Data, &raw); err != nil {
		t.Fatal(err)
	}
	if string(raw.Events[0].Payload["exact"]) != "9007199254740993" {
		t.Fatal("private event cloning rounded an integer")
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	fresh := childEvents{store: reopened, writer: &childWriter{}}
	actual, err := fresh.Get(ctx, "child")
	if err != nil {
		t.Fatal(err)
	}
	if actual.Request != "original request" || actual.Plan.Capabilities.Calls[0].Arguments["target"] != "original" || string(actual.Plan.Capabilities.Calls[0].LegacyIntent) != `{"destination":"original"}` || len(actual.Events) != 2 || actual.Events[0].Payload["evidence"].(map[string]any)["target"] != "original" {
		t.Fatalf("uncommitted alias became truth: %+v", actual)
	}
	if _, err := fresh.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "CAPABILITY_STOP_CONFIRMED"}); err != nil {
		t.Fatal(err)
	}
	state, _, err = reopened.LoadState(ctx, "edge-child/child")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(state.Data, &raw); err != nil || string(raw.Events[0].Payload["exact"]) != "9007199254740993" {
		t.Fatalf("writer cold load rewrote historical integer: %s err=%v", raw.Events[0].Payload["exact"], err)
	}
	checkpoint, _, err = reopened.LatestCheckpoint(ctx, "edge-child/child")
	if err != nil || checkpoint.Version != 4 {
		t.Fatalf("critical boundary not checkpointed: %+v err=%v", checkpoint, err)
	}
}

func TestChildWriterCASAndCancellationDiscardUncommittedState(t *testing.T) {
	ctx := context.Background()
	events, store, _ := childWriterFixture(t)
	if _, err := events.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "FIRST"}); err != nil {
		t.Fatal(err)
	}
	other := childEvents{store: store}
	if _, err := other.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "CONCURRENT"}); err != nil {
		t.Fatal(err)
	}
	if _, err := events.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "CONFLICT_MUST_NOT_PERSIST"}); !errors.Is(err, eventstore.ErrVersionConflict) {
		t.Fatalf("stale writer was accepted: %v", err)
	}
	if events.writer.task != nil {
		t.Fatal("conflicted private state retained")
	}
	if _, err := events.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "RELOADED"}); err != nil {
		t.Fatal(err)
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if _, err := events.AppendEvent(cancelled, "child", tasks.TaskEvent{Type: "CANCELLED_MUST_NOT_PERSIST"}); err == nil {
		t.Fatal("cancelled commit accepted")
	}
	if events.writer.task != nil {
		t.Fatal("cancelled private state retained")
	}
	if _, err := events.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "AFTER_CANCEL"}); err != nil {
		t.Fatal(err)
	}
	actual, err := events.Get(ctx, "child")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"FIRST", "CONCURRENT", "RELOADED", "AFTER_CANCEL"}
	if len(actual.Events) != len(want) {
		t.Fatalf("uncommitted facts retained: %+v", actual.Events)
	}
	for i, e := range actual.Events {
		if e.Type != want[i] || e.Sequence != uint64(i+1) {
			t.Fatalf("event %d=%+v", i, e)
		}
	}
}

type mutatingFailedParent struct{}

func (mutatingFailedParent) Get(context.Context, string) (*tasks.Task, error) {
	return &tasks.Task{CurrentRevision: 1}, nil
}
func (mutatingFailedParent) AppendEvent(_ context.Context, _ string, event tasks.TaskEvent) (*tasks.Task, error) {
	if detail, ok := event.Payload["detail"].(map[string]any); ok {
		detail["evidence"] = "parent mutated outgoing event"
	}
	return nil, errors.New("injected parent delivery error")
}
func TestChildWriterRetainsCommittedFactAfterParentFailureWithoutAlias(t *testing.T) {
	ctx := context.Background()
	events, _, _ := childWriterFixture(t)
	events.parent = mutatingFailedParent{}
	if _, err := events.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "CAPABILITY_STOP_CONFIRMED", Payload: map[string]any{"evidence": "original"}}); err == nil {
		t.Fatal("mandatory parent failure hidden")
	}
	events.parent = nil
	if _, err := events.AppendEvent(ctx, "child", tasks.TaskEvent{Type: "AUDIT"}); err != nil {
		t.Fatal(err)
	}
	actual, err := events.Get(ctx, "child")
	if err != nil || len(actual.Events) != 2 || actual.Events[0].Payload["evidence"] != "original" {
		t.Fatalf("committed child fact lost or aliased: %+v err=%v", actual, err)
	}
}
