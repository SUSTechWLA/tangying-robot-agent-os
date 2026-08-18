package memory

import (
	"context"
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

type EventBus[T any] struct {
	mu            sync.Mutex
	subscriptions map[*subscription[T]]struct{}
}

func NewEventBus[T any]() *EventBus[T] {
	return &EventBus[T]{subscriptions: map[*subscription[T]]struct{}{}}
}

func (b *EventBus[T]) Subscribe(capacity int) middleware.Subscription[T] {
	if capacity < 1 {
		capacity = 1
	}
	subscription := &subscription[T]{bus: b, values: make(chan T, capacity)}
	b.mu.Lock()
	b.subscriptions[subscription] = struct{}{}
	b.mu.Unlock()
	return subscription
}

func (b *EventBus[T]) Publish(ctx context.Context, event T) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	backpressure := false
	for subscription := range b.subscriptions {
		select {
		case subscription.values <- event:
		default:
			backpressure = true
		}
	}
	if backpressure {
		return middleware.ErrSubscriberBackpressure
	}
	return nil
}

func (b *EventBus[T]) remove(subscription *subscription[T]) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if _, exists := b.subscriptions[subscription]; !exists {
		return
	}
	delete(b.subscriptions, subscription)
	close(subscription.values)
}

type subscription[T any] struct {
	bus    *EventBus[T]
	values chan T
	once   sync.Once
}

func (s *subscription[T]) Receive(ctx context.Context) (T, error) {
	select {
	case <-ctx.Done():
		var zero T
		return zero, ctx.Err()
	case event, ok := <-s.values:
		if !ok {
			var zero T
			return zero, middleware.ErrSubscriptionClosed
		}
		return event, nil
	}
}

func (s *subscription[T]) Close() error {
	s.once.Do(func() { s.bus.remove(s) })
	return nil
}

var _ middleware.Publisher[string] = (*EventBus[string])(nil)
