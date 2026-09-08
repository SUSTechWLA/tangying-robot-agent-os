// Package sqlite implements the local-first persistence adapters. SQL and the
// SQLite driver remain here; Agent and application packages depend only on
// tasks.Repository and middleware.ExecutionStore.
package sqlite

import (
	"context"
	"database/sql"
	"errors"
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	_ "modernc.org/sqlite"
)

type Store struct {
	db         *sql.DB
	evidenceMu sync.Mutex
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
			current_revision INTEGER NOT NULL DEFAULT 1,
			aggregate_version INTEGER NOT NULL DEFAULT 1,
			revision_state TEXT NOT NULL DEFAULT 'ACTIVE',
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
	if err := ensureTaskRevisionSchema(db); err != nil {
		db.Close()
		return nil, err
	}
	if err := ensureFleetSchema(db); err != nil {
		db.Close()
		return nil, err
	}
	if err := ensureExecutionMetadata(db); err != nil {
		db.Close()
		return nil, err
	}
	if err := ensureEvidenceSchema(db); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db}, nil
}

func ensureExecutionMetadata(db *sql.DB) error {
	rows, err := db.Query(`PRAGMA table_info(step_runs)`)
	if err != nil {
		return err
	}
	columns := map[string]bool{}
	for rows.Next() {
		var cid, notNull, primaryKey int
		var name, kind string
		var value any
		if err := rows.Scan(&cid, &name, &kind, &notNull, &value, &primaryKey); err != nil {
			rows.Close()
			return err
		}
		columns[name] = true
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	for _, column := range []string{"capability", "safety_level"} {
		if !columns[column] {
			if _, err := db.Exec(`ALTER TABLE step_runs ADD COLUMN ` + column + ` TEXT NOT NULL DEFAULT ''`); err != nil {
				return err
			}
		}
	}
	return nil
}

func ensureTaskRevisionSchema(db *sql.DB) error {
	columns := map[string]bool{}
	rows, err := db.Query(`PRAGMA table_info(tasks)`)
	if err != nil {
		return err
	}
	for rows.Next() {
		var cid int
		var name, columnType string
		var notNull, primaryKey int
		var defaultValue any
		if err := rows.Scan(&cid, &name, &columnType, &notNull, &defaultValue, &primaryKey); err != nil {
			_ = rows.Close()
			return err
		}
		columns[name] = true
	}
	if err := rows.Close(); err != nil {
		return err
	}
	alterations := []struct {
		name string
		sql  string
	}{
		{"current_revision", `ALTER TABLE tasks ADD COLUMN current_revision INTEGER NOT NULL DEFAULT 1`},
		{"aggregate_version", `ALTER TABLE tasks ADD COLUMN aggregate_version INTEGER NOT NULL DEFAULT 1`},
		{"revision_state", `ALTER TABLE tasks ADD COLUMN revision_state TEXT NOT NULL DEFAULT 'ACTIVE'`},
	}
	for _, alteration := range alterations {
		if columns[alteration.name] {
			continue
		}
		if _, err := db.Exec(alteration.sql); err != nil {
			return err
		}
	}
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS task_revisions (
			task_id TEXT NOT NULL,
			revision INTEGER NOT NULL,
			content_json BLOB NOT NULL,
			idempotency_key TEXT NOT NULL,
			created_at TEXT NOT NULL,
			PRIMARY KEY (task_id, revision),
			UNIQUE (task_id, idempotency_key),
			FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
		);
		CREATE TABLE IF NOT EXISTS task_revision_events (
			task_id TEXT NOT NULL,
			revision INTEGER NOT NULL,
			sequence INTEGER NOT NULL,
			status TEXT NOT NULL,
			reason TEXT NOT NULL DEFAULT '',
			payload_json BLOB NOT NULL,
			occurred_at TEXT NOT NULL,
			PRIMARY KEY (task_id, revision, sequence),
			FOREIGN KEY (task_id, revision) REFERENCES task_revisions(task_id, revision) ON DELETE CASCADE
		);
	`)
	return err
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
        INSERT INTO step_runs (task_id, step_id, idempotency_key, status, capability, safety_level)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id, step_id) DO UPDATE SET
            idempotency_key = excluded.idempotency_key,
            status = excluded.status,
            capability = excluded.capability,
            safety_level = excluded.safety_level,
            updated_at = CURRENT_TIMESTAMP
    `, record.TaskID, record.StepID, record.IdempotencyKey, string(status), record.Capability, record.SafetyLevel)
	return err
}

func (s *Store) ListStepRuns(ctx context.Context, taskID string) ([]middleware.StepRun, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT step_id, idempotency_key, capability, safety_level, status FROM step_runs WHERE task_id = ? ORDER BY rowid`, taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	runs := []middleware.StepRun{}
	for rows.Next() {
		run := middleware.StepRun{StepRecord: middleware.StepRecord{TaskID: taskID}}
		if err := rows.Scan(&run.StepID, &run.IdempotencyKey, &run.Capability, &run.SafetyLevel, &run.Status); err != nil {
			return nil, err
		}
		runs = append(runs, run)
	}
	return runs, rows.Err()
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
