package worker

import (
	"context"
	"errors"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
	fleetredis "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/redis"
)

// TaskSource pulls ready task ids. Two transports exist: HTTP long-poll
// through the cloud control plane, and direct Redis Stream consumption for
// robots that share the LAN with the queue broker.
type TaskSource interface {
	// Next returns a task id, or ("", nil) when no task is currently
	// available (callers must retry), or an error.
	Next(context.Context) (string, error)
}

// ErrSourceStopped is returned when the source context is cancelled.
var ErrSourceStopped = errors.New("task source stopped")

// HTTPTaskSource long-polls the cloud ready queue for one robot.
type HTTPTaskSource struct {
	cloud   *cloudclient.Client
	robotID string
}

func NewHTTPTaskSource(cloud *cloudclient.Client, robotID string) *HTTPTaskSource {
	return &HTTPTaskSource{cloud: cloud, robotID: robotID}
}

func (s *HTTPTaskSource) Next(ctx context.Context) (string, error) {
	taskID, err := s.cloud.NextTask(ctx, s.robotID)
	if errors.Is(err, cloudclient.ErrTaskUnavailable) {
		return "", nil
	}
	return taskID, err
}

// RedisTaskSource consumes the per-robot Redis Stream directly. The stream
// name is derived from the fleet base stream (fleet.tasks.ready) plus the
// robot id, matching the control plane's fan-out.
type RedisTaskSource struct {
	queue *fleetredis.StreamQueue
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
		return "", ErrSourceStopped
	}
	return taskID, err
}

func (s *RedisTaskSource) Close() error { return s.queue.Close() }
