package tasks

import (
	"context"
	"errors"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

var (
	ErrTaskNotFound        = errors.New("task not found")
	ErrRevisionNotFound    = errors.New("task revision not found")
	ErrRevisionConflict    = errors.New("task revision aggregate version conflict")
	ErrIdempotencyConflict = errors.New("task revision idempotency conflict")
)

type Repository interface {
	Create(context.Context, *Task) error
	CreateWithRevision(context.Context, *Task, *TaskRevision) error
	Get(context.Context, string) (*Task, error)
	Update(context.Context, *Task) error
	List(context.Context) ([]*Task, error)
	CommitRevision(context.Context, RevisionCommit) error
	Revision(context.Context, string, uint64) (RevisionRecord, error)
	ListRevisions(context.Context, string) ([]RevisionRecord, error)
}

// TaskSummary is the bounded list projection used by polling consoles. Heavy
// plans, event histories, and evidence bytes remain available on task detail
// endpoints and are deliberately excluded here.
type TaskSummary struct {
	ID               string              `json:"id"`
	Request          string              `json:"request"`
	Adapter          string              `json:"adapter"`
	Intent           manipulation.Intent `json:"intent"`
	State            taskgraph.TaskState `json:"state"`
	Approved         bool                `json:"approved"`
	CurrentRevision  uint64              `json:"currentRevision"`
	AggregateVersion uint64              `json:"aggregateVersion"`
	UpdatedAt        time.Time           `json:"updatedAt"`
}

// SummaryRepository lets persistence adapters avoid loading task event
// history for the high-frequency task list.
type SummaryRepository interface {
	ListSummaries(context.Context, int) ([]TaskSummary, error)
}

func Summarize(task *Task) TaskSummary {
	if task == nil {
		return TaskSummary{}
	}
	return TaskSummary{
		ID: task.ID, Request: task.Request, Adapter: task.Adapter, Intent: task.Intent,
		State: task.State, Approved: task.Approved, CurrentRevision: task.CurrentRevision,
		AggregateVersion: task.AggregateVersion, UpdatedAt: task.UpdatedAt,
	}
}

// BoundSummaryLimit keeps polling responses predictable in every adapter.
func BoundSummaryLimit(limit int) int {
	if limit <= 0 {
		return 20
	}
	if limit > 100 {
		return 100
	}
	return limit
}
