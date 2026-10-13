package tasksource

import (
	"context"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

type lostAckHook struct{ lose bool }

func (*lostAckHook) DialHook(next redis.DialHook) redis.DialHook { return next }
func (*lostAckHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *lostAckHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		err := next(ctx, cmd)
		if err == nil && cmd.Name() == "xack" && h.lose {
			h.lose = false
			return errors.New("injected lost XACK response after Redis commit")
		}
		return err
	}
}

// EDGE_REDIS_TEST_ADDR must point to a disposable Redis. The test creates and
// removes only its uniquely named stream; no FLUSHDB or shared data mutations.
func TestRedisEnginePendingRestartAndLostAck(t *testing.T) {
	address := os.Getenv("EDGE_REDIS_TEST_ADDR")
	if address == "" {
		t.Skip("EDGE_REDIS_TEST_ADDR is not configured")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	stream, group, robot := fmt.Sprintf("edge-test-%d", time.Now().UnixNano()), "test-workers", "test-robot"
	producer := redis.NewClient(&redis.Options{Addr: address})
	defer producer.Close()
	defer producer.Del(context.Background(), stream+"."+robot)
	newSource := func() *RedisTaskSource {
		s, err := NewRedisTaskSource(address, "", stream, group, robot, 0)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = s.Close() })
		return s
	}
	source := newSource()
	if err := producer.XAdd(ctx, &redis.XAddArgs{Stream: stream + "." + robot, Values: map[string]any{"task_id": "long-task"}}).Err(); err != nil {
		t.Fatal(err)
	}
	id, err := source.Next(ctx)
	if err != nil || id != "long-task" {
		t.Fatalf("receive=%q err=%v", id, err)
	}
	assertPending := func(want int64) {
		t.Helper()
		pending, err := producer.XPending(ctx, stream+"."+robot, group).Result()
		if err != nil || pending.Count != want {
			t.Fatalf("pending=%+v err=%v want=%d", pending, err, want)
		}
	}
	assertPending(1)
	_ = source.Close() // Process loss before edge journal acknowledgement.
	source = newSource()
	id, err = source.Next(ctx)
	if err != nil || id != "long-task" {
		t.Fatalf("pending replay=%q err=%v", id, err)
	}
	assertPending(1)
	source.queue.(*notificationQueue).client.AddHook(&lostAckHook{lose: true})
	if err := source.AckNotification(ctx, id); err == nil {
		t.Fatal("lost-ack fault did not reach source")
	}
	assertPending(0) // Redis committed despite the response being lost.
	if replay, err := source.Next(ctx); err != nil || replay != id {
		t.Fatalf("retained receipt=%q err=%v", replay, err)
	}
	if err := source.AckNotification(ctx, id); err != nil {
		t.Fatalf("idempotent ACK retry: %v", err)
	}
	assertPending(0)
	t.Log("real Redis pending receipt survived source restart; lost post-commit ACK replayed without a second queue entry")
}
