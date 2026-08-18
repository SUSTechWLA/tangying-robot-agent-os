// Package middleware defines infrastructure ports used by application code.
// Contracts are deliberately vendor-neutral: concrete SQL, Redis, Kafka and
// other SDKs belong only in adapter packages selected by a composition root.
package middleware

import (
	"context"
	"errors"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/trace"
)

var (
	ErrQueueFull              = errors.New("middleware queue is full")
	ErrQueueClosed            = errors.New("middleware queue is closed")
	ErrSubscriptionClosed     = errors.New("middleware subscription is closed")
	ErrSubscriberBackpressure = errors.New("middleware subscriber is not keeping up")
)

type Queue[T any] interface {
	Enqueue(context.Context, T) error
	Dequeue(context.Context) (T, error)
	Close() error
}

type Publisher[T any] interface {
	Publish(context.Context, T) error
}

type Subscription[T any] interface {
	Receive(context.Context) (T, error)
	Close() error
}

// Cache is an optional optimization port. Correctness must never depend on a
// cache hit; adapters may use an in-process map, Redis, or another store.
type Cache interface {
	Get(context.Context, string) ([]byte, bool, error)
	Set(context.Context, string, []byte, time.Duration) error
	Delete(context.Context, string) error
}

// Lease includes a fencing token so a future distributed lock adapter can
// reject stale owners instead of relying only on wall-clock expiry.
type Lease interface {
	Token() uint64
	Release(context.Context) error
}

type Locker interface {
	Acquire(context.Context, string, time.Duration) (Lease, error)
}

type TraceStore interface {
	Append(context.Context, trace.Event) error
	List(context.Context, string, uint64, int) ([]trace.Event, error)
}

type StepStatus string

const (
	StepPending   StepStatus = "PENDING"
	StepStarted   StepStatus = "STARTED"
	StepCompleted StepStatus = "COMPLETED"
)

type StepRecord struct {
	TaskID         string
	StepID         string
	IdempotencyKey string
}

type ExecutionStore interface {
	StepStatus(context.Context, string, string) (StepStatus, error)
	MarkStepStarted(context.Context, StepRecord) error
	MarkStepCompleted(context.Context, StepRecord) error
}
