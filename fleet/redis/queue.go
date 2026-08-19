package redis

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// StreamQueue implements middleware.Queue[string] with Redis Streams and a
// consumer group, suitable for a multi-instance Fleet control plane or edge
// worker pool.
type StreamQueue struct {
	client   *redis.Client
	stream   string
	group    string
	consumer string
	block    time.Duration
}

func NewStreamQueue(addr, password, stream, group, consumer string, db int) (*StreamQueue, error) {
	if stream == "" || group == "" || consumer == "" {
		return nil, errors.New("stream, group, and consumer are required")
	}
	client := redis.NewClient(&redis.Options{Addr: addr, Password: password, DB: db})
	return &StreamQueue{client: client, stream: stream, group: group, consumer: consumer, block: time.Second}, nil
}

func (q *StreamQueue) EnsureGroup(ctx context.Context) error {
	err := q.client.XGroupCreateMkStream(ctx, q.stream, q.group, "0").Err()
	if err != nil && !strings.Contains(err.Error(), "BUSYGROUP") {
		return err
	}
	return nil
}

func (q *StreamQueue) Enqueue(ctx context.Context, taskID string) error {
	return q.client.XAdd(ctx, &redis.XAddArgs{Stream: q.stream, Values: map[string]any{"task_id": taskID}}).Err()
}

func (q *StreamQueue) Dequeue(ctx context.Context) (string, error) {
	for {
		result, err := q.client.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group:    q.group,
			Consumer: q.consumer,
			Streams:  []string{q.stream, ">"},
			Count:    1,
			Block:    q.block,
		}).Result()
		if errors.Is(err, redis.Nil) || len(result) == 0 {
			select {
			case <-ctx.Done():
				return "", ctx.Err()
			default:
				continue
			}
		}
		if err != nil {
			return "", err
		}
		if len(result[0].Messages) == 0 {
			continue
		}
		message := result[0].Messages[0]
		taskID, ok := message.Values["task_id"].(string)
		if !ok || taskID == "" {
			return "", fmt.Errorf("redis stream message is missing task_id")
		}
		if err := q.client.XAck(ctx, q.stream, q.group, message.ID).Err(); err != nil {
			return "", err
		}
		return taskID, nil
	}
}

func (q *StreamQueue) Close() error { return q.client.Close() }
