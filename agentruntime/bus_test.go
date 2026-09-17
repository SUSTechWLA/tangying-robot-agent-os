package agentruntime_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

func receive(t *testing.T, subscription agentruntime.Subscription) agentcontract.Event {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	event, err := subscription.Receive(ctx)
	if err != nil {
		t.Fatalf("receive: %v", err)
	}
	return event
}

func TestPublishRoutesOnlyToMatchingSubscribers(t *testing.T) {
	runtime := agentruntime.New()
	ops, err := runtime.Subscribe("ops", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	tasksOnly, err := runtime.Subscribe("task-watcher", []string{agentcontract.TopicTaskAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()

	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, TaskID: "task-1",
	})

	event := receive(t, ops)
	if event.Topic != agentcontract.TopicOpsAnomalyDetected {
		t.Fatalf("topic = %q", event.Topic)
	}
	// An observer must never be handed an event it did not subscribe to: it
	// would act on a fact it never declared an interest in.
	assertNothingPending(t, tasksOnly)
}

func TestPublishAssignsIdentityAndTimestamp(t *testing.T) {
	runtime := agentruntime.New()
	subscription, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()

	runtime.Publish(context.Background(), agentcontract.Event{Topic: agentcontract.TopicOpsAnomalyDetected})
	first := receive(t, subscription)
	runtime.Publish(context.Background(), agentcontract.Event{Topic: agentcontract.TopicOpsAnomalyDetected})
	second := receive(t, subscription)

	if first.ID == "" || second.ID == "" {
		t.Fatalf("published events have no identity: %#v %#v", first, second)
	}
	if first.ID == second.ID {
		t.Fatalf("two events share the identity %q, so they cannot be told apart", first.ID)
	}
	if first.OccurredAt.IsZero() {
		t.Fatal("a published event must say when it happened")
	}
}

func TestSubscribeRefusesAnUndeclaredTopic(t *testing.T) {
	runtime := agentruntime.New()
	defer runtime.Close()
	// A typo in a subscription is silent in production: the agent looks healthy
	// and simply never reports anything. Failing at startup is the only way it
	// becomes visible.
	_, err := runtime.Subscribe("typo", []string{"ops.anomoly_detected"}, 0)
	var unknown *agentruntime.UnknownTopicError
	if !errors.As(err, &unknown) {
		t.Fatalf("subscribe error = %v, want UnknownTopicError", err)
	}
	if unknown.Pattern != "ops.anomoly_detected" {
		t.Fatalf("error names %q, want the offending pattern", unknown.Pattern)
	}
}

func TestSlowSubscriberLosesEventsWithoutBlockingThePublisher(t *testing.T) {
	runtime := agentruntime.New(agentruntime.WithQueueCapacity(4))
	slow, err := runtime.Subscribe("slow", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()

	// The publisher is the task agent. A subscriber that never reads must not be
	// able to delay it, or observability becomes part of execution.
	done := make(chan struct{})
	go func() {
		defer close(done)
		for index := 0; index < 50; index++ {
			runtime.Publish(context.Background(), agentcontract.Event{
				Topic: agentcontract.TopicOpsAnomalyDetected, ID: eventID(index),
			})
		}
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("publishing blocked on a subscriber that was not reading")
	}

	// A subscriber that never reads keeps only what fits, and the loss is
	// counted and attributed rather than silent.
	kept := drain(t, slow)
	if kept != 4 {
		t.Fatalf("a non-reading subscriber kept %d events, want its capacity of 4", kept)
	}
	if runtime.Dropped() == 0 {
		t.Fatal("an overflowing subscriber reported no drops, so the loss is invisible")
	}
	if slow.(interface{ Dropped() uint64 }).Dropped() == 0 {
		t.Fatal("the drop was not attributed to the subscriber that caused it")
	}
}

// A subscriber that keeps up must be unaffected by one that does not. This is
// the property that per-subscriber queues exist for: with one shared queue, a
// stalled observer would consume the budget of every other observer.
func TestAReadingSubscriberIsUnaffectedByAStalledOne(t *testing.T) {
	const capacity = 8
	runtime := agentruntime.New(agentruntime.WithQueueCapacity(capacity))
	if _, err := runtime.Subscribe("stalled", []string{agentcontract.TopicOpsAll}, 0); err != nil {
		t.Fatal(err)
	}
	reader, err := runtime.Subscribe("reader", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()

	// Far more events than either queue can hold, so both overflow. The reader
	// then still receives every event it was entitled to: its own oldest
	// `capacity` events, unpushed by anything the stalled subscriber did.
	// With one shared queue instead of per-subscriber queues, the reader would
	// receive whatever the two happened to leave behind.
	for index := 0; index < capacity*10; index++ {
		runtime.Publish(context.Background(), agentcontract.Event{
			Topic: agentcontract.TopicOpsAnomalyDetected, ID: eventID(index), Priority: agentcontract.PriorityLow,
		})
	}
	if runtime.Dropped() == 0 {
		t.Fatal("nothing overflowed, so this test proves nothing")
	}

	for index := 0; index < capacity; index++ {
		event := receive(t, reader)
		if event.ID != eventID(index) {
			t.Fatalf("reader event %d = %q, want %q: a stalled neighbour changed what the reader saw",
				index, event.ID, eventID(index))
		}
	}
	if extra := drain(t, reader); extra != 0 {
		t.Fatalf("reader had %d events beyond its capacity", extra)
	}
}

func drain(t *testing.T, subscription agentruntime.Subscription) int {
	t.Helper()
	kept := 0
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
		_, err := subscription.Receive(ctx)
		cancel()
		if err != nil {
			return kept
		}
		kept++
	}
}

func TestEvictionDropsTheLeastImportantEvent(t *testing.T) {
	runtime := agentruntime.New(agentruntime.WithQueueCapacity(2))
	subscription, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()

	// Fill the queue with low-value events, then publish the one that matters.
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, ID: "low-1", Priority: agentcontract.PriorityLow,
	})
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, ID: "low-2", Priority: agentcontract.PriorityLow,
	})
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsEscalationRequired, ID: "critical",
		Priority: agentcontract.PriorityCritical,
	})

	// The critical event must survive: dropping it and keeping telemetry would
	// defeat the reason the agent exists.
	first := receive(t, subscription)
	if first.ID != "critical" {
		t.Fatalf("first delivered event = %q, want the critical one", first.ID)
	}
	if first.Priority != agentcontract.PriorityCritical {
		t.Fatalf("priority = %v", first.Priority)
	}
}

