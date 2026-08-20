// Package eventlog defines the atomic durable boundary for Fleet coordination.
package eventlog

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

var ErrVersionConflict = errors.New("aggregate version conflict")

// OutboxClaimTTL makes a crashed dispatcher claim recoverable.
const OutboxClaimTTL = 30 * time.Second

type AggregateState struct {
	AggregateID string          `json:"aggregateId"`
	Version     uint64          `json:"version"`
	Data        json.RawMessage `json:"data"`
	UpdatedAt   time.Time       `json:"updatedAt"`
}

type DomainEvent struct {
	EventID          string         `json:"eventId"`
	AggregateType    string         `json:"aggregateType"`
	AggregateID      string         `json:"aggregateId"`
	AggregateVersion uint64         `json:"aggregateVersion"`
	EventType        string         `json:"eventType"`
	Payload          map[string]any `json:"payload,omitempty"`
	IdempotencyKey   string         `json:"idempotencyKey"`
	CausationID      string         `json:"causationId,omitempty"`
	CorrelationID    string         `json:"correlationId,omitempty"`
	Actor            string         `json:"actor,omitempty"`
	OccurredAt       time.Time      `json:"occurredAt"`
}

type OutboxEntry struct {
	ID        string          `json:"id"`
	Topic     string          `json:"topic"`
	Key       string          `json:"key"`
	Payload   json.RawMessage `json:"payload"`
	CreatedAt time.Time       `json:"createdAt"`
	ClaimedAt time.Time       `json:"claimedAt,omitempty"`
	AckedAt   time.Time       `json:"ackedAt,omitempty"`
	Attempts  int             `json:"attempts"`
}

type Checkpoint struct {
	AggregateID string          `json:"aggregateId"`
	Version     uint64          `json:"version"`
	EventCursor string          `json:"eventCursor"`
	Data        json.RawMessage `json:"data"`
	CreatedAt   time.Time       `json:"createdAt"`
}

type CommitRequest struct {
	State           AggregateState `json:"state"`
	ExpectedVersion uint64         `json:"expectedVersion"`
	Events          []DomainEvent  `json:"events"`
	Outbox          []OutboxEntry  `json:"outbox"`
	Checkpoint      *Checkpoint    `json:"checkpoint,omitempty"`
}

type Store interface {
	LoadState(context.Context, string) (AggregateState, bool, error)
	Commit(context.Context, CommitRequest) error
	ListEvents(context.Context, string, string, uint64, int) ([]DomainEvent, error)
	LatestCheckpoint(context.Context, string) (Checkpoint, bool, error)
	ClaimOutbox(context.Context, int) ([]OutboxEntry, error)
	ReleaseOutbox(context.Context, string) error
	AckOutbox(context.Context, string) error
}
