// Package mysql is the cloud Fleet task repository adapter. It stores each
// task as a JSON document so the distributed control plane can evolve task
// shapes without immediate schema migrations.
package mysql

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"

	mysqldriver "github.com/go-sql-driver/mysql"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type Store struct {
	db *sql.DB
}

var _ tasks.Repository = (*Store)(nil)
var _ tasks.SummaryRepository = (*Store)(nil)

const taskRevisionSchema = `
	CREATE TABLE IF NOT EXISTS task_revisions (
		task_id VARCHAR(128) NOT NULL,
		revision BIGINT UNSIGNED NOT NULL,
		content JSON NOT NULL,
		idempotency_key VARCHAR(255) NOT NULL,
		created_at DATETIME(6) NOT NULL,
		PRIMARY KEY (task_id, revision),
		UNIQUE KEY uq_task_revision_idempotency (task_id, idempotency_key),
		CONSTRAINT fk_task_revision_task FOREIGN KEY (task_id) REFERENCES robot_tasks(id) ON DELETE CASCADE
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
	CREATE TABLE IF NOT EXISTS task_revision_events (
		task_id VARCHAR(128) NOT NULL,
		revision BIGINT UNSIGNED NOT NULL,
		sequence BIGINT UNSIGNED NOT NULL,
		status VARCHAR(64) NOT NULL,
		reason TEXT NOT NULL,
		payload JSON NOT NULL,
		occurred_at DATETIME(6) NOT NULL,
		PRIMARY KEY (task_id, revision, sequence),
		CONSTRAINT fk_task_revision_event FOREIGN KEY (task_id, revision) REFERENCES task_revisions(task_id, revision) ON DELETE CASCADE
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
`

const taskRevisionColumnSchema = `
	ALTER TABLE robot_tasks ADD COLUMN aggregate_version BIGINT UNSIGNED NOT NULL DEFAULT 1
`

func Open(dsn string) (*Store, error) {
	if dsn == "" {
		return nil, errors.New("mysql DSN is required")
	}
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(20)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(30 * time.Minute)
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, err
	}
	if _, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS robot_tasks (
			id VARCHAR(128) PRIMARY KEY,
			data JSON NOT NULL,
			aggregate_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
			updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
			INDEX idx_robot_tasks_updated (updated_at)
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
	`); err != nil {
		db.Close()
		return nil, err
	}
	if err := ensureTaskRevisionSchema(db); err != nil {
		db.Close()
		return nil, err
	}
	if err := ensureCoordinationSchema(db); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db}, nil
}

func ensureTaskRevisionSchema(db *sql.DB) error {
	var aggregateVersionColumns int
	if err := db.QueryRow(`
		SELECT COUNT(*)
		FROM information_schema.columns
		WHERE table_schema = DATABASE()
		  AND table_name = 'robot_tasks'
		  AND column_name = 'aggregate_version'
	`).Scan(&aggregateVersionColumns); err != nil {
		return err
	}
	if aggregateVersionColumns == 0 {
		if _, err := db.Exec(strings.TrimSpace(taskRevisionColumnSchema)); err != nil {
			var mysqlErr *mysqldriver.MySQLError
			if !errors.As(err, &mysqlErr) || mysqlErr.Number != 1060 {
				return err
			}
		}
	}
	for _, statement := range strings.Split(taskRevisionSchema, ";") {
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

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) Create(ctx context.Context, task *tasks.Task) error {
	data, err := json.Marshal(task)
	if err != nil {
		return err
	}
	_, err = s.db.ExecContext(ctx, `INSERT INTO robot_tasks (id, data, aggregate_version, updated_at) VALUES (?, ?, ?, ?)`,
		task.ID, data, mysqlRevision(task.AggregateVersion), task.UpdatedAt)
	return err
}

func (s *Store) CreateWithRevision(ctx context.Context, task *tasks.Task, revision *tasks.TaskRevision) (err error) {
	if task == nil || revision == nil || task.ID == "" || revision.TaskID != task.ID || revision.Revision == 0 {
		return errors.New("task and initial revision identity are required")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback()
		}
	}()
	taskData, err := json.Marshal(task)
	if err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO robot_tasks (id, data, aggregate_version, updated_at) VALUES (?, ?, ?, ?)`,
		task.ID, taskData, mysqlRevision(task.AggregateVersion), task.UpdatedAt); err != nil {
		return err
	}
	if err = insertMySQLRevision(ctx, tx, revision); err != nil {
		return err
	}
	status := task.RevisionState
	if status == "" {
		status = tasks.RevisionActive
	}
	if err = insertMySQLRevisionEvent(ctx, tx, task.ID, tasks.RevisionLifecycleEvent{
		Sequence: 1, Revision: revision.Revision, Status: status, OccurredAt: revision.CreatedAt,
	}); err != nil {
		return err
	}
	err = tx.Commit()
	return err
}

