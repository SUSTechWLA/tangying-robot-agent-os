package eventlog

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"
)

type MemoryStore struct {
	mu          sync.Mutex
	now         func() time.Time
	states      map[string]AggregateState
	events      []DomainEvent
	eventIDs    map[string]struct{}
	idempotency map[string]struct{}
	outbox      map[string]OutboxEntry
	checkpoints map[string]Checkpoint
}

func NewMemoryStore() *MemoryStore {
	return NewMemoryStoreWithClock(time.Now)
}

func NewMemoryStoreWithClock(now func() time.Time) *MemoryStore {
	return &MemoryStore{
		now: now, states: map[string]AggregateState{}, eventIDs: map[string]struct{}{},
		idempotency: map[string]struct{}{}, outbox: map[string]OutboxEntry{},
		checkpoints: map[string]Checkpoint{},
	}
}

func (s *MemoryStore) LoadState(_ context.Context, aggregateID string) (AggregateState, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	state, ok := s.states[aggregateID]
	return cloneState(state), ok, nil
}

func (s *MemoryStore) Commit(_ context.Context, request CommitRequest) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	current, exists := s.states[request.State.AggregateID]
	currentVersion := uint64(0)
	if exists {
		currentVersion = current.Version
	}
	if currentVersion != request.ExpectedVersion || request.State.Version != request.ExpectedVersion+1 {
		return ErrVersionConflict
	}
	if request.State.AggregateID == "" {
		return errors.New("aggregate id is required")
	}
	for _, event := range request.Events {
		if event.EventID == "" || event.AggregateType == "" || event.AggregateID != request.State.AggregateID ||
			event.EventType == "" || event.IdempotencyKey == "" {
			return errors.New("domain event identity is incomplete")
		}
		if _, duplicate := s.eventIDs[event.EventID]; duplicate {
			return fmt.Errorf("duplicate event id %q", event.EventID)
		}
		key := event.AggregateType + "/" + event.AggregateID + "/" + event.IdempotencyKey
		if _, duplicate := s.idempotency[key]; duplicate {
			return fmt.Errorf("duplicate event idempotency key %q", event.IdempotencyKey)
		}
	}
	for _, entry := range request.Outbox {
		if entry.ID == "" || entry.Topic == "" {
			return errors.New("outbox identity is incomplete")
		}
		if _, duplicate := s.outbox[entry.ID]; duplicate {
			return fmt.Errorf("duplicate outbox id %q", entry.ID)
		}
	}
	now := s.now().UTC()
	state := cloneState(request.State)
	if state.UpdatedAt.IsZero() {
		state.UpdatedAt = now
	}
	s.states[state.AggregateID] = state
	for _, event := range request.Events {
		if event.OccurredAt.IsZero() {
			event.OccurredAt = now
		}
		event.Payload = cloneAnyMap(event.Payload)
		s.events = append(s.events, event)
		s.eventIDs[event.EventID] = struct{}{}
		s.idempotency[event.AggregateType+"/"+event.AggregateID+"/"+event.IdempotencyKey] = struct{}{}
	}
	for _, entry := range request.Outbox {
		if entry.CreatedAt.IsZero() {
			entry.CreatedAt = now
		}
		entry.Payload = cloneJSON(entry.Payload)
		s.outbox[entry.ID] = entry
	}
	if request.Checkpoint != nil {
		checkpoint := *request.Checkpoint
		if checkpoint.CreatedAt.IsZero() {
			checkpoint.CreatedAt = now
		}
		checkpoint.Data = cloneJSON(checkpoint.Data)
		s.checkpoints[checkpoint.AggregateID] = checkpoint
	}
	return nil
}

func (s *MemoryStore) ListEvents(_ context.Context, aggregateType, aggregateID string, afterVersion uint64, limit int) ([]DomainEvent, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if limit <= 0 {
		return []DomainEvent{}, nil
	}
	result := make([]DomainEvent, 0, limit)
	for _, event := range s.events {
		if event.AggregateType != aggregateType || event.AggregateID != aggregateID || event.AggregateVersion <= afterVersion {
			continue
		}
		event.Payload = cloneAnyMap(event.Payload)
		result = append(result, event)
		if len(result) == limit {
			break
		}
	}
	return result, nil
}

func (s *MemoryStore) LatestCheckpoint(_ context.Context, aggregateID string) (Checkpoint, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	checkpoint, ok := s.checkpoints[aggregateID]
	checkpoint.Data = cloneJSON(checkpoint.Data)
	return checkpoint, ok, nil
}

func (s *MemoryStore) ClaimOutbox(_ context.Context, limit int) ([]OutboxEntry, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if limit <= 0 {
		return []OutboxEntry{}, nil
	}
	ids := make([]string, 0, len(s.outbox))
	for id := range s.outbox {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	now := s.now().UTC()
	result := make([]OutboxEntry, 0, limit)
	for _, id := range ids {
		entry := s.outbox[id]
		if !entry.AckedAt.IsZero() || (!entry.ClaimedAt.IsZero() && now.Sub(entry.ClaimedAt) < OutboxClaimTTL) {
			continue
		}
		entry.ClaimedAt = now
		entry.Attempts++
		s.outbox[id] = entry
		entry.Payload = cloneJSON(entry.Payload)
		result = append(result, entry)
		if len(result) == limit {
			break
		}
	}
	return result, nil
}

func (s *MemoryStore) ReleaseOutbox(_ context.Context, id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry, ok := s.outbox[id]
	if !ok {
		return errors.New("outbox entry not found")
	}
	if entry.AckedAt.IsZero() {
		entry.ClaimedAt = time.Time{}
		s.outbox[id] = entry
	}
	return nil
}

func (s *MemoryStore) AckOutbox(_ context.Context, id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry, ok := s.outbox[id]
	if !ok {
		return errors.New("outbox entry not found")
	}
	entry.AckedAt = s.now().UTC()
	s.outbox[id] = entry
	return nil
}

func cloneState(state AggregateState) AggregateState {
	state.Data = cloneJSON(state.Data)
	return state
}

func cloneJSON(value json.RawMessage) json.RawMessage { return append(json.RawMessage(nil), value...) }

func cloneAnyMap(source map[string]any) map[string]any {
	if source == nil {
		return nil
	}
	wire, _ := json.Marshal(source)
	var result map[string]any
	_ = json.Unmarshal(wire, &result)
	return result
}
