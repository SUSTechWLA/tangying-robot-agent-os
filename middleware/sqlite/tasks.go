package sqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var _ tasks.Repository = (*Store)(nil)

type sqlExecutor interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

type sqlQuerier interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

func (s *Store) Create(ctx context.Context, task *tasks.Task) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := insertTaskRow(ctx, tx, task); err != nil {
		return err
	}
	for _, event := range task.Events {
		if err := insertEventRow(ctx, tx, task.ID, event); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *Store) CreateWithRevision(ctx context.Context, task *tasks.Task, revision *tasks.TaskRevision) error {
	if task == nil || revision == nil || task.ID == "" || revision.TaskID != task.ID || revision.Revision == 0 {
		return errors.New("task and initial revision identity are required")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := insertTaskRow(ctx, tx, task); err != nil {
		return err
	}
	for _, event := range task.Events {
		if err := insertEventRow(ctx, tx, task.ID, event); err != nil {
			return err
		}
	}
	if err := insertRevisionRow(ctx, tx, revision); err != nil {
		return err
	}
	status := normalizedRevisionState(task.RevisionState)
	if err := insertRevisionEvent(ctx, tx, task.ID, tasks.RevisionLifecycleEvent{
		Sequence: 1, Revision: revision.Revision, Status: status, OccurredAt: revision.CreatedAt,
	}); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) Get(ctx context.Context, id string) (*tasks.Task, error) {
	task, err := scanTask(s.db.QueryRowContext(ctx, taskSelect+" WHERE id = ?", id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, tasks.ErrTaskNotFound
	}
	if err != nil {
		return nil, err
	}
	task.Events, err = s.loadEvents(ctx, id)
	return task, err
}

func (s *Store) Update(ctx context.Context, task *tasks.Task) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := updateTaskRow(ctx, tx, task)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		if err != nil {
			return err
		}
		return tasks.ErrTaskNotFound
	}
	for _, event := range task.Events {
		if err := insertEventRow(ctx, tx, task.ID, event); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *Store) List(ctx context.Context) ([]*tasks.Task, error) {
	rows, err := s.db.QueryContext(ctx, taskSelect+" ORDER BY created_at, id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var tasks []*tasks.Task
	for rows.Next() {
		task, err := scanTask(rows)
		if err != nil {
			return nil, err
		}
		task.Events, err = s.loadEvents(ctx, task.ID)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, task)
	}
	return tasks, rows.Err()
}

func (s *Store) CommitRevision(ctx context.Context, commit tasks.RevisionCommit) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if commit.NewRevision != nil {
		var existingJSON []byte
		err := tx.QueryRowContext(ctx, `SELECT content_json FROM task_revisions WHERE task_id = ? AND idempotency_key = ?`,
			commit.TaskID, commit.NewRevision.IdempotencyKey).Scan(&existingJSON)
		if err == nil {
			var existing tasks.TaskRevision
			if err := json.Unmarshal(existingJSON, &existing); err != nil {
				return err
			}
			if tasks.RevisionContentEqual(&existing, commit.NewRevision) {
				return nil
			}
			return tasks.ErrIdempotencyConflict
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
	}
	var currentVersion uint64
	if err := tx.QueryRowContext(ctx, `SELECT aggregate_version FROM tasks WHERE id = ?`, commit.TaskID).Scan(&currentVersion); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return tasks.ErrTaskNotFound
		}
		return err
	}
	if currentVersion != commit.ExpectedAggregateVersion || commit.Task == nil ||
		commit.Task.ID != commit.TaskID || commit.Task.AggregateVersion != commit.ExpectedAggregateVersion+1 {
		return tasks.ErrRevisionConflict
	}
	result, err := updateTaskRowCAS(ctx, tx, commit.Task, commit.ExpectedAggregateVersion)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		if err != nil {
			return err
		}
		return tasks.ErrRevisionConflict
	}
	if commit.NewRevision != nil {
		if err := insertRevisionRow(ctx, tx, commit.NewRevision); err != nil {
			return err
		}
	}
	if err := insertRevisionEvent(ctx, tx, commit.TaskID, commit.LifecycleEvent); err != nil {
		return err
	}
	if commit.TaskEvent != nil {
		if err := insertEventRow(ctx, tx, commit.TaskID, *commit.TaskEvent); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *Store) Revision(ctx context.Context, taskID string, revision uint64) (tasks.RevisionRecord, error) {
	var content []byte
	if err := s.db.QueryRowContext(ctx, `SELECT content_json FROM task_revisions WHERE task_id = ? AND revision = ?`,
		taskID, revision).Scan(&content); err != nil {
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
	rows, err := s.db.QueryContext(ctx, `SELECT content_json FROM task_revisions WHERE task_id = ? ORDER BY revision`, taskID)
	if err != nil {
		return nil, err
	}
	var revisions []tasks.TaskRevision
	for rows.Next() {
		var content []byte
		if err := rows.Scan(&content); err != nil {
			_ = rows.Close()
			return nil, err
		}
		var revision tasks.TaskRevision
		if err := json.Unmarshal(content, &revision); err != nil {
			_ = rows.Close()
			return nil, err
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

// UpdateWithEvent commits a task mutation and its corresponding audit event
// together. Local execution uses this boundary so the Console never observes
// a state without the event that explains it.
func (s *Store) UpdateWithEvent(ctx context.Context, task *tasks.Task, event tasks.TaskEvent) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := updateTaskRow(ctx, tx, task)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		if err != nil {
			return err
		}
		return tasks.ErrTaskNotFound
	}
	if event.Sequence == 0 {
		event.Sequence, err = nextEventSequence(ctx, tx, task.ID)
		if err != nil {
			return err
		}
	}
	if event.OccurredAt.IsZero() {
		event.OccurredAt = time.Now().UTC()
	}
	if err := insertEventRow(ctx, tx, task.ID, event); err != nil {
		return err
	}
	return tx.Commit()
}

const taskSelect = `SELECT id, request, adapter, intent_json, plan_json, state,
	approved, current_revision, aggregate_version, revision_state, created_at, updated_at FROM tasks`

func insertTaskRow(ctx context.Context, executor sqlExecutor, task *tasks.Task) error {
	intentJSON, planJSON, err := taskJSON(task)
	if err != nil {
		return err
	}
	_, err = executor.ExecContext(ctx, `INSERT INTO tasks (
		id, request, adapter, intent_json, plan_json, state, approved,
		current_revision, aggregate_version, revision_state, created_at, updated_at
	) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		task.ID, task.Request, task.Adapter, intentJSON, planJSON, string(task.State),
		boolInt(task.Approved), normalizedRevision(task.CurrentRevision), normalizedRevision(task.AggregateVersion),
		string(normalizedRevisionState(task.RevisionState)), encodeTime(task.CreatedAt), encodeTime(task.UpdatedAt),
	)
	return err
}

func updateTaskRow(ctx context.Context, executor sqlExecutor, task *tasks.Task) (sql.Result, error) {
	intentJSON, planJSON, err := taskJSON(task)
	if err != nil {
		return nil, err
	}
	return executor.ExecContext(ctx, `UPDATE tasks SET
		request = ?, adapter = ?, intent_json = ?, plan_json = ?, state = ?, approved = ?,
		current_revision = ?, aggregate_version = ?, revision_state = ?, created_at = ?, updated_at = ?
		WHERE id = ?`,
		task.Request, task.Adapter, intentJSON, planJSON, string(task.State), boolInt(task.Approved),
		normalizedRevision(task.CurrentRevision), normalizedRevision(task.AggregateVersion), string(normalizedRevisionState(task.RevisionState)),
		encodeTime(task.CreatedAt), encodeTime(task.UpdatedAt), task.ID,
	)
}

func updateTaskRowCAS(ctx context.Context, executor sqlExecutor, task *tasks.Task, expectedVersion uint64) (sql.Result, error) {
	intentJSON, planJSON, err := taskJSON(task)
	if err != nil {
		return nil, err
	}
	return executor.ExecContext(ctx, `UPDATE tasks SET
		request = ?, adapter = ?, intent_json = ?, plan_json = ?, state = ?, approved = ?,
		current_revision = ?, aggregate_version = ?, revision_state = ?, created_at = ?, updated_at = ?
		WHERE id = ? AND aggregate_version = ?`,
		task.Request, task.Adapter, intentJSON, planJSON, string(task.State), boolInt(task.Approved),
		normalizedRevision(task.CurrentRevision), task.AggregateVersion, string(normalizedRevisionState(task.RevisionState)),
		encodeTime(task.CreatedAt), encodeTime(task.UpdatedAt), task.ID, expectedVersion,
	)
}

func taskJSON(task *tasks.Task) ([]byte, []byte, error) {
	intentJSON, err := json.Marshal(task.Intent)
	if err != nil {
		return nil, nil, fmt.Errorf("marshal task intent: %w", err)
	}
	planJSON, err := json.Marshal(task.Plan)
	if err != nil {
		return nil, nil, fmt.Errorf("marshal task plan: %w", err)
	}
	return intentJSON, planJSON, nil
}

type rowScanner interface {
	Scan(...any) error
}

func scanTask(row rowScanner) (*tasks.Task, error) {
	var task tasks.Task
	var intentJSON, planJSON []byte
	var state, revisionState, createdAt, updatedAt string
	var approved int
	if err := row.Scan(
		&task.ID, &task.Request, &task.Adapter, &intentJSON, &planJSON, &state,
		&approved, &task.CurrentRevision, &task.AggregateVersion, &revisionState, &createdAt, &updatedAt,
	); err != nil {
		return nil, err
	}
	task.State = taskgraph.TaskState(state)
	task.Approved = approved != 0
	task.RevisionState = tasks.RevisionStatus(revisionState)
	task.CreatedAt = decodeTime(createdAt)
	task.UpdatedAt = decodeTime(updatedAt)
	if err := json.Unmarshal(intentJSON, &task.Intent); err != nil {
		return nil, fmt.Errorf("unmarshal task intent: %w", err)
	}
	if string(planJSON) != "null" {
		var plan orchestration.Bundle
		if err := json.Unmarshal(planJSON, &plan); err != nil {
			return nil, fmt.Errorf("unmarshal task plan: %w", err)
		}
		task.Plan = &plan
	}
	return tasks.NormalizeLegacyTask(&task), nil
}

func insertRevisionRow(ctx context.Context, executor sqlExecutor, revision *tasks.TaskRevision) error {
	content, err := json.Marshal(revision)
	if err != nil {
		return err
	}
	_, err = executor.ExecContext(ctx, `INSERT INTO task_revisions (
		task_id, revision, content_json, idempotency_key, created_at
	) VALUES (?, ?, ?, ?, ?)`, revision.TaskID, revision.Revision, content, revision.IdempotencyKey, encodeTime(revision.CreatedAt))
	return err
}

func insertRevisionEvent(ctx context.Context, executor sqlExecutor, taskID string, event tasks.RevisionLifecycleEvent) error {
	if event.Sequence == 0 {
		var next uint64
		querier, ok := executor.(sqlQuerier)
		if !ok {
			return errors.New("revision event executor cannot query sequence")
		}
		if err := querier.QueryRowContext(ctx, `SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_revision_events WHERE task_id = ? AND revision = ?`,
			taskID, event.Revision).Scan(&next); err != nil {
			return err
		}
		event.Sequence = next
	}
	payload, err := json.Marshal(event.Payload)
	if err != nil {
		return err
	}
	_, err = executor.ExecContext(ctx, `INSERT INTO task_revision_events (
		task_id, revision, sequence, status, reason, payload_json, occurred_at
	) VALUES (?, ?, ?, ?, ?, ?, ?)`, taskID, event.Revision, event.Sequence, string(event.Status), event.Reason, payload, encodeTime(event.OccurredAt))
	return err
}

func (s *Store) loadRevisionEvents(ctx context.Context, taskID string, revision uint64) ([]tasks.RevisionLifecycleEvent, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT sequence, status, reason, payload_json, occurred_at
		FROM task_revision_events WHERE task_id = ? AND revision = ? ORDER BY sequence`, taskID, revision)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var events []tasks.RevisionLifecycleEvent
	for rows.Next() {
		event := tasks.RevisionLifecycleEvent{Revision: revision}
		var status, occurredAt string
		var payload []byte
		if err := rows.Scan(&event.Sequence, &status, &event.Reason, &payload, &occurredAt); err != nil {
			return nil, err
		}
		event.Status = tasks.RevisionStatus(status)
		if string(payload) != "null" {
			if err := json.Unmarshal(payload, &event.Payload); err != nil {
				return nil, err
			}
		}
		event.OccurredAt = decodeTime(occurredAt)
		events = append(events, event)
	}
	return events, rows.Err()
}

func normalizedRevision(value uint64) uint64 {
	if value == 0 {
		return 1
	}
	return value
}

func normalizedRevisionState(value tasks.RevisionStatus) tasks.RevisionStatus {
	if value == "" {
		return tasks.RevisionActive
	}
	return value
}

func (s *Store) loadEvents(ctx context.Context, taskID string) ([]tasks.TaskEvent, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT sequence, type, step_id, message, payload_json, occurred_at
		FROM task_events WHERE task_id = ? ORDER BY sequence`, taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var events []tasks.TaskEvent
	for rows.Next() {
		var event tasks.TaskEvent
		var payloadJSON []byte
		var occurredAt string
		if err := rows.Scan(&event.Sequence, &event.Type, &event.StepID, &event.Message, &payloadJSON, &occurredAt); err != nil {
			return nil, err
		}
		if string(payloadJSON) != "null" {
			if err := json.Unmarshal(payloadJSON, &event.Payload); err != nil {
				return nil, fmt.Errorf("unmarshal event payload: %w", err)
			}
		}
		event.OccurredAt = decodeTime(occurredAt)
		events = append(events, event)
	}
	return events, rows.Err()
}

func insertEventRow(ctx context.Context, executor sqlExecutor, taskID string, event tasks.TaskEvent) error {
	payloadJSON, err := json.Marshal(event.Payload)
	if err != nil {
		return fmt.Errorf("marshal event payload: %w", err)
	}
	_, err = executor.ExecContext(ctx, `INSERT INTO task_events (
		task_id, sequence, type, step_id, message, payload_json, occurred_at
	) VALUES (?, ?, ?, ?, ?, ?, ?)
	ON CONFLICT(task_id, sequence) DO UPDATE SET
		type = excluded.type, step_id = excluded.step_id, message = excluded.message,
		payload_json = excluded.payload_json, occurred_at = excluded.occurred_at`,
		taskID, event.Sequence, event.Type, event.StepID, event.Message, payloadJSON, encodeTime(event.OccurredAt),
	)
	return err
}

func nextEventSequence(ctx context.Context, querier sqlQuerier, taskID string) (uint64, error) {
	var sequence uint64
	if err := querier.QueryRowContext(ctx,
		`SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id = ?`, taskID,
	).Scan(&sequence); err != nil {
		return 0, err
	}
	return sequence, nil
}

func boolInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

func encodeTime(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.UTC().Format(time.RFC3339Nano)
}

func decodeTime(value string) time.Time {
	if value == "" {
		return time.Time{}
	}
	parsed, _ := time.Parse(time.RFC3339Nano, value)
	return parsed
}
