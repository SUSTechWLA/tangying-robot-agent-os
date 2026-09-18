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
	// StepFailed means the runtime rejected a command before any physical
	// effect was authorized. It remains retryable and is distinct from STARTED,
	// whose physical outcome is unknown after an interrupted invocation.
	StepFailed StepStatus = "FAILED"
)

type StepRecord struct {
	TaskID         string
	StepID         string
	IdempotencyKey string
	Capability     string
	SafetyLevel    string
}

// StepOutcome is what a person concluded about a step whose physical result was
// never recorded.
type StepOutcome string

const (
	// StepOutcomeHappened means the step did take effect, established by looking
	// at the robot or the scene afterwards.
	StepOutcomeHappened StepOutcome = "HAPPENED"
	// StepOutcomeNeverActed means it is established that nothing was dispatched,
	// so the step may be attempted again.
	StepOutcomeNeverActed StepOutcome = "NEVER_ACTED"
	// StepOutcomeAbandoned means the step is given up on: the world may have
	// changed and the task cannot continue down this path.
	StepOutcomeAbandoned StepOutcome = "ABANDONED"
)

// Valid reports whether an outcome is one this system knows how to act on.
func (o StepOutcome) Valid() bool {
	switch o {
	case StepOutcomeHappened, StepOutcomeNeverActed, StepOutcomeAbandoned:
		return true
	default:
		return false
	}
}

// StepReconciliation is a person resolving a step whose outcome was unknown.
//
// It exists because the alternative was a dead end. An unconfirmed physical step
// blocks readiness and forbids retrying the step — correctly — but the operator
// who went and looked at the robot had nowhere to record what they saw, so the
// block never lifted. The system learned to ignore its own safety report, which
// is the failure the report exists to prevent.
//
// So the clearing path is a person, and this is their signature: what they
// concluded, who they are, and on what grounds. It is never written by an agent
// or by a timer. Nothing here decides that the system was right; it records that
// somebody looked.
type StepReconciliation struct {
	Outcome    StepOutcome
	Actor      string
	Note       string
	RecordedAt time.Time
}

// StepRun is durable execution evidence, including an explicit safety class.
// Empty safety metadata is a legacy record and must be treated conservatively.
type StepRun struct {
	StepRecord
	Status StepStatus
	// Reconciled is set once a person resolved this step's unknown outcome. A
	// reconciled step is no longer uncertain: it is decided, and the decision is
	// what downstream retry and readiness rules read.
	Reconciled *StepReconciliation
}

// ReconciliationStore is the write half of recording a person's conclusion about
// an unknown outcome.
//
// It is separate from ExecutionStore because not every deployment can offer it,
// and a console that assumed otherwise would advertise a clearing path it cannot
// provide.
type ReconciliationStore interface {
	ReconcileStep(context.Context, StepRecord, StepReconciliation) error
}

type ExecutionReader interface {
	ListStepRuns(context.Context, string) ([]StepRun, error)
}

type ExecutionStore interface {
	StepStatus(context.Context, string, string) (StepStatus, error)
	MarkStepStarted(context.Context, StepRecord) error
	MarkStepCompleted(context.Context, StepRecord) error
	MarkStepFailed(context.Context, StepRecord) error
	// ReconcileStep records a person's conclusion about a step whose outcome was
	// never established. It does not change the step's status: what happened is
	// still unknown, and the record keeps saying so. What changes is that a
	// person has looked, which is what lifts the block on proceeding.
	ReconcileStep(context.Context, StepRecord, StepReconciliation) error
}
