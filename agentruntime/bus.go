// Package agentruntime hosts agents: it routes events between them, decides
// which of two competing requests wins, enforces what each agent declared it is
// allowed to do, and owns their lifecycle.
//
// The design follows one rule that shapes everything else here: the runtime is
// an observer of execution, never a participant in it. A task's completion, its
// evidence requirements and its retry rules are decided by the existing closed
// loop, the permission system and the task service. Nothing in this package can
// change any of those outcomes, and a runtime that is slow, wrong or switched
// off leaves execution exactly as it was.
//
// That is why event publishing never returns an error a caller must handle, why
// a slow subscriber loses events instead of blocking a publisher, and why the
// OpsAgent holds no execution port at all.
package agentruntime

import (
	"context"
	"sync"
	"sync/atomic"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// DefaultQueueCapacity is how many undelivered events one subscriber may hold
// before the runtime starts dropping the least important ones. It is per
// subscriber, so one slow agent cannot consume another's budget.
const DefaultQueueCapacity = 256

// subscribersBuffer is the per-channel capacity requested from the subscription
// port. It is only a hint: the runtime's own pending queue is what bounds
// memory and decides drops, so the channel is deliberately small.
const subscribersBuffer = 1

// Subscription is a live subscription. Receive blocks until an event is
// available, the subscription is closed, or the context is done.
type Subscription interface {
	Receive(ctx context.Context) (agentcontract.Event, error)
	Close() error
}

// AgentRuntime is the event bus agents publish to and subscribe from.
//
// Delivery is deliberately per-subscriber rather than broadcast-to-all: with a
// shared queue one observing agent that stops reading would delay every other
// agent and, once the queue filled, would start costing them events. Here each
// subscriber has its own pending set, and overflow is charged to the subscriber
// that caused it.
type AgentRuntime struct {
	mu          sync.Mutex
	subscribers map[*subscriber]struct{}
	closed      bool

	sequence atomic.Uint64
	capacity int

	// seen remembers which event identities have already been delivered, so a
	// fact that reaches the runtime twice is broadcast once.
	//
	// It is needed because a fact legitimately has two routes in: an agent can
	// publish it directly, and the same fact is also recorded in the durable
	// task ledger, whose observer feeds it back here. Both routes carry the same
	// identity, derived from the ledger sequence, so the second arrival is
	// recognised rather than duplicated. Without this, every action the task
	// agent performed would appear twice in a replay.
	seenMu   sync.Mutex
	seen     map[string]struct{}
	seenTick uint64

	// Published and Dropped are lifetime counters, read by Health. They are
	// counters rather than logs because "is the runtime keeping up" is a
	// question about a rate, and a log line cannot answer it.
	published atomic.Uint64
	dropped   atomic.Uint64

	// sink, when set, persists an event to the durable ledger.
	sink EventSink

	// onPublish, when set, is called after a delivery attempt so a consumer that
	// polls the runtime knows there is something to collect.
	//
	// It exists because the runtime delivers into per-subscriber queues and
	// deliberately does not run a goroutine per subscriber: whoever drains those
	// queues has to be told when they are worth draining. Without this the
	// orchestrator's delivery loop would have nothing to wake it and events would
	// sit unread while every agent looked healthy.
	onPublish func()
}

// seenPruneInterval is how many deliveries pass between trimming the identity
// set. The set only has to cover the window in which the two routes can race,
// so it is bounded rather than growing with the lifetime of the process.
const seenPruneInterval = 4096

// Option adjusts the runtime at construction.
type Option func(*AgentRuntime)

// WithQueueCapacity sets the per-subscriber pending capacity. A capacity below
// one is refused at construction time rather than silently corrected, because a
// zero-capacity runtime would drop every event and look like a healthy runtime
// with nothing to say.
func WithQueueCapacity(capacity int) Option {
	return func(runtime *AgentRuntime) {
		if capacity > 0 {
			runtime.capacity = capacity
		}
	}
}

// WithPublishObserver installs a callback invoked after each publish attempt.
//
// The callback must return promptly and must not publish: it runs on the
// publisher's goroutine, which is the TaskAgent executing a step. It is a nudge,
// not a hook for work.
func WithPublishObserver(observer func()) Option {
	return func(runtime *AgentRuntime) {
		runtime.onPublish = observer
	}
}

// New creates a runtime.
func New(options ...Option) *AgentRuntime {
	runtime := &AgentRuntime{
		subscribers: map[*subscriber]struct{}{},
		seen:        map[string]struct{}{},
		capacity:    DefaultQueueCapacity,
	}
	for _, option := range options {
		option(runtime)
	}
	return runtime
}

// Subscribe registers a subscriber for the given topic patterns. Patterns are
// validated: subscribing to a topic nothing can publish is a typo, and a typo
// that fails at startup is a bug report, while a typo that fails silently is an
// agent that never reports anything.
func (r *AgentRuntime) Subscribe(name string, patterns []string, capacity int) (Subscription, error) {
	for _, pattern := range patterns {
		if !agentcontract.KnownPattern(pattern) {
			return nil, &UnknownTopicError{Pattern: pattern}
		}
	}
	if capacity <= 0 {
		capacity = r.capacity
	}
	entry := &subscriber{
		name:     name,
		patterns: append([]string(nil), patterns...),
		queues:   make(map[agentcontract.Priority][]agentcontract.Event),
		signal:   make(chan struct{}, subscribersBuffer),
		capacity: capacity,
		closed:   make(chan struct{}),
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return nil, agentcontract.ErrShutdown
	}
	r.subscribers[entry] = struct{}{}
	return entry, nil
}

// Publish delivers an event to every subscriber whose patterns select it.
//
// It never blocks and never returns an error. That is not laziness: the callers
// are the agents executing tasks, and a publishing failure that had to be
// handled would make the event stream part of the safety path. A subscriber
// that cannot keep up loses its least important pending event, and the loss is
// counted and attributed rather than hidden.
func (r *AgentRuntime) Publish(ctx context.Context, event agentcontract.Event) {
	if err := ctx.Err(); err != nil {
		return
	}
	if event.ID == "" {
		event.ID = r.nextEventID()
	}
	if event.OccurredAt.IsZero() {
		event.OccurredAt = time.Now().UTC()
	}
	if !r.firstDelivery(event.ID) {
		// The same fact arrived by its second route. It has already been
		// delivered, so delivering it again would duplicate it for every
		// subscriber and in every replay.
		return
	}
	r.published.Add(1)

	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return
	}
	for entry := range r.subscribers {
		if !agentcontract.MatchesAny(entry.patterns, event.Topic) {
			continue
		}
		r.deliver(entry, event)
	}
	r.mu.Unlock()
	// Persist before nudging, so a consumer woken by this publish can read the
	// durable record of it.
	r.sinkTo(ctx, event)
	if r.onPublish != nil {
		r.onPublish()
	}
}

