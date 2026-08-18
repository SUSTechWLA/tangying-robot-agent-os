package orchestrator

import (
	"context"
	"errors"
)

var (
	ErrTaskNotFound  = errors.New("task not found")
	ErrLeaseNotFound = errors.New("lease not found")
)

type Store interface {
	Create(context.Context, *Task) error
	Get(context.Context, string) (*Task, error)
	Update(context.Context, *Task) error
	List(context.Context) ([]*Task, error)
}
