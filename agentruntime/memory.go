package agentruntime

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

// The memory layers an agent reads. Only Execution is backed by the real system
// here; Ledger and Beads ship as small in-process implementations so the
// interfaces have something to be tested against and so a future
// ExperienceAgent has a working thing to replace rather than an empty shape.
//
// The restraint is deliberate. A durable bead store with a schema, migrations
// and conflict rules is a project of its own, and building it now would be
// designing for a consumer that does not exist yet.

// ---------------------------------------------------------------------------
// Ledger
// ---------------------------------------------------------------------------

// MemoryLedger is an in-process append-only ledger.
//
// It is lost on restart and is not a system of record. The durable record of
// what happened is the task event stream, which already exists; this is here so
// an agent has a uniform way to append facts to a ledger-shaped thing.
type MemoryLedger struct {
	mu       sync.Mutex
	records  []agentcontract.LedgerRecord
	sequence uint64
}

// NewMemoryLedger creates an empty ledger.
func NewMemoryLedger() *MemoryLedger { return &MemoryLedger{} }

var _ agentcontract.Ledger = (*MemoryLedger)(nil)

// Append records one fact. Kind is required: a record with no kind cannot be
// selected by any reader, so accepting it would only hide a caller's mistake.
func (l *MemoryLedger) Append(_ context.Context, record agentcontract.LedgerRecord) (agentcontract.LedgerRecord, error) {
	if strings.TrimSpace(record.Kind) == "" {
		return agentcontract.LedgerRecord{}, errors.New("ledger record requires a kind")
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	l.sequence++
	stored := record
	stored.Sequence = l.sequence
	if stored.OccurredAt.IsZero() {
		stored.OccurredAt = time.Now().UTC()
	}
	stored.Payload = clonePayload(stored.Payload)
	if len(stored.Payload) == 0 {
		stored.Payload = nil
	}
	l.records = append(l.records, stored)
	return stored, nil
}

// List returns records in sequence order, oldest first.
func (l *MemoryLedger) List(_ context.Context, taskID string, limit int) ([]agentcontract.LedgerRecord, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	result := make([]agentcontract.LedgerRecord, 0, len(l.records))
	for _, record := range l.records {
		if taskID != "" && record.TaskID != taskID {
			continue
		}
		result = append(result, record)
		if limit > 0 && len(result) >= limit {
			break
		}
	}
	return result, nil
}

// Len reports how many records the ledger holds.
func (l *MemoryLedger) Len() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.records)
}

// ---------------------------------------------------------------------------
// Beads
// ---------------------------------------------------------------------------

// MemoryBeads is an in-process bead store.
//
// Revision is append-versioned rather than overwritten: Upsert returns the new
// version, so a reader that held version 3 can tell that version 4 exists. That
// is the minimum a revisable belief store must do, and it is the part worth
// pinning with tests before a durable implementation arrives.
type MemoryBeads struct {
	mu    sync.Mutex
	beads map[string]agentcontract.Bead
	now   func() time.Time
}

// NewMemoryBeads creates an empty bead store.
func NewMemoryBeads() *MemoryBeads {
	return &MemoryBeads{beads: map[string]agentcontract.Bead{}, now: time.Now}
}

var _ agentcontract.Beads = (*MemoryBeads)(nil)

