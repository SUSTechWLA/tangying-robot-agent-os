// Package tasksource contains task-notification transport adapters.
package tasksource

import (
	"context"
	"errors"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
	fleetredis "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/redis"
)

// RedisTaskSource consumes the per-robot Redis Stream directly. The stream
// name is derived from the fleet base stream (fleet.tasks.ready) plus the
// robot id, matching the control plane's fan-out.
type RedisTaskSource struct {
	queue interface {
		Dequeue(context.Context) (string, error)
		Close() error
	}
	// PollInterval bounds each blocking dequeue so cancellation is prompt.
	PollInterval time.Duration
}

func NewRedisTaskSource(addr, password, stream, group, robotID string, db int) (*RedisTaskSource, error) {
	if robotID == "" {
		return nil, errors.New("robot id is required for a redis task source")
	}
	name := stream + "." + robotID
	queue, err := fleetredis.NewStreamQueue(addr, password, name, group, "edge-"+robotID, db)
	if err != nil {
		return nil, err
	}
	if err := queue.EnsureGroup(context.Background()); err != nil {
		queue.Close()
		return nil, err
	}
	return &RedisTaskSource{queue: queue, PollInterval: time.Second}, nil
}

func (s *RedisTaskSource) Next(ctx context.Context) (string, error) {
	pollCtx, cancel := context.WithTimeout(ctx, s.PollInterval)
	defer cancel()
	taskID, err := s.queue.Dequeue(pollCtx)
	if errors.Is(err, context.DeadlineExceeded) {
		return "", nil
	}
	if errors.Is(err, context.Canceled) {
		return "", worker.ErrSourceStopped
	}
	return taskID, err
}

func (s *RedisTaskSource) Close() error { return s.queue.Close() }