func TestHigherPriorityIsDeliveredFirst(t *testing.T) {
	runtime := agentruntime.New()
	subscription, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()

	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, ID: "low", Priority: agentcontract.PriorityLow,
	})
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, ID: "critical", Priority: agentcontract.PriorityCritical,
	})
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, ID: "normal", Priority: agentcontract.PriorityNormal,
	})

	order := []string{receive(t, subscription).ID, receive(t, subscription).ID, receive(t, subscription).ID}
	want := []string{"critical", "normal", "low"}
	for index := range want {
		if order[index] != want[index] {
			t.Fatalf("delivery order = %#v, want %#v", order, want)
		}
	}
}

func TestOrderWithinAPriorityIsPreserved(t *testing.T) {
	runtime := agentruntime.New()
	subscription, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()

	for index := 0; index < 5; index++ {
		runtime.Publish(context.Background(), agentcontract.Event{
			Topic: agentcontract.TopicOpsAnomalyDetected, ID: eventID(index), Priority: agentcontract.PriorityNormal,
		})
	}
	for index := 0; index < 5; index++ {
		if got := receive(t, subscription).ID; got != eventID(index) {
			t.Fatalf("event %d delivered as %q, want %q", index, got, eventID(index))
		}
	}
}

func TestReceiveReportsClosedSubscriptionAfterDraining(t *testing.T) {
	runtime := agentruntime.New()
	subscription, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, ID: "pending",
	})
	runtime.Close()

	// A shutdown must not silently discard an event the agent had not read yet:
	// the fact still happened and is still worth reporting.
	if event := receive(t, subscription); event.ID != "pending" {
		t.Fatalf("drained event = %q, want the pending one", event.ID)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if _, err := subscription.Receive(ctx); !errors.Is(err, agentruntime.ErrSubscriptionClosed) {
		t.Fatalf("receive after close = %v, want ErrSubscriptionClosed", err)
	}
}

func TestCloseIsIdempotentAndRefusesFurtherWork(t *testing.T) {
	runtime := agentruntime.New()
	first, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	runtime.Close()
	runtime.Close()

	if runtime.Subscribers() != 0 {
		t.Fatalf("subscribers after close = %d, want 0", runtime.Subscribers())
	}
	if _, err := runtime.Subscribe("late", []string{agentcontract.TopicOpsAll}, 0); !errors.Is(err, agentcontract.ErrShutdown) {
		t.Fatalf("subscribe after close = %v, want ErrShutdown", err)
	}
	// Publishing after close must be harmless rather than a panic: shutdown
	// races with in-flight work by construction.
	runtime.Publish(context.Background(), agentcontract.Event{Topic: agentcontract.TopicOpsAnomalyDetected})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if _, err := first.Receive(ctx); !errors.Is(err, agentruntime.ErrSubscriptionClosed) {
		t.Fatalf("receive after close = %v, want ErrSubscriptionClosed", err)
	}
}

func TestRemoveStopsDelivery(t *testing.T) {
	runtime := agentruntime.New()
	subscription, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()
	runtime.Remove(subscription)
	if runtime.Subscribers() != 0 {
		t.Fatalf("subscribers = %d, want 0", runtime.Subscribers())
	}
	runtime.Publish(context.Background(), agentcontract.Event{Topic: agentcontract.TopicOpsAnomalyDetected})
	if runtime.Published() == 0 {
		t.Fatal("the event was not counted as published")
	}
}

func TestCancelledContextIsNotDelivered(t *testing.T) {
	runtime := agentruntime.New()
	subscription, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAll}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer runtime.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	runtime.Publish(ctx, agentcontract.Event{Topic: agentcontract.TopicOpsAnomalyDetected, ID: "never"})
	assertNothingPending(t, subscription)
}

func assertNothingPending(t *testing.T, subscription agentruntime.Subscription) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	if event, err := subscription.Receive(ctx); err == nil {
		t.Fatalf("unexpected event delivered: %#v", event)
	}
}

func eventID(index int) string {
	return "evt-" + string(rune('a'+index%26)) + string(rune('0'+index/26))
}
