package eventlog

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"
)

func TestMemoryStoreCommitsStateEventOutboxAndCheckpointAtomically(t *testing.T) {
	store := NewMemoryStore()
	now := time.Unix(100, 0).UTC()
	request := CommitRequest{
		ExpectedVersion: 0,
		State:           AggregateState{AggregateID: "task-1", Version: 1, Data: json.RawMessage(`{"status":"RUNNING"}`), UpdatedAt: now},
		Events: []DomainEvent{{
			EventID: "evt-1", AggregateType: "fleet_task", AggregateID: "task-1", AggregateVersion: 1,
			EventType: "INTENT_CLAIMED", IdempotencyKey: "task-1/claim/0", OccurredAt: now,
		}},
		Outbox:     []OutboxEntry{{ID: "out-1", Topic: "robot-2", Key: "task-1", Payload: json.RawMessage(`{"taskId":"task-1"}`), CreatedAt: now}},
		Checkpoint: &Checkpoint{AggregateID: "task-1", Version: 1, EventCursor: "evt-1", Data: json.RawMessage(`{"next":1}`), CreatedAt: now},
	}
	if err := store.Commit(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	state, ok, err := store.LoadState(context.Background(), "task-1")
	if err != nil || !ok || state.Version != 1 {
		t.Fatalf("state=%#v ok=%v err=%v", state, ok, err)
	}
	events, err := store.ListEvents(context.Background(), "fleet_task", "task-1", 0, 10)
	if err != nil || len(events) != 1 || events[0].EventType != "INTENT_CLAIMED" {
		t.Fatalf("events=%#v err=%v", events, err)
	}
	entries, err := store.ClaimOutbox(context.Background(), 10)
	if err != nil || len(entries) != 1 || entries[0].ID != "out-1" {
		t.Fatalf("outbox=%#v err=%v", entries, err)
	}
	checkpoint, ok, err := store.LatestCheckpoint(context.Background(), "task-1")
	if err != nil || !ok || checkpoint.EventCursor != "evt-1" {
		t.Fatalf("checkpoint=%#v ok=%v err=%v", checkpoint, ok, err)
	}
}

func TestMemoryStoreRejectsVersionConflictWithoutPartialWrites(t *testing.T) {
	store := NewMemoryStore()
	first := CommitRequest{ExpectedVersion: 0, State: AggregateState{AggregateID: "task-1", Version: 1}}
	if err := store.Commit(context.Background(), first); err != nil {
		t.Fatal(err)
	}
	conflict := CommitRequest{
		ExpectedVersion: 0,
		State:           AggregateState{AggregateID: "task-1", Version: 2},
		Events:          []DomainEvent{{EventID: "evt-conflict", AggregateType: "fleet_task", AggregateID: "task-1", AggregateVersion: 2, EventType: "BAD", IdempotencyKey: "bad"}},
	}
	if err := store.Commit(context.Background(), conflict); !errors.Is(err, ErrVersionConflict) {
		t.Fatalf("conflict error=%v", err)
	}
	events, _ := store.ListEvents(context.Background(), "fleet_task", "task-1", 0, 10)
	if len(events) != 0 {
		t.Fatalf("conflicting event leaked: %#v", events)
	}
}

func TestOutboxClaimCanBeReleasedAndExpiredClaimIsReclaimed(t *testing.T) {
	now := time.Unix(100, 0).UTC()
	store := NewMemoryStoreWithClock(func() time.Time { return now })
	request := CommitRequest{
		ExpectedVersion: 0,
		State:           AggregateState{AggregateID: "task-1", Version: 1},
		Outbox:          []OutboxEntry{{ID: "out-1", Topic: "robot/robot-2", Key: "task-1"}},
	}
	if err := store.Commit(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	first, err := store.ClaimOutbox(context.Background(), 1)
	if err != nil || len(first) != 1 {
		t.Fatalf("first claim=%#v err=%v", first, err)
	}
	if err := store.ReleaseOutbox(context.Background(), "out-1"); err != nil {
		t.Fatal(err)
	}
	second, err := store.ClaimOutbox(context.Background(), 1)
	if err != nil || len(second) != 1 || second[0].Attempts != 2 {
		t.Fatalf("released claim=%#v err=%v", second, err)
	}
	now = now.Add(OutboxClaimTTL + time.Second)
	third, err := store.ClaimOutbox(context.Background(), 1)
	if err != nil || len(third) != 1 || third[0].Attempts != 3 {
		t.Fatalf("expired claim=%#v err=%v", third, err)
	}
}
