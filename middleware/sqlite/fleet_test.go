package sqlite

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
)

func TestFleetCoordinationSurvivesSQLiteReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local-brain.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(100, 0).UTC()
	request := eventlog.CommitRequest{
		ExpectedVersion: 0,
		State:           eventlog.AggregateState{AggregateID: "task-1", Version: 1, Data: json.RawMessage(`{"status":"RUNNING"}`), UpdatedAt: now},
		Events:          []eventlog.DomainEvent{{EventID: "evt-1", AggregateType: "fleet_task", AggregateID: "task-1", AggregateVersion: 1, EventType: "RUNNING", IdempotencyKey: "task-1/running", OccurredAt: now}},
		Outbox:          []eventlog.OutboxEntry{{ID: "out-1", Topic: "robot-2", Key: "task-1", Payload: json.RawMessage(`{}`), CreatedAt: now}},
		Checkpoint:      &eventlog.Checkpoint{AggregateID: "task-1", Version: 1, EventCursor: "evt-1", Data: json.RawMessage(`{}`), CreatedAt: now},
	}
	if err := store.Commit(context.Background(), request); err != nil {
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
	state, ok, err := reopened.LoadState(context.Background(), "task-1")
	if err != nil || !ok || state.Version != 1 {
		t.Fatalf("state=%#v ok=%v err=%v", state, ok, err)
	}
	entries, err := reopened.ClaimOutbox(context.Background(), 10)
	if err != nil || len(entries) != 1 {
		t.Fatalf("outbox=%#v err=%v", entries, err)
	}
	checkpoint, ok, err := reopened.LatestCheckpoint(context.Background(), "task-1")
	if err != nil || !ok || checkpoint.EventCursor != "evt-1" {
		t.Fatalf("checkpoint=%#v ok=%v err=%v", checkpoint, ok, err)
	}
}
