package tasks

import (
	"context"
	"errors"
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

// EventAppender is an optional append-only journal port. Implementations assign
// the next sequence and persist the event plus UpdatedAt atomically, without
// reading the entire event history or overwriting other task fields. Concurrent
// appenders must not reuse a sequence. The returned event is the committed fact.
type EventAppender interface {
	AppendTaskEvent(context.Context, string, TaskEvent) (TaskEvent, error)
}
