package tasksource

import (
	"context"
	"net"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Command hook exercises the real go-redis command construction and source
// lifecycle without requiring a Redis daemon. It is a protocol fixture, not
// evidence of Redis engine or network-partition acceptance.
type pendingRedisFixture struct {
	t                  *testing.T
	pending, delivered bool
	cursors            []string
	acks               int
}

func (h *pendingRedisFixture) DialHook(next redis.DialHook) redis.DialHook {
	return func(context.Context, string, string) (net.Conn, error) {
		h.t.Fatal("fixture unexpectedly dialed Redis")
		return nil, nil
	}
}
func (h *pendingRedisFixture) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *pendingRedisFixture) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		switch c := cmd.(type) {
		case *redis.XStreamSliceCmd:
			args := c.Args()
			cursor := args[len(args)-1].(string)
			h.cursors = append(h.cursors, cursor)
			if (cursor == "0" && h.pending) || (cursor == ">" && !h.delivered) {
				h.pending, h.delivered = true, true
				c.SetVal([]redis.XStream{{Stream: "ready.robot-7", Messages: []redis.XMessage{{ID: "42-0", Values: map[string]any{"task_id": "task-long"}}}}})
			} else {
				c.SetVal(nil)
			}
		case *redis.IntCmd:
			if !reflect.DeepEqual(c.Args(), []any{"xack", "ready.robot-7", "workers", "42-0"}) {
				h.t.Fatalf("unexpected acknowledgement: %v", c.Args())
			}
			h.pending = false
			h.acks++
			c.SetVal(1)
		default:
			h.t.Fatalf("unexpected Redis command: %T %v", cmd, cmd.Args())
		}
		return nil
	}
}

func TestRedisPendingNotificationReplaysAfterSourceRestartUntilJournalAck(t *testing.T) {
	fixture := &pendingRedisFixture{t: t}
	newSource := func() *RedisTaskSource {
		client := redis.NewClient(&redis.Options{Addr: "fixture.invalid:6379"})
		client.AddHook(fixture)
		t.Cleanup(func() { _ = client.Close() })
		return &RedisTaskSource{queue: &notificationQueue{client: client, stream: "ready.robot-7", group: "workers", consumer: "edge-robot-7"}, PollInterval: time.Second}
	}
	ctx := context.Background()
	source := newSource()
	id, err := source.Next(ctx)
	if err != nil || id != "task-long" || fixture.acks != 0 {
		t.Fatalf("delivery=%q err=%v acks=%d", id, err, fixture.acks)
	}
	if !reflect.DeepEqual(fixture.cursors, []string{"0", ">"}) {
		t.Fatalf("cursors=%v", fixture.cursors)
	}
	// A crash before the inbox commit must leave the Redis entry pending.
	restarted := newSource()
	id, err = restarted.Next(ctx)
	if err != nil || id != "task-long" || fixture.cursors[len(fixture.cursors)-1] != "0" || fixture.acks != 0 {
		t.Fatalf("restart delivery=%q err=%v cursors=%v acks=%d", id, err, fixture.cursors, fixture.acks)
	}
	if err := restarted.AckNotification(ctx, "wrong-task"); err == nil {
		t.Fatal("foreign journal receipt acknowledged")
	}
	if err := restarted.AckNotification(ctx, id); err != nil {
		t.Fatal(err)
	}
	if fixture.pending || fixture.acks != 1 {
		t.Fatalf("pending=%v acks=%d", fixture.pending, fixture.acks)
	}
	if id, err := restarted.Next(ctx); err != nil || id != "" {
		t.Fatalf("acked task replayed: %q %v", id, err)
	}
}
