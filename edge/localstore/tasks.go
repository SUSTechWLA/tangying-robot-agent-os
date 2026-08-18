package localstore

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

var _ tasks.Store = (*Store)(nil)

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
	approved, created_at, updated_at FROM tasks`

func insertTaskRow(ctx context.Context, executor sqlExecutor, task *tasks.Task) error {
	intentJSON, planJSON, err := taskJSON(task)
	if err != nil {
		return err
	}
	_, err = executor.ExecContext(ctx, `INSERT INTO tasks (
		id, request, adapter, intent_json, plan_json, state, approved,
		created_at, updated_at
	) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		task.ID, task.Request, task.Adapter, intentJSON, planJSON, string(task.State),
		boolInt(task.Approved), encodeTime(task.CreatedAt), encodeTime(task.UpdatedAt),
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
		created_at = ?, updated_at = ?
		WHERE id = ?`,
		task.Request, task.Adapter, intentJSON, planJSON, string(task.State), boolInt(task.Approved),
		encodeTime(task.CreatedAt), encodeTime(task.UpdatedAt), task.ID,
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
	var state, createdAt, updatedAt string
	var approved int
	if err := row.Scan(
		&task.ID, &task.Request, &task.Adapter, &intentJSON, &planJSON, &state,
		&approved, &createdAt, &updatedAt,
	); err != nil {
		return nil, err
	}
	task.State = taskgraph.TaskState(state)
	task.Approved = approved != 0
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
	return &task, nil
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
