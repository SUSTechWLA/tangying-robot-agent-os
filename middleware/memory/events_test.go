package memory

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

func TestEventBusDeliversToEachSubscriber(t *testing.T) {
	bus := NewEventBus[string]()
	first := bus.Subscribe(1)
	second := bus.Subscribe(1)
	if err := bus.Publish(context.Background(), "TASK_APPROVED"); err != nil {
		t.Fatal(err)
	}
	assertReceive(t, first, "TASK_APPROVED")
	assertReceive(t, second, "TASK_APPROVED")
}

func TestEventBusReportsSlowSubscriberWithoutBlockingOthers(t *testing.T) {
	bus := NewEventBus[string]()
	slow := bus.Subscribe(1)
	fast := bus.Subscribe(2)
	if err := bus.Publish(context.Background(), "first"); err != nil {
		t.Fatal(err)
	}
	if err := bus.Publish(context.Background(), "second"); !errors.Is(err, middleware.ErrSubscriberBackpressure) {
		t.Fatalf("second publish error = %v", err)
	}
	assertReceive(t, fast, "first")
	assertReceive(t, fast, "second")
	if err := slow.Close(); err != nil {
		t.Fatal(err)
	}
}

func assertReceive[T comparable](t *testing.T, subscription middleware.Subscription[T], expected T) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	actual, err := subscription.Receive(ctx)
	if err != nil || actual != expected {
		t.Fatalf("receive = %#v, %v; want %#v", actual, err, expected)
	}
}
