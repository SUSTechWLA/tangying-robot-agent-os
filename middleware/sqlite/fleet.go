package sqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
)

func ensureFleetSchema(db *sql.DB) error {
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS fleet_graph_states (
			aggregate_id TEXT PRIMARY KEY,
			version INTEGER NOT NULL,
			data BLOB NOT NULL,
			updated_at TEXT NOT NULL
		);
		CREATE TABLE IF NOT EXISTS fleet_domain_events (
			event_id TEXT PRIMARY KEY,
			aggregate_type TEXT NOT NULL,
			aggregate_id TEXT NOT NULL,
			aggregate_version INTEGER NOT NULL,
			event_type TEXT NOT NULL,
			payload_json BLOB NOT NULL,
			idempotency_key TEXT NOT NULL,
			causation_id TEXT NOT NULL DEFAULT '',
			correlation_id TEXT NOT NULL DEFAULT '',
			actor TEXT NOT NULL DEFAULT '',
			occurred_at TEXT NOT NULL,
			UNIQUE (aggregate_type, aggregate_id, idempotency_key)
		);
		CREATE INDEX IF NOT EXISTS fleet_domain_events_aggregate_idx
			ON fleet_domain_events (aggregate_type, aggregate_id, aggregate_version);
		CREATE TABLE IF NOT EXISTS fleet_outbox (
			id TEXT PRIMARY KEY,
			topic TEXT NOT NULL,
			message_key TEXT NOT NULL,
			payload BLOB NOT NULL,
			created_at TEXT NOT NULL,
			claimed_at TEXT,
			acked_at TEXT,
			attempts INTEGER NOT NULL DEFAULT 0
		);
		CREATE INDEX IF NOT EXISTS fleet_outbox_pending_idx
			ON fleet_outbox (acked_at, claimed_at, created_at);
		CREATE TABLE IF NOT EXISTS fleet_checkpoints (
			aggregate_id TEXT PRIMARY KEY,
			version INTEGER NOT NULL,
			event_cursor TEXT NOT NULL,
			data BLOB NOT NULL,
			created_at TEXT NOT NULL
		);
	`)
	return err
}

func (s *Store) LoadState(ctx context.Context, aggregateID string) (eventlog.AggregateState, bool, error) {
	var state eventlog.AggregateState
	var updated string
	err := s.db.QueryRowContext(ctx, `
		SELECT aggregate_id, version, data, updated_at
		FROM fleet_graph_states WHERE aggregate_id = ?
	`, aggregateID).Scan(&state.AggregateID, &state.Version, &state.Data, &updated)
	if errors.Is(err, sql.ErrNoRows) {
		return eventlog.AggregateState{}, false, nil
	}
	if err != nil {
		return eventlog.AggregateState{}, false, err
	}
	state.UpdatedAt, err = parseTime(updated)
	return state, true, err
}

func (s *Store) Commit(ctx context.Context, request eventlog.CommitRequest) (err error) {
	transaction, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = transaction.Rollback()
		}
	}()

	var currentVersion uint64
	err = transaction.QueryRowContext(ctx, `SELECT version FROM fleet_graph_states WHERE aggregate_id = ?`, request.State.AggregateID).Scan(&currentVersion)
	if errors.Is(err, sql.ErrNoRows) {
		currentVersion = 0
		err = nil
	}
	if err != nil {
		return err
	}
	if currentVersion != request.ExpectedVersion || request.State.Version != request.ExpectedVersion+1 {
		return eventlog.ErrVersionConflict
	}
	now := time.Now().UTC()
	updatedAt := request.State.UpdatedAt
	if updatedAt.IsZero() {
		updatedAt = now
	}
	if currentVersion == 0 {
		_, err = transaction.ExecContext(ctx, `
			INSERT INTO fleet_graph_states (aggregate_id, version, data, updated_at) VALUES (?, ?, ?, ?)
		`, request.State.AggregateID, request.State.Version, []byte(request.State.Data), formatTime(updatedAt))
	} else {
		var result sql.Result
		result, err = transaction.ExecContext(ctx, `
			UPDATE fleet_graph_states SET version = ?, data = ?, updated_at = ?
			WHERE aggregate_id = ? AND version = ?
		`, request.State.Version, []byte(request.State.Data), formatTime(updatedAt), request.State.AggregateID, request.ExpectedVersion)
		if err == nil {
			var affected int64
			affected, err = result.RowsAffected()
			if err == nil && affected != 1 {
				err = eventlog.ErrVersionConflict
			}
		}
	}
	if err != nil {
		return err
	}
	for _, event := range request.Events {
		payload, marshalErr := json.Marshal(event.Payload)
		if marshalErr != nil {
			return marshalErr
		}
		occurredAt := event.OccurredAt
		if occurredAt.IsZero() {
			occurredAt = now
		}
		if _, err = transaction.ExecContext(ctx, `
			INSERT INTO fleet_domain_events (
				event_id, aggregate_type, aggregate_id, aggregate_version, event_type,
				payload_json, idempotency_key, causation_id, correlation_id, actor, occurred_at
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`, event.EventID, event.AggregateType, event.AggregateID, event.AggregateVersion, event.EventType,
			payload, event.IdempotencyKey, event.CausationID, event.CorrelationID, event.Actor, formatTime(occurredAt)); err != nil {
			return err
		}
	}
	for _, entry := range request.Outbox {
		createdAt := entry.CreatedAt
		if createdAt.IsZero() {
			createdAt = now
		}
		if _, err = transaction.ExecContext(ctx, `
			INSERT INTO fleet_outbox (id, topic, message_key, payload, created_at, attempts)
			VALUES (?, ?, ?, ?, ?, ?)
		`, entry.ID, entry.Topic, entry.Key, []byte(entry.Payload), formatTime(createdAt), entry.Attempts); err != nil {
			return err
		}
	}
	if request.Checkpoint != nil {
		createdAt := request.Checkpoint.CreatedAt
		if createdAt.IsZero() {
			createdAt = now
		}
		_, err = transaction.ExecContext(ctx, `
			INSERT INTO fleet_checkpoints (aggregate_id, version, event_cursor, data, created_at)
			VALUES (?, ?, ?, ?, ?)
			ON CONFLICT(aggregate_id) DO UPDATE SET
				version = excluded.version,
				event_cursor = excluded.event_cursor,
				data = excluded.data,
				created_at = excluded.created_at
			WHERE excluded.version >= fleet_checkpoints.version
		`, request.Checkpoint.AggregateID, request.Checkpoint.Version, request.Checkpoint.EventCursor,
			[]byte(request.Checkpoint.Data), formatTime(createdAt))
		if err != nil {
			return err
		}
	}
	err = transaction.Commit()
	return err
}

func (s *Store) ListEvents(ctx context.Context, aggregateType, aggregateID string, afterVersion uint64, limit int) ([]eventlog.DomainEvent, error) {
	if limit <= 0 {
		return []eventlog.DomainEvent{}, nil
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT event_id, aggregate_type, aggregate_id, aggregate_version, event_type,
			payload_json, idempotency_key, causation_id, correlation_id, actor, occurred_at
		FROM fleet_domain_events
		WHERE aggregate_type = ? AND aggregate_id = ? AND aggregate_version > ?
		ORDER BY aggregate_version, event_id LIMIT ?
	`, aggregateType, aggregateID, afterVersion, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var events []eventlog.DomainEvent
	for rows.Next() {
		var event eventlog.DomainEvent
		var payload []byte
		var occurred string
		if err := rows.Scan(&event.EventID, &event.AggregateType, &event.AggregateID, &event.AggregateVersion,
			&event.EventType, &payload, &event.IdempotencyKey, &event.CausationID, &event.CorrelationID,
			&event.Actor, &occurred); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(payload, &event.Payload); err != nil {
			return nil, err
		}
		event.OccurredAt, err = parseTime(occurred)
		if err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	return events, rows.Err()
}

func (s *Store) LatestCheckpoint(ctx context.Context, aggregateID string) (eventlog.Checkpoint, bool, error) {
	var checkpoint eventlog.Checkpoint
	var created string
	err := s.db.QueryRowContext(ctx, `
		SELECT aggregate_id, version, event_cursor, data, created_at
		FROM fleet_checkpoints WHERE aggregate_id = ?
	`, aggregateID).Scan(&checkpoint.AggregateID, &checkpoint.Version, &checkpoint.EventCursor, &checkpoint.Data, &created)
	if errors.Is(err, sql.ErrNoRows) {
		return eventlog.Checkpoint{}, false, nil
	}
	if err != nil {
		return eventlog.Checkpoint{}, false, err
	}
	checkpoint.CreatedAt, err = parseTime(created)
	return checkpoint, true, err
}

func (s *Store) ClaimOutbox(ctx context.Context, limit int) (entries []eventlog.OutboxEntry, err error) {
	if limit <= 0 {
		return []eventlog.OutboxEntry{}, nil
	}
	transaction, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			_ = transaction.Rollback()
		}
	}()
	claimCutoff := formatTime(time.Now().UTC().Add(-eventlog.OutboxClaimTTL))
	rows, err := transaction.QueryContext(ctx, `
		SELECT id, topic, message_key, payload, created_at, attempts
		FROM fleet_outbox WHERE acked_at IS NULL AND (claimed_at IS NULL OR claimed_at <= ?)
		ORDER BY created_at, id LIMIT ?
	`, claimCutoff, limit)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var entry eventlog.OutboxEntry
		var created string
		if err = rows.Scan(&entry.ID, &entry.Topic, &entry.Key, &entry.Payload, &created, &entry.Attempts); err != nil {
			_ = rows.Close()
			return nil, err
		}
		entry.CreatedAt, err = parseTime(created)
		if err != nil {
			_ = rows.Close()
			return nil, err
		}
		entries = append(entries, entry)
	}
	if err = rows.Close(); err != nil {
		return nil, err
	}
	claimedAt := time.Now().UTC()
	for index := range entries {
		result, updateErr := transaction.ExecContext(ctx, `
			UPDATE fleet_outbox SET claimed_at = ?, attempts = attempts + 1
			WHERE id = ? AND acked_at IS NULL AND (claimed_at IS NULL OR claimed_at <= ?)
		`, formatTime(claimedAt), entries[index].ID, claimCutoff)
		if updateErr != nil {
			return nil, updateErr
		}
		affected, updateErr := result.RowsAffected()
		if updateErr != nil || affected != 1 {
			if updateErr != nil {
				return nil, updateErr
			}
			return nil, eventlog.ErrVersionConflict
		}
		entries[index].ClaimedAt = claimedAt
		entries[index].Attempts++
	}
	err = transaction.Commit()
	return entries, err
}

func (s *Store) ReleaseOutbox(ctx context.Context, id string) error {
	result, err := s.db.ExecContext(ctx, `UPDATE fleet_outbox SET claimed_at = NULL WHERE id = ? AND acked_at IS NULL`, id)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected != 1 {
		return fmt.Errorf("pending outbox entry %q not found", id)
	}
	return nil
}

func (s *Store) AckOutbox(ctx context.Context, id string) error {
	result, err := s.db.ExecContext(ctx, `UPDATE fleet_outbox SET acked_at = ? WHERE id = ?`, formatTime(time.Now().UTC()), id)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected != 1 {
		return fmt.Errorf("outbox entry %q not found", id)
	}
	return nil
}

func formatTime(value time.Time) string { return value.UTC().Format(time.RFC3339Nano) }

func parseTime(value string) (time.Time, error) { return time.Parse(time.RFC3339Nano, value) }

var _ eventlog.Store = (*Store)(nil)