func (s *Store) Get(ctx context.Context, id string) (*tasks.Task, error) {
	var data []byte
	err := s.db.QueryRowContext(ctx, `SELECT data FROM robot_tasks WHERE id = ?`, id).Scan(&data)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, tasks.ErrTaskNotFound
	}
	if err != nil {
		return nil, err
	}
	var task tasks.Task
	if err := json.Unmarshal(data, &task); err != nil {
		return nil, err
	}
	return tasks.NormalizeLegacyTask(&task), nil
}

func (s *Store) Update(ctx context.Context, task *tasks.Task) error {
	data, err := json.Marshal(task)
	if err != nil {
		return err
	}
	result, err := s.db.ExecContext(ctx, `UPDATE robot_tasks SET data = ?, aggregate_version = ?, updated_at = ? WHERE id = ?`,
		data, mysqlRevision(task.AggregateVersion), task.UpdatedAt, task.ID)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected == 0 {
		return tasks.ErrTaskNotFound
	}
	return nil
}

func (s *Store) List(ctx context.Context) ([]*tasks.Task, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT data FROM robot_tasks ORDER BY updated_at, id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var list []*tasks.Task
	for rows.Next() {
		var data []byte
		if err := rows.Scan(&data); err != nil {
			return nil, err
		}
		var task tasks.Task
		if err := json.Unmarshal(data, &task); err != nil {
			return nil, err
		}
		list = append(list, tasks.NormalizeLegacyTask(&task))
	}
	return list, rows.Err()
}

