// Package sqlite implements the local-first persistence adapters. SQL and the
// SQLite driver remain here; Agent and application packages depend only on
// tasks.Repository and middleware.ExecutionStore.
package sqlite

import (
	"context"
	"database/sql"
	"errors"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	_ "modernc.org/sqlite"
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

func (s *Store) MarkStepStarted(ctx context.Context, record middleware.StepRecord) error {
	return s.setStatus(ctx, record, middleware.StepStarted)
}

func (s *Store) MarkStepCompleted(ctx context.Context, record middleware.StepRecord) error {
	return s.setStatus(ctx, record, middleware.StepCompleted)
}

func (s *Store) setStatus(ctx context.Context, record middleware.StepRecord, status middleware.StepStatus) error {
	_, err := s.db.ExecContext(ctx, `
        INSERT INTO step_runs (task_id, step_id, idempotency_key, status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(task_id, step_id) DO UPDATE SET
            idempotency_key = excluded.idempotency_key,
            status = excluded.status,
            updated_at = CURRENT_TIMESTAMP
    `, record.TaskID, record.StepID, record.IdempotencyKey, string(status))
	return err
}

func (s *Store) StepStatus(ctx context.Context, taskID, stepID string) (middleware.StepStatus, error) {
	var status string
	err := s.db.QueryRowContext(ctx, `SELECT status FROM step_runs WHERE task_id = ? AND step_id = ?`, taskID, stepID).Scan(&status)
	if errors.Is(err, sql.ErrNoRows) {
		return middleware.StepPending, nil
	}
	return middleware.StepStatus(status), err
}

var _ middleware.ExecutionStore = (*Store)(nil)