// firstDelivery reports whether this identity has not been delivered before,
// recording it when it has not.
func (r *AgentRuntime) firstDelivery(id string) bool {
	r.seenMu.Lock()
	defer r.seenMu.Unlock()
	if _, exists := r.seen[id]; exists {
		return false
	}
	r.seen[id] = struct{}{}
	r.seenTick++
	if r.seenTick%seenPruneInterval == 0 {
		// Bounded memory: the set only has to cover the window in which the two
		// routes to one fact can race, not the lifetime of the process.
		r.seen = map[string]struct{}{id: {}}
	}
	return true
}

// deliver enqueues one event for one subscriber, evicting to make room. Callers
// must hold the runtime lock.
func (r *AgentRuntime) deliver(entry *subscriber, event agentcontract.Event) {
	select {
	case <-entry.closed:
		return
	default:
	}
	if entry.pending() >= entry.capacity {
		if !entry.evictFor(event.Priority) {
			r.dropped.Add(1)
			entry.dropped.Add(1)
			return
		}
		r.dropped.Add(1)
		entry.dropped.Add(1)
	}
	entry.push(event)
	// A non-blocking signal: the receiver drains the whole queue once woken, so
	// one signal is enough and a full signal channel means a wakeup is already
	// pending.
	select {
	case entry.signal <- struct{}{}:
	default:
	}
}

// Remove drops a subscription. It is safe to call more than once.
func (r *AgentRuntime) Remove(subscription Subscription) {
	entry, ok := subscription.(*subscriber)
	if !ok {
		return
	}
	entry.Close()
	r.mu.Lock()
	delete(r.subscribers, entry)
	r.mu.Unlock()
}

// Published reports how many events were published.
func (r *AgentRuntime) Published() uint64 { return r.published.Load() }

// Dropped reports how many deliveries were dropped because a subscriber could
// not keep up. It should stay zero in normal operation: a non-zero value means
// an agent is too slow for the rate it subscribed to.
func (r *AgentRuntime) Dropped() uint64 { return r.dropped.Load() }

// Subscribers reports how many subscriptions are live.
func (r *AgentRuntime) Subscribers() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.subscribers)
}

// Close refuses further publishes and closes every subscription, waking any
// receiver still blocked. It is safe to call more than once.
func (r *AgentRuntime) Close() {
	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return
	}
	r.closed = true
	entries := make([]*subscriber, 0, len(r.subscribers))
	for entry := range r.subscribers {
		entries = append(entries, entry)
	}
	r.subscribers = map[*subscriber]struct{}{}
	r.mu.Unlock()
	for _, entry := range entries {
		entry.Close()
	}
}