func (s *Store) ListSummaries(ctx context.Context, limit int) ([]tasks.TaskSummary, error) {
	limit = tasks.BoundSummaryLimit(limit)
	rows, err := s.db.QueryContext(ctx,
		`SELECT JSON_REMOVE(data, '$.events', '$.plan') FROM robot_tasks ORDER BY updated_at DESC, id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	summaries := make([]tasks.TaskSummary, 0, limit)
	for rows.Next() {
		var data []byte
		if err := rows.Scan(&data); err != nil {
			return nil, err
		}
		var task tasks.Task
		if err := json.Unmarshal(data, &task); err != nil {
			return nil, err
		}
		summaries = append(summaries, tasks.Summarize(tasks.NormalizeLegacyTask(&task)))
	}
	return summaries, rows.Err()
}

func (s *Store) CommitRevision(ctx context.Context, commit tasks.RevisionCommit) (err error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback()
		}
	}()
	if commit.NewRevision != nil {
		var content []byte
		queryErr := tx.QueryRowContext(ctx, `SELECT content FROM task_revisions WHERE task_id = ? AND idempotency_key = ?`,
			commit.TaskID, commit.NewRevision.IdempotencyKey).Scan(&content)
		if queryErr == nil {
			var existing tasks.TaskRevision
			if err = json.Unmarshal(content, &existing); err != nil {
				return err
			}
			if tasks.RevisionContentEqual(&existing, commit.NewRevision) {
				_ = tx.Rollback()
				return nil
			}
			return tasks.ErrIdempotencyConflict
		}
		if !errors.Is(queryErr, sql.ErrNoRows) {
			return queryErr
		}
	}
	var currentVersion uint64
	if err = tx.QueryRowContext(ctx, `SELECT aggregate_version FROM robot_tasks WHERE id = ? FOR UPDATE`, commit.TaskID).Scan(&currentVersion); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return tasks.ErrTaskNotFound
		}
		return err
	}
	if currentVersion != commit.ExpectedAggregateVersion || commit.Task == nil ||
		commit.Task.ID != commit.TaskID || commit.Task.AggregateVersion != commit.ExpectedAggregateVersion+1 {
		return tasks.ErrRevisionConflict
	}
	taskData, err := taskDataForRevisionCommit(commit.Task, commit.TaskEvent)
	if err != nil {
		return err
	}
	result, err := tx.ExecContext(ctx, `UPDATE robot_tasks SET data = ?, aggregate_version = ?, updated_at = ?
		WHERE id = ? AND aggregate_version = ?`, taskData, commit.Task.AggregateVersion, commit.Task.UpdatedAt,
		commit.TaskID, commit.ExpectedAggregateVersion)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected != 1 {
		return tasks.ErrRevisionConflict
	}
	if commit.NewRevision != nil {
		if err = insertMySQLRevision(ctx, tx, commit.NewRevision); err != nil {
			return err
		}
	}
	events := commit.LifecycleEvents
	if len(events) == 0 && commit.LifecycleEvent.Revision != 0 {
		events = []tasks.RevisionLifecycleEvent{commit.LifecycleEvent}
	}
	if len(events) == 0 {
		return tasks.ErrRevisionConflict
	}
	for _, event := range events {
		if err = insertMySQLRevisionEvent(ctx, tx, commit.TaskID, event); err != nil {
			return err
		}
	}
	err = tx.Commit()
	return err
}

func (s *Store) Revision(ctx context.Context, taskID string, revision uint64) (tasks.RevisionRecord, error) {
	var content []byte
	if err := s.db.QueryRowContext(ctx, `SELECT content FROM task_revisions WHERE task_id = ? AND revision = ?`, taskID, revision).Scan(&content); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return tasks.RevisionRecord{}, tasks.ErrRevisionNotFound
		}
		return tasks.RevisionRecord{}, err
	}
	var value tasks.TaskRevision
	if err := json.Unmarshal(content, &value); err != nil {
		return tasks.RevisionRecord{}, err
	}
	events, err := s.loadRevisionEvents(ctx, taskID, revision)
	if err != nil {
		return tasks.RevisionRecord{}, err
	}
	status := tasks.RevisionStatus("")
	if len(events) > 0 {
		status = events[len(events)-1].Status
	}
	return tasks.RevisionRecord{Revision: value, Status: status, Events: events}, nil
}

func (s *Store) ListRevisions(ctx context.Context, taskID string) ([]tasks.RevisionRecord, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT revision, content FROM task_revisions WHERE task_id = ? ORDER BY revision`, taskID)
	if err != nil {
		return nil, err
	}
	var revisions []tasks.TaskRevision
	for rows.Next() {
		var number uint64
		var content []byte
		if err := rows.Scan(&number, &content); err != nil {
			_ = rows.Close()
			return nil, err
		}
		var revision tasks.TaskRevision
		if err := json.Unmarshal(content, &revision); err != nil {
			_ = rows.Close()
			return nil, err
		}
		if revision.Revision != number {
			return nil, tasks.ErrRevisionConflict
		}
		revisions = append(revisions, revision)
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	if len(revisions) == 0 {
		if _, err := s.Get(ctx, taskID); err != nil {
			return nil, err
		}
	}
	records := make([]tasks.RevisionRecord, 0, len(revisions))
	for _, revision := range revisions {
		events, err := s.loadRevisionEvents(ctx, taskID, revision.Revision)
		if err != nil {
			return nil, err
		}
		status := tasks.RevisionStatus("")
		if len(events) > 0 {
			status = events[len(events)-1].Status
		}
		records = append(records, tasks.RevisionRecord{Revision: revision, Status: status, Events: events})
	}
	return records, nil
}

func insertMySQLRevision(ctx context.Context, tx *sql.Tx, revision *tasks.TaskRevision) error {
	content, err := json.Marshal(revision)
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO task_revisions (task_id, revision, content, idempotency_key, created_at)
		VALUES (?, ?, ?, ?, ?)`, revision.TaskID, revision.Revision, content, revision.IdempotencyKey, revision.CreatedAt)
	return err
}

func insertMySQLRevisionEvent(ctx context.Context, tx *sql.Tx, taskID string, event tasks.RevisionLifecycleEvent) error {
	if event.Sequence == 0 {
		if err := tx.QueryRowContext(ctx, `SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_revision_events
			WHERE task_id = ? AND revision = ? FOR UPDATE`, taskID, event.Revision).Scan(&event.Sequence); err != nil {
			return err
		}
	}
	payload, err := json.Marshal(event.Payload)
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO task_revision_events
		(task_id, revision, sequence, status, reason, payload, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)`,
		taskID, event.Revision, event.Sequence, string(event.Status), event.Reason, payload, event.OccurredAt)
	return err
}

func (s *Store) loadRevisionEvents(ctx context.Context, taskID string, revision uint64) ([]tasks.RevisionLifecycleEvent, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT sequence, status, reason, payload, occurred_at FROM task_revision_events
		WHERE task_id = ? AND revision = ? ORDER BY sequence`, taskID, revision)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var events []tasks.RevisionLifecycleEvent
	for rows.Next() {
		event := tasks.RevisionLifecycleEvent{Revision: revision}
		var status string
		var payload []byte
		if err := rows.Scan(&event.Sequence, &status, &event.Reason, &payload, &event.OccurredAt); err != nil {
			return nil, err
		}
		event.Status = tasks.RevisionStatus(status)
		if string(payload) != "null" {
			if err := json.Unmarshal(payload, &event.Payload); err != nil {
				return nil, err
			}
		}
		events = append(events, event)
	}
	return events, rows.Err()
}

func mysqlRevision(value uint64) uint64 {
	if value == 0 {
		return 1
	}
	return value
}

func taskDataForRevisionCommit(task *tasks.Task, event *tasks.TaskEvent) ([]byte, error) {
	if task == nil {
		return nil, errors.New("task is required")
	}
	copyTask := *task
	copyTask.Events = append([]tasks.TaskEvent(nil), task.Events...)
	if event != nil {
		copyEvent := *event
		copyTask.Events = append(copyTask.Events, copyEvent)
	}
	return json.Marshal(&copyTask)
}
