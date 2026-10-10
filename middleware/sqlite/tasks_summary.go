package sqlite

import (
	"context"
	"database/sql"
	"errors"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var _ tasks.SummaryReader = (*Store)(nil)

// These scalar subqueries use the existing task/sequence index. Neither query
// reads plan_json, intent_json or payload_json, regardless of history size.
const taskSummarySelect = `SELECT t.id, t.state, t.current_revision, t.aggregate_version, t.updated_at,
 COALESCE((SELECT MAX(sequence) FROM task_events e WHERE e.task_id=t.id), 0),
 COALESCE((SELECT MAX(sequence) FROM task_events e WHERE e.task_id=t.id
   AND substr(e.type,1,4) != 'ops.' AND substr(e.type,1,6) != 'agent.'), 0)
 FROM tasks t`

func scanTaskSummary(row rowScanner) (tasks.Summary, error) {
	var summary tasks.Summary
	var updated string
	err := row.Scan(&summary.ID, &summary.State, &summary.CurrentRevision, &summary.AggregateVersion,
		&updated, &summary.EventSequence, &summary.SourceSequence)
	summary.UpdatedAt = decodeTime(updated)
	return summary, err
}

func (s *Store) ListSummaries(ctx context.Context) ([]tasks.Summary, error) {
	rows, err := s.db.QueryContext(ctx, taskSummarySelect+" ORDER BY t.created_at, t.id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make([]tasks.Summary, 0)
	for rows.Next() {
		summary, err := scanTaskSummary(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, summary)
	}
	return result, rows.Err()
}

func (s *Store) GetSummary(ctx context.Context, id string) (tasks.Summary, error) {
	summary, err := scanTaskSummary(s.db.QueryRowContext(ctx, taskSummarySelect+" WHERE t.id=?", id))
	if errors.Is(err, sql.ErrNoRows) {
		return tasks.Summary{}, tasks.ErrTaskNotFound
	}
	return summary, err
}
