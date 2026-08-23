package mysql

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
)

const coordinationSchema = `CREATE TABLE IF NOT EXISTS fleet_graph_states (
  aggregate_id VARCHAR(128) PRIMARY KEY,
  version BIGINT UNSIGNED NOT NULL,
  data JSON NOT NULL,
  updated_unix_ms BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS fleet_domain_events (
  event_id VARCHAR(256) PRIMARY KEY,
  aggregate_type VARCHAR(64) NOT NULL,
  aggregate_id VARCHAR(128) NOT NULL,
  aggregate_version BIGINT UNSIGNED NOT NULL,
  event_type VARCHAR(96) NOT NULL,
  payload_json JSON NOT NULL,
  idempotency_key VARCHAR(256) NOT NULL,
  causation_id VARCHAR(256) NOT NULL DEFAULT '',
  correlation_id VARCHAR(256) NOT NULL DEFAULT '',
  actor VARCHAR(128) NOT NULL DEFAULT '',
  occurred_unix_ms BIGINT NOT NULL,
  UNIQUE KEY fleet_events_idempotency (aggregate_type, aggregate_id, idempotency_key),
  INDEX fleet_events_aggregate (aggregate_type, aggregate_id, aggregate_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS fleet_outbox (
  id VARCHAR(256) PRIMARY KEY,
  topic VARCHAR(256) NOT NULL,
  message_key VARCHAR(256) NOT NULL,
  payload LONGBLOB NOT NULL,
  created_unix_ms BIGINT NOT NULL,
  claimed_unix_ms BIGINT NULL,
  acked_unix_ms BIGINT NULL,
  attempts INT NOT NULL DEFAULT 0,
  INDEX fleet_outbox_pending (acked_unix_ms, claimed_unix_ms, created_unix_ms)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS fleet_checkpoints (
  aggregate_id VARCHAR(128) PRIMARY KEY,
  version BIGINT UNSIGNED NOT NULL,
  event_cursor VARCHAR(256) NOT NULL,
  data JSON NOT NULL,
  created_unix_ms BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;`

func ensureCoordinationSchema(db *sql.DB) error {
	for _, statement := range strings.Split(coordinationSchema, ";") {
		statement = strings.TrimSpace(statement)
		if statement == "" {
			continue
		}
		if _, err := db.Exec(statement); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) LoadState(ctx context.Context, aggregateID string) (eventlog.AggregateState, bool, error) {
	var state eventlog.AggregateState
	var updatedMS int64
	err := s.db.QueryRowContext(ctx, `
		SELECT aggregate_id, version, data, updated_unix_ms
		FROM fleet_graph_states WHERE aggregate_id = ?
	`, aggregateID).Scan(&state.AggregateID, &state.Version, &state.Data, &updatedMS)
	if errors.Is(err, sql.ErrNoRows) {
		return eventlog.AggregateState{}, false, nil
	}
	if err != nil {
		return eventlog.AggregateState{}, false, err
	}
	state.UpdatedAt = time.UnixMilli(updatedMS).UTC()
	return state, true, nil
}

func (s *Store) Commit(ctx context.Context, request eventlog.CommitRequest) (err error) {
	transaction, err := s.db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = transaction.Rollback()
		}
	}()
	var currentVersion uint64
	err = transaction.QueryRowContext(ctx, `
		SELECT version FROM fleet_graph_states WHERE aggregate_id = ? FOR UPDATE
	`, request.State.AggregateID).Scan(&currentVersion)
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
			INSERT INTO fleet_graph_states (aggregate_id, version, data, updated_unix_ms)
			VALUES (?, ?, ?, ?)
		`, request.State.AggregateID, request.State.Version, []byte(request.State.Data), updatedAt.UnixMilli())
	} else {
		var result sql.Result
		result, err = transaction.ExecContext(ctx, `
			UPDATE fleet_graph_states SET version = ?, data = ?, updated_unix_ms = ?
			WHERE aggregate_id = ? AND version = ?
		`, request.State.Version, []byte(request.State.Data), updatedAt.UnixMilli(), request.State.AggregateID, request.ExpectedVersion)
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
				payload_json, idempotency_key, causation_id, correlation_id, actor, occurred_unix_ms
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`, event.EventID, event.AggregateType, event.AggregateID, event.AggregateVersion, event.EventType,
			payload, event.IdempotencyKey, event.CausationID, event.CorrelationID, event.Actor, occurredAt.UnixMilli()); err != nil {
			return err
		}
	}
	for _, entry := range request.Outbox {
		createdAt := entry.CreatedAt
		if createdAt.IsZero() {
			createdAt = now
		}
		if _, err = transaction.ExecContext(ctx, `
			INSERT INTO fleet_outbox (id, topic, message_key, payload, created_unix_ms, attempts)
			VALUES (?, ?, ?, ?, ?, ?)
		`, entry.ID, entry.Topic, entry.Key, []byte(entry.Payload), createdAt.UnixMilli(), entry.Attempts); err != nil {
			return err
		}
	}
	if request.Checkpoint != nil {
		createdAt := request.Checkpoint.CreatedAt
		if createdAt.IsZero() {
			createdAt = now
		}
		_, err = transaction.ExecContext(ctx, `
			INSERT INTO fleet_checkpoints (aggregate_id, version, event_cursor, data, created_unix_ms)
			VALUES (?, ?, ?, ?, ?) AS incoming
			ON DUPLICATE KEY UPDATE
				version = IF(incoming.version >= fleet_checkpoints.version, incoming.version, fleet_checkpoints.version),
				event_cursor = IF(incoming.version >= fleet_checkpoints.version, incoming.event_cursor, fleet_checkpoints.event_cursor),
				data = IF(incoming.version >= fleet_checkpoints.version, incoming.data, fleet_checkpoints.data),
				created_unix_ms = IF(incoming.version >= fleet_checkpoints.version, incoming.created_unix_ms, fleet_checkpoints.created_unix_ms)
		`, request.Checkpoint.AggregateID, request.Checkpoint.Version, request.Checkpoint.EventCursor,
			[]byte(request.Checkpoint.Data), createdAt.UnixMilli())
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
			payload_json, idempotency_key, causation_id, correlation_id, actor, occurred_unix_ms
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
		var occurredMS int64
		if err := rows.Scan(&event.EventID, &event.AggregateType, &event.AggregateID, &event.AggregateVersion,
			&event.EventType, &payload, &event.IdempotencyKey, &event.CausationID, &event.CorrelationID,
			&event.Actor, &occurredMS); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(payload, &event.Payload); err != nil {
			return nil, err
		}
		event.OccurredAt = time.UnixMilli(occurredMS).UTC()
		events = append(events, event)
	}
	return events, rows.Err()
}

