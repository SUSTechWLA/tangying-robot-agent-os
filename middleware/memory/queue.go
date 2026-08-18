package memory

import (
	"context"
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

type Queue[T any] struct {
	mu     sync.Mutex
	values chan T
	closed bool
}

func NewQueue[T any](capacity int) *Queue[T] {
	if capacity < 1 {
		capacity = 1
	}
	return &Queue[T]{values: make(chan T, capacity)}
}

func (q *Queue[T]) Enqueue(ctx context.Context, value T) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.closed {
		return middleware.ErrQueueClosed
	}
	select {
	case q.values <- value:
		return nil
	default:
		return middleware.ErrQueueFull
	}
}

func (q *Queue[T]) Dequeue(ctx context.Context) (T, error) {
	select {
	case <-ctx.Done():
		var zero T
		return zero, ctx.Err()
	case value, ok := <-q.values:
		if !ok {
			var zero T
			return zero, middleware.ErrQueueClosed
		}
		return value, nil
	}
}

func (q *Queue[T]) Close() error {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.closed {
		return nil
	}
	q.closed = true
	close(q.values)
	return nil
}

var _ middleware.Queue[string] = (*Queue[string])(nil)
