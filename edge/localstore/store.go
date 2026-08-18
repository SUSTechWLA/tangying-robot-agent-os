package localstore

import (
	"context"
	"database/sql"
	"errors"

	_ "modernc.org/sqlite"
)

type StepStatus string

const (
	StatusPending   StepStatus = "PENDING"
	StatusStarted   StepStatus = "STARTED"
	StatusCompleted StepStatus = "COMPLETED"
)

type Store struct {
	db *sql.DB
}

func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	if _, err := db.Exec(`
        PRAGMA journal_mode=WAL;
		PRAGMA foreign_keys=ON;
		CREATE TABLE IF NOT EXISTS tasks (
			id TEXT PRIMARY KEY,
			request TEXT NOT NULL,
			adapter TEXT NOT NULL,
			intent_json BLOB NOT NULL,
			plan_json BLOB NOT NULL,
			state TEXT NOT NULL,
			approved INTEGER NOT NULL,
			lease_id TEXT NOT NULL DEFAULT '',
			leased_to TEXT NOT NULL DEFAULT '',
			lease_expires_at TEXT NOT NULL DEFAULT '',
			created_at TEXT NOT NULL,
			updated_at TEXT NOT NULL
		);
		CREATE TABLE IF NOT EXISTS task_events (
			task_id TEXT NOT NULL,
			sequence INTEGER NOT NULL,
			type TEXT NOT NULL,
			step_id TEXT NOT NULL DEFAULT '',
			message TEXT NOT NULL DEFAULT '',
			payload_json BLOB NOT NULL,
			occurred_at TEXT NOT NULL,
			PRIMARY KEY (task_id, sequence),
			FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
		);
        CREATE TABLE IF NOT EXISTS step_runs (
            task_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (task_id, step_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS step_runs_idempotency_idx
            ON step_runs (idempotency_key) WHERE idempotency_key <> '';
    `); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) MarkStarted(ctx context.Context, taskID, stepID, idempotencyKey string) error {
	return s.setStatus(ctx, taskID, stepID, idempotencyKey, StatusStarted)
}

func (s *Store) MarkCompleted(ctx context.Context, taskID, stepID, idempotencyKey string) error {
	return s.setStatus(ctx, taskID, stepID, idempotencyKey, StatusCompleted)
}

func (s *Store) setStatus(ctx context.Context, taskID, stepID, idempotencyKey string, status StepStatus) error {
	_, err := s.db.ExecContext(ctx, `
        INSERT INTO step_runs (task_id, step_id, idempotency_key, status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(task_id, step_id) DO UPDATE SET
            idempotency_key = excluded.idempotency_key,
            status = excluded.status,
            updated_at = CURRENT_TIMESTAMP
    `, taskID, stepID, idempotencyKey, string(status))
	return err
}

func (s *Store) Status(ctx context.Context, taskID, stepID string) (StepStatus, error) {
	var status string
	err := s.db.QueryRowContext(ctx, `SELECT status FROM step_runs WHERE task_id = ? AND step_id = ?`, taskID, stepID).Scan(&status)
	if errors.Is(err, sql.ErrNoRows) {
		return StatusPending, nil
	}
	return StepStatus(status), err
}

func (s *Store) Completed(ctx context.Context, taskID, stepID string) (bool, error) {
	status, err := s.Status(ctx, taskID, stepID)
	return status == StatusCompleted, err
}