func (s *Store) LatestCheckpoint(ctx context.Context, aggregateID string) (eventlog.Checkpoint, bool, error) {
	var checkpoint eventlog.Checkpoint
	var createdMS int64
	err := s.db.QueryRowContext(ctx, `
		SELECT aggregate_id, version, event_cursor, data, created_unix_ms
		FROM fleet_checkpoints WHERE aggregate_id = ?
	`, aggregateID).Scan(&checkpoint.AggregateID, &checkpoint.Version, &checkpoint.EventCursor, &checkpoint.Data, &createdMS)
	if errors.Is(err, sql.ErrNoRows) {
		return eventlog.Checkpoint{}, false, nil
	}
	if err != nil {
		return eventlog.Checkpoint{}, false, err
	}
	checkpoint.CreatedAt = time.UnixMilli(createdMS).UTC()
	return checkpoint, true, nil
}

func (s *Store) ClaimOutbox(ctx context.Context, limit int) (entries []eventlog.OutboxEntry, err error) {
	if limit <= 0 {
		return []eventlog.OutboxEntry{}, nil
	}
	transaction, err := s.db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			_ = transaction.Rollback()
		}
	}()
	claimCutoff := time.Now().UTC().Add(-eventlog.OutboxClaimTTL).UnixMilli()
	rows, err := transaction.QueryContext(ctx, `
		SELECT id, topic, message_key, payload, created_unix_ms, attempts
		FROM fleet_outbox
		WHERE acked_unix_ms IS NULL AND (claimed_unix_ms IS NULL OR claimed_unix_ms <= ?)
		ORDER BY created_unix_ms, id LIMIT ? FOR UPDATE SKIP LOCKED
	`, claimCutoff, limit)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var entry eventlog.OutboxEntry
		var createdMS int64
		if err = rows.Scan(&entry.ID, &entry.Topic, &entry.Key, &entry.Payload, &createdMS, &entry.Attempts); err != nil {
			_ = rows.Close()
			return nil, err
		}
		entry.CreatedAt = time.UnixMilli(createdMS).UTC()
		entries = append(entries, entry)
	}
	if err = rows.Close(); err != nil {
		return nil, err
	}
	claimedAt := time.Now().UTC()
	for index := range entries {
		if _, err = transaction.ExecContext(ctx, `
			UPDATE fleet_outbox SET claimed_unix_ms = ?, attempts = attempts + 1
			WHERE id = ? AND acked_unix_ms IS NULL AND (claimed_unix_ms IS NULL OR claimed_unix_ms <= ?)
		`, claimedAt.UnixMilli(), entries[index].ID, claimCutoff); err != nil {
			return nil, err
		}
		entries[index].ClaimedAt = claimedAt
		entries[index].Attempts++
	}
	err = transaction.Commit()
	return entries, err
}

func (s *Store) ReleaseOutbox(ctx context.Context, id string) error {
	result, err := s.db.ExecContext(ctx, `UPDATE fleet_outbox SET claimed_unix_ms = NULL WHERE id = ? AND acked_unix_ms IS NULL`, id)
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
	result, err := s.db.ExecContext(ctx, `UPDATE fleet_outbox SET acked_unix_ms = ? WHERE id = ?`, time.Now().UTC().UnixMilli(), id)
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

var _ eventlog.Store = (*Store)(nil)
