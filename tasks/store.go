package tasks

import (
	"context"
	"errors"
)

var (
	ErrTaskNotFound = errors.New("task not found")
)

type Repository interface {
	Create(context.Context, *Task) error
	Get(context.Context, string) (*Task, error)
	Update(context.Context, *Task) error
	List(context.Context) ([]*Task, error)
}
