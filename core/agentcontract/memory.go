package agentcontract

import (
	"context"
	"time"
)

// The three memory layers an agent may read from and write to.
//
// They are declared here, next to the Agent interface, because memory is part of
// what an agent is rather than something one host provides. Declaring them now
// is the whole of the forward-looking work for ExperienceAgent: the layers and
// their read/write shapes exist, and nothing in the runtime has to change when
// an implementation of them arrives.
//
// The three layers are genuinely different things and are kept apart because
// conflating them is how a system ends up unable to answer "what happened" and
// "what do we believe" separately:
//
//   - Ledger is what happened. Append-only, ordered, never edited. It is the
//     record a replay and an audit read.
//   - Beads are what is believed. Named, versioned, revisable summaries that
//     make the next similar diagnosis faster. A belief may be revised; a fact
//     may not.
//   - Execution is what the robot did, step by step, including the steps whose
//     physical outcome is unknown. It is the reconciliation input, and it is
//     read-mostly: it is written by execution, not by memory.

// LedgerRecord is one immutable fact.
type LedgerRecord struct {
	// Sequence orders records within the ledger. It is assigned by the ledger.
	Sequence uint64 `json:"sequence"`
	// Kind is a stable discriminator such as task.state or ops.anomaly.
	Kind string `json:"kind"`
	// TaskID, when set, scopes the record to one task.
	TaskID string `json:"taskId,omitempty"`
	// Source identifies who recorded it.
	Source string `json:"source,omitempty"`
	// OccurredAt is when the fact happened, not when it was recorded.
	OccurredAt time.Time `json:"occurredAt"`
	// Payload is the structured body. Treat it as read-only.
	Payload map[string]any `json:"payload,omitempty"`
}

// Ledger is the append-only record of what happened.
type Ledger interface {
	// Append records one fact and returns it with its assigned sequence.
	Append(context.Context, LedgerRecord) (LedgerRecord, error)
	// List returns records in sequence order, oldest first, up to limit. A
	// limit of zero or less means no limit.
	List(ctx context.Context, taskID string, limit int) ([]LedgerRecord, error)
}

// Bead is one revisable unit of belief: what a diagnosis concluded, what a
// skill is, what a fault usually means.
//
// Beads carry a Version because they are revised, and a reader must be able to
// tell a conclusion from a revision of it. An observer acting on a bead needs to
// know whether it is reading the current belief or a superseded one.
type Bead struct {
	// ID is stable across revisions of the same belief.
	ID string `json:"id"`
	// Kind groups beads, for example diagnosis or skill.
	Kind string `json:"kind"`
	// Version increases with each revision, starting at 1.
	Version uint64 `json:"version"`
	// Subject is what the belief is about: a fault code, a capability, a task.
	Subject string `json:"subject,omitempty"`
	// Body is the structured content.
	Body map[string]any `json:"body,omitempty"`
	// Confidence is in [0,1]; it is what makes a belief usable for a decision.
	Confidence float64 `json:"confidence"`
	// UpdatedAt is when this revision was written.
	UpdatedAt time.Time `json:"updatedAt"`
}

// BeadQuery selects beads to read.
type BeadQuery struct {
	// Kind, when set, restricts to one kind.
	Kind string
	// Subject, when set, restricts to one subject.
	Subject string
	// Limit caps the result. Zero or less means the implementation's default.
	Limit int
}

// Beads is the revisable belief store. It is the extension point an
// ExperienceAgent fills; this version ships only an in-memory implementation so
// the interface has something to be tested against.
type Beads interface {
	// Upsert writes a bead, increasing its version when one with the same ID
	// already exists, and returns the stored revision.
	Upsert(context.Context, Bead) (Bead, error)
	// Get returns the current revision of one bead.
	Get(ctx context.Context, id string) (Bead, bool, error)
	// Query returns matching beads, most recently updated first.
	Query(context.Context, BeadQuery) ([]Bead, error)
}

// StepRecord is one durable execution step.
//
// Status is carried as a string here rather than as the execution store's own
// type so the contract stays free of that dependency; the concrete adapter is
// responsible for using the real vocabulary.
type StepRecord struct {
	TaskID         string    `json:"taskId"`
	StepID         string    `json:"stepId"`
	Capability     string    `json:"capability,omitempty"`
	SafetyLevel    string    `json:"safetyLevel,omitempty"`
	Status         string    `json:"status"`
	IdempotencyKey string    `json:"idempotencyKey,omitempty"`
	UpdatedAt      time.Time `json:"updatedAt,omitempty"`
}

// Execution is the read view of what the robot actually did.
type Execution interface {
	// Steps returns the recorded steps for a task in the order they ran.
	Steps(ctx context.Context, taskID string) ([]StepRecord, error)
	// Uncertain returns the steps whose physical outcome is unknown: they were
	// started and never confirmed or failed. This is the reconciliation input,
	// and it is the reason this layer is separate from the ledger. A step that
	// may have moved the robot must be reconciled by looking at the world, and
	// nothing may be retried automatically until it is.
	Uncertain(ctx context.Context, taskID string) ([]StepRecord, error)
}

// Memory bundles the three layers so an agent can be handed one thing.
//
// Fields are interfaces rather than a struct of concrete stores so a host can
// provide only what it has: nil means the layer is unavailable, and an agent
// must treat an unavailable layer as "cannot answer" rather than "empty".
type Memory struct {
	Ledger    Ledger
	Beads     Beads
	Execution Execution
}

// TaskHistory is the durable record of tasks that already exist.
//
// It is what an agent reads to learn about work that happened before it started.
// Live events only describe the present: a process that restarts after a failure
// sees an empty world, because nothing tells it about the tasks that were already
// there. For a supervisor that is the whole problem — the failures worth
// reviewing are exactly the ones that outlived the process that caused them.
//
// It is deliberately a port rather than a direct dependency on the task service:
// the observer needs to ask "which tasks are there, and which already ended
// badly", not to hold the authority that owns them.
type TaskHistory interface {
	// TaskIDs returns the identities of the tasks on record. Order is not
	// significant; an implementation should return them all, because a task
	// left out is a failure nobody will be told about.
	TaskIDs(ctx context.Context) ([]string, error)
	// Abnormal returns the task ids whose recorded ending was not a success.
	// A task still running is not abnormal.
	Abnormal(ctx context.Context) ([]string, error)
	// Events returns one task's recorded events, oldest first.
	//
	// The supervisor needs the ledger, not only the task's current state: a
	// failed tool dispatch is a historical fact, and the action that failed is
	// what an operator needs to see. Without this, a restarted supervisor could
	// say only "this task ended abnormally" and never which step broke.
	Events(ctx context.Context, taskID string) ([]TaskEvent, error)
}

// TaskEvent is one durable record, as much of it as a supervisor needs.
//
// It is a narrow projection of the task ledger rather than the ledger's own type,
// because the contract must not depend on the task package: the supervisor reads
// what happened, it does not own the record.
type TaskEvent struct {
	Type string
	// Payload is the structured body. Treat it as read-only.
	Payload map[string]any
}
