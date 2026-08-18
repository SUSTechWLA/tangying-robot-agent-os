package memory

import (
	"context"
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

func TestQueueReturnsBackpressureWithoutBlocking(t *testing.T) {
	queue := NewQueue[string](1)
	if err := queue.Enqueue(context.Background(), "task-1"); err != nil {
		t.Fatal(err)
	}
	if err := queue.Enqueue(context.Background(), "task-2"); !errors.Is(err, middleware.ErrQueueFull) {
		t.Fatalf("second enqueue error = %v", err)
	}
	value, err := queue.Dequeue(context.Background())
	if err != nil || value != "task-1" {
		t.Fatalf("dequeue = %q, %v", value, err)
	}
}

func TestQueueCloseAndCancellationAreObservable(t *testing.T) {
	queue := NewQueue[string](1)
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := queue.Dequeue(cancelled); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled dequeue error = %v", err)
	}
	if err := queue.Close(); err != nil {
		t.Fatal(err)
	}
	if err := queue.Enqueue(context.Background(), "late"); !errors.Is(err, middleware.ErrQueueClosed) {
		t.Fatalf("enqueue after close error = %v", err)
	}
	if _, err := queue.Dequeue(context.Background()); !errors.Is(err, middleware.ErrQueueClosed) {
		t.Fatalf("dequeue after close error = %v", err)
	}
}
