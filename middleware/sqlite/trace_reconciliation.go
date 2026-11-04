package sqlite

import (
	"context"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var _ tasks.ReconciliationReader = (*Store)(nil)

// ListReconciliations exposes operator conclusions as task-scoped audit data.
// It preserves execution status; no conclusion is promoted to completion or
// permission to replay an action.
func (s *Store) ListReconciliations(ctx context.Context, taskID string) ([]tasks.LocalReconciliation, error) {
	runs, err := s.ListStepRuns(ctx, taskID)
	if err != nil {
		return nil, err
	}
	records := []tasks.LocalReconciliation{}
	for _, run := range runs {
		if run.Reconciled == nil {
			continue
		}
		records = append(records, tasks.LocalReconciliation{
			StepID: run.StepID, Capability: run.Capability, OriginalStatus: string(run.Status),
			Outcome: string(run.Reconciled.Outcome), Actor: run.Reconciled.Actor,
			Note: run.Reconciled.Note, RecordedAt: run.Reconciled.RecordedAt,
		})
	}
	return records, nil
}