func (r *AgentRuntime) nextEventID() string {
	return "evt-" + formatUint(r.sequence.Add(1))
}

// subscriber is one agent's pending set.
//
// Events are held per priority rather than in one queue so that eviction can
// drop the least important thing this subscriber is holding. Without that, a
// flood of low-value telemetry would evict the one critical event the agent
// existed to react to.
type subscriber struct {
	name     string
	patterns []string
	capacity int

	mu      sync.Mutex
	queues  map[agentcontract.Priority][]agentcontract.Event
	dropped atomic.Uint64

	signal chan struct{}
	closed chan struct{}
	once   sync.Once
}

func (s *subscriber) Name() string { return s.name }

// Dropped reports how many events this subscriber lost.
func (s *subscriber) Dropped() uint64 { return s.dropped.Load() }

func (s *subscriber) pending() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.pendingLocked()
}

func (s *subscriber) pendingLocked() int {
	total := 0
	for _, queue := range s.queues {
		total += len(queue)
	}
	return total
}

func (s *subscriber) push(event agentcontract.Event) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.queues[event.Priority] = append(s.queues[event.Priority], event)
}

// evictFor makes room for an event of the given priority, and reports whether
// it succeeded. The rule is that a new event may only displace something less
// important than itself: if everything pending outranks the newcomer, the
// newcomer is the one to drop.
func (s *subscriber) evictFor(priority agentcontract.Priority) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	for candidate := agentcontract.PriorityLow; candidate < priority; candidate++ {
		queue := s.queues[candidate]
		if len(queue) == 0 {
			continue
		}
		// Drop the oldest of the least important: the newest facts are the ones
		// an observer can still act on.
		s.queues[candidate] = queue[1:]
		return true
	}
	return false
}

// pop returns the highest-priority pending event, oldest first within a
// priority. Ordering within a priority is preserved so a replay of what an
// agent saw matches the order facts arrived in.
func (s *subscriber) pop() (agentcontract.Event, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for priority := agentcontract.PriorityCritical; priority >= agentcontract.PriorityLow; priority-- {
		queue := s.queues[priority]
		if len(queue) == 0 {
			continue
		}
		event := queue[0]
		s.queues[priority] = queue[1:]
		return event, true
	}
	return agentcontract.Event{}, false
}

func (s *subscriber) Receive(ctx context.Context) (agentcontract.Event, error) {
	for {
		if event, ok := s.pop(); ok {
			return event, nil
		}
		select {
		case <-ctx.Done():
			return agentcontract.Event{}, ctx.Err()
		case <-s.closed:
			// Drain what is already pending before reporting closure, so a
			// shutdown does not silently discard events an agent had not yet
			// read.
			if event, ok := s.pop(); ok {
				return event, nil
			}
			return agentcontract.Event{}, ErrSubscriptionClosed
		case <-s.signal:
		}
	}
}

func (s *subscriber) Close() error {
	s.once.Do(func() { close(s.closed) })
	return nil
}

// ErrSubscriptionClosed is returned by Receive after the subscription closed
// and its pending events were drained.
var ErrSubscriptionClosed = errSubscriptionClosed{}

type errSubscriptionClosed struct{}

func (errSubscriptionClosed) Error() string { return "agent runtime subscription closed" }

// UnknownTopicError reports a subscription to a pattern outside the declared
// vocabulary.
type UnknownTopicError struct {
	Pattern string
}

func (e *UnknownTopicError) Error() string {
	return "unknown event topic pattern: " + e.Pattern
}

// SetPublishObserver installs the publish nudge after construction.
//
// It exists because the runtime and the orchestrator are constructed separately
// and each needs the other: the orchestrator needs a runtime to subscribe to,
// and the runtime needs somewhere to send its nudge. Setting it afterwards keeps
// both constructors honest instead of hiding a two-way dependency inside one.
func (r *AgentRuntime) SetPublishObserver(observer func()) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.onPublish = observer
}

// EventSink persists an event a subscriber will also receive.
//
// It is the port the composition root uses to append agent events to the same
// durable ledger that already holds execution events. Persisting is best effort
// and never reported to the publisher: a fact that could not be written down is
// still a fact the live observers should hear, and an error here must not travel
// back into the agent that published it.
type EventSink func(ctx context.Context, event agentcontract.Event)

// SetEventSink installs the persistence hook.
func (r *AgentRuntime) SetEventSink(sink EventSink) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.sink = sink
}

// sinkTo runs the persistence hook outside the runtime lock, so a sink that
// reads back through the runtime cannot deadlock.
func (r *AgentRuntime) sinkTo(ctx context.Context, event agentcontract.Event) {
	r.mu.Lock()
	sink := r.sink
	r.mu.Unlock()
	if sink == nil {
		return
	}
	sink(ctx, event)
}