// Upsert writes a bead and returns the stored revision.
func (b *MemoryBeads) Upsert(_ context.Context, bead agentcontract.Bead) (agentcontract.Bead, error) {
	if strings.TrimSpace(bead.ID) == "" {
		return agentcontract.Bead{}, errors.New("bead requires an id")
	}
	if strings.TrimSpace(bead.Kind) == "" {
		return agentcontract.Bead{}, errors.New("bead requires a kind")
	}
	if bead.Confidence < 0 || bead.Confidence > 1 {
		return agentcontract.Bead{}, fmt.Errorf("bead %s confidence %v is outside [0,1]", bead.ID, bead.Confidence)
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	stored := bead
	if existing, ok := b.beads[bead.ID]; ok {
		stored.Version = existing.Version + 1
	} else {
		stored.Version = 1
	}
	stored.UpdatedAt = b.now().UTC()
	stored.Body = clonePayload(stored.Body)
	if len(stored.Body) == 0 {
		stored.Body = nil
	}
	b.beads[stored.ID] = stored
	return stored, nil
}

// Get returns the current revision of one bead.
func (b *MemoryBeads) Get(_ context.Context, id string) (agentcontract.Bead, bool, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	bead, ok := b.beads[id]
	return bead, ok, nil
}

// Query returns matching beads, most recently updated first. Ordering is by
// recency because a belief store is read to find the most current view; an
// unordered result would make a caller re-sort to get the obvious answer.
func (b *MemoryBeads) Query(_ context.Context, query agentcontract.BeadQuery) ([]agentcontract.Bead, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	matched := make([]agentcontract.Bead, 0, len(b.beads))
	for _, bead := range b.beads {
		if query.Kind != "" && bead.Kind != query.Kind {
			continue
		}
		if query.Subject != "" && bead.Subject != query.Subject {
			continue
		}
		matched = append(matched, bead)
	}
	sort.SliceStable(matched, func(i, j int) bool {
		if matched[i].UpdatedAt.Equal(matched[j].UpdatedAt) {
			return matched[i].ID < matched[j].ID
		}
		return matched[i].UpdatedAt.After(matched[j].UpdatedAt)
	})
	if query.Limit > 0 && len(matched) > query.Limit {
		matched = matched[:query.Limit]
	}
	return matched, nil
}

// ---------------------------------------------------------------------------
// Execution
// ---------------------------------------------------------------------------

// ExecutionStore is the read view of durable step records. It is the shape
// middleware.ExecutionStore already has, narrowed to what memory needs.
type ExecutionStore interface {
	StepStatus(ctx context.Context, taskID, stepID string) (middleware.StepStatus, error)
}

// ExecutionReader is the optional list capability. A store that cannot list
// cannot answer what a task did, and the adapter says so rather than returning
// an empty list, which would read as "the task did nothing".
type ExecutionReader interface {
	ListStepRuns(ctx context.Context, taskID string) ([]middleware.StepRun, error)
}

// ExecutionMemory adapts the durable execution store to the memory port.
//
// This is the one memory layer that is real in this version, because it is the
// one an observer actually needs: telling "this step is still unconfirmed" from
// "this step failed" is the difference between a finding that says reconcile the
// world and one that says retry, and only the execution store knows it.
type ExecutionMemory struct {
	store ExecutionStore
}

// NewExecutionMemory wraps an execution store.
func NewExecutionMemory(store ExecutionStore) *ExecutionMemory {
	return &ExecutionMemory{store: store}
}

var _ agentcontract.Execution = (*ExecutionMemory)(nil)

// ErrExecutionHistoryUnavailable means the store cannot list step runs, so the
// question "what did this task do" cannot be answered.
var ErrExecutionHistoryUnavailable = errors.New("durable execution history unavailable")

// ErrNoExecutionStore means no execution store was provided.
var ErrNoExecutionStore = errors.New("no execution store configured")

// Steps returns the recorded steps for a task.
func (m *ExecutionMemory) Steps(ctx context.Context, taskID string) ([]agentcontract.StepRecord, error) {
	if m == nil || m.store == nil {
		return nil, ErrNoExecutionStore
	}
	reader, ok := m.store.(ExecutionReader)
	if !ok {
		return nil, ErrExecutionHistoryUnavailable
	}
	runs, err := reader.ListStepRuns(ctx, taskID)
	if err != nil {
		return nil, err
	}
	records := make([]agentcontract.StepRecord, 0, len(runs))
	for _, run := range runs {
		records = append(records, stepRecordFromRun(run))
	}
	return records, nil
}

// Uncertain returns the steps whose physical outcome is unknown.
//
// A step is uncertain when it was started and never reached a terminal record.
// This is the set that must be reconciled against the world before anything
// else touches the hardware, and it is deliberately derived from the durable
// records rather than from anything an agent remembers.
func (m *ExecutionMemory) Uncertain(ctx context.Context, taskID string) ([]agentcontract.StepRecord, error) {
	steps, err := m.Steps(ctx, taskID)
	if err != nil {
		return nil, err
	}
	uncertain := make([]agentcontract.StepRecord, 0)
	for _, step := range steps {
		if step.Reconciled {
			// A person has established what happened. The observer must stop
			// reporting this as unknown, or the report outlives the condition it
			// describes — which is how a safety signal becomes noise.
			continue
		}
		if middleware.StepStatus(step.Status) == middleware.StepStarted {
			uncertain = append(uncertain, step)
		}
	}
	return uncertain, nil
}

func stepRecordFromRun(run middleware.StepRun) agentcontract.StepRecord {
	// middleware.StepRun carries no timestamp: it records what happened to a
	// step, not when. The field is left zero rather than filled with a
	// convenient-looking time, because a fabricated timestamp on execution
	// evidence is worse than an absent one.
	return agentcontract.StepRecord{
		TaskID: run.TaskID, StepID: run.StepID, Capability: run.Capability,
		SafetyLevel: run.SafetyLevel, Status: string(run.Status),
		IdempotencyKey: run.IdempotencyKey,
		Reconciled:     run.Reconciled != nil,
	}
}

// NewMemory assembles the three layers, using the supplied execution store when
// one is given.
func NewMemory(store ExecutionStore) agentcontract.Memory {
	memory := agentcontract.Memory{
		Ledger: NewMemoryLedger(),
		Beads:  NewMemoryBeads(),
	}
	if store != nil {
		memory.Execution = NewExecutionMemory(store)
	}
	return memory
}
