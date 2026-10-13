// Package tasksource contains task-notification transport adapters.
package tasksource

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
	"github.com/redis/go-redis/v9"
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
	PollInterval                time.Duration
	pendingTask, pendingReceipt string
}

func NewRedisTaskSource(addr, password, stream, group, robotID string, db int) (*RedisTaskSource, error) {
	if robotID == "" {
		return nil, errors.New("robot id is required for a redis task source")
	}
	name := stream + "." + robotID
	if stream == "" || group == "" {
		return nil, errors.New("stream and consumer group are required")
	}
	client := redis.NewClient(&redis.Options{Addr: addr, Password: password, DB: db})
	queue := &notificationQueue{client: client, stream: name, group: group, consumer: "edge-" + robotID}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := queue.EnsureGroup(ctx); err != nil {
		queue.Close()
		return nil, err
	}
	return &RedisTaskSource{queue: queue, PollInterval: time.Second}, nil
}

func (s *RedisTaskSource) Next(ctx context.Context) (string, error) {
	if s.pendingReceipt != "" {
		return s.pendingTask, nil
	}
	pollCtx, cancel := context.WithTimeout(ctx, s.PollInterval)
	defer cancel()
	var taskID string
	var err error
	if queue, ok := s.queue.(*notificationQueue); ok {
		taskID, s.pendingReceipt, err = queue.receive(pollCtx)
		if err == nil {
			s.pendingTask = taskID
		}
	} else {
		taskID, err = s.queue.Dequeue(pollCtx)
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "", nil
	}
	if errors.Is(err, context.Canceled) {
		return "", worker.ErrSourceStopped
	}
	return taskID, err
}

// AckNotification must only follow a successful edge journal commit. The stable
// per-robot consumer re-reads its pending entries after process restart.
func (s *RedisTaskSource) AckNotification(ctx context.Context, taskID string) error {
	queue, ok := s.queue.(*notificationQueue)
	if !ok {
		return nil
	}
	if taskID != s.pendingTask || s.pendingReceipt == "" {
		return errors.New("Redis notification receipt mismatch")
	}
	if err := queue.client.XAck(ctx, queue.stream, queue.group, s.pendingReceipt).Err(); err != nil {
		return err
	}
	s.pendingTask, s.pendingReceipt = "", ""
	return nil
}

func (s *RedisTaskSource) Close() error { return s.queue.Close() }

type notificationQueue struct {
	client                  *redis.Client
	stream, group, consumer string
}

func (q *notificationQueue) Close() error { return q.client.Close() }
func (q *notificationQueue) EnsureGroup(ctx context.Context) error {
	err := q.client.XGroupCreateMkStream(ctx, q.stream, q.group, "0").Err()
	if err != nil && !strings.Contains(err.Error(), "BUSYGROUP") {
		return err
	}
	return nil
}
func (q *notificationQueue) Dequeue(ctx context.Context) (string, error) {
	taskID, _, err := q.receive(ctx)
	return taskID, err
}
func (q *notificationQueue) receive(ctx context.Context) (string, string, error) {
	for _, cursor := range []string{"0", ">"} {
		block := time.Duration(-1)
		if cursor == ">" {
			block = time.Second
		}
		streams, err := q.client.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group: q.group, Consumer: q.consumer, Streams: []string{q.stream, cursor}, Count: 1, Block: block}).Result()
		if errors.Is(err, redis.Nil) {
			continue
		}
		if err != nil {
			return "", "", err
		}
		if len(streams) == 0 || len(streams[0].Messages) == 0 {
			continue
		}
		message := streams[0].Messages[0]
		taskID, ok := message.Values["task_id"].(string)
		if !ok || taskID == "" {
			return "", "", fmt.Errorf("Redis notification %s has no task_id", message.ID)
		}
		return taskID, message.ID, nil
	}
	return "", "", nil
}
