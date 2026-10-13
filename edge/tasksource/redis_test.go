package tasksource

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
)

type queueStub struct {
	dequeue func(context.Context) (string, error)
	closed  bool
}

func (q *queueStub) Dequeue(ctx context.Context) (string, error) { return q.dequeue(ctx) }
func (q *queueStub) Close() error                                { q.closed = true; return nil }

func TestRedisTaskSourcePreservesDeliveryAndCancellation(t *testing.T) {
	queueFailure := errors.New("queue unavailable")
	for _, test := range []struct {
		name    string
		id      string
		err     error
		wantID  string
		wantErr error
	}{
		{name: "delivery", id: "task-42", wantID: "task-42"},
		{name: "poll timeout", err: context.DeadlineExceeded},
		{name: "shutdown", err: context.Canceled, wantErr: worker.ErrSourceStopped},
		{name: "transport failure", err: queueFailure, wantErr: queueFailure},
	} {
		t.Run(test.name, func(t *testing.T) {
			queue := &queueStub{dequeue: func(ctx context.Context) (string, error) {
				deadline, ok := ctx.Deadline()
				if !ok || time.Until(deadline) > time.Second {
					t.Fatal("queue poll must be bounded")
				}
				return test.id, test.err
			}}
			source := &RedisTaskSource{queue: queue, PollInterval: time.Second}
			id, err := source.Next(context.Background())
			if id != test.wantID || !errors.Is(err, test.wantErr) {
				t.Fatalf("Next() = %q, %v; want %q, %v", id, err, test.wantID, test.wantErr)
			}
			if err := source.Close(); err != nil || !queue.closed {
				t.Fatalf("Close() did not release queue: %v", err)
			}
		})
	}
}

func TestRedisTaskSourceRejectsMissingRobotBeforeConnecting(t *testing.T) {
	if _, err := NewRedisTaskSource("invalid:0", "", "ready", "workers", "", 0); err == nil {
		t.Fatal("missing robot id was accepted")
	}
}

var _ worker.TaskSource = (*RedisTaskSource)(nil)
