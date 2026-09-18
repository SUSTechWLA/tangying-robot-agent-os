package agentruntime

import (
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// The ledger's re-observation filter.
//
// An observing agent re-evaluates on every tick and re-publishes a condition for
// as long as it stands. That is right for a live console — an operator looking at
// a banner needs "the robot is still stopped", not "something was reported forty
// minutes ago" — and it is wrong for an append-only record, which is what the
// ledger is.
//
// The cost of getting this wrong was measured on a real deployment: of 159,314
// ledger rows, 158,193 (99.3%) were observer reports, and a single standing
// condition on a single task accounted for 1,410 of them. The ledger is also the
// replay source and the training corpus, so the repetition did not merely waste
// space — it buried the execution record the ledger exists to hold, and made the
// dataset export report "no usable samples" against a corpus that was mostly one
// sentence repeated.
//
// # The rule
//
// The ledger records transitions, not observations:
//
//   - a condition not currently open is a fact (it opened);
//   - a repeat of an open condition is not (nothing happened);
//   - a condition that closes is a fact (it ended), and reopens the identity, so
//     a condition that goes quiet and comes back is recorded again.
//
// The key is the stable identity — code and component — not the report identity.
// The report identity carries the observed count precisely so a worsening
// condition is re-announced to live subscribers; using it here would mean a
// condition that worsened every tick was recorded every tick, which is the storm
// again with a different spelling.
//
// # Where it sits
//
// Only the durable write is filtered. The bus still delivers every report, so the
// console, the recovery trigger and every other subscriber see exactly what they
// saw before. Filtering at the publisher instead would have quietened the live
// view to fix the record, which is the wrong trade.
type LedgerFilter struct {
	mu   sync.Mutex
	open map[string]struct{}
	// order preserves insertion order so the oldest condition can be evicted when
	// the set is full. Eviction costs one extra row for that condition later; the
	// alternative is a map that grows for the lifetime of a robot that runs for
	// weeks.
	order []string
	limit int
	// dropped counts evictions, so "the filter is full" is readable rather than
	// inferred from a rising row count.
	dropped uint64
}

// DefaultLedgerFilterLimit is how many open conditions are tracked.
//
// It is far above what a real deployment holds at once — the observed deployment
// carried a few dozen across sixty-five tasks — and the behaviour past it is
// bounded rather than wrong: one extra row per evicted condition per episode.
const DefaultLedgerFilterLimit = 4096

// NewLedgerFilter creates an empty filter.
func NewLedgerFilter(limit int) *LedgerFilter {
	if limit <= 0 {
		limit = DefaultLedgerFilterLimit
	}
	return &LedgerFilter{open: map[string]struct{}{}, limit: limit}
}

// Admit reports whether this event is a new fact for the durable ledger.
//
// It is safe for concurrent use and has no I/O: it is called on the path that
// writes the ledger, and a filter that could block would put observability back
// into the execution path.
func (f *LedgerFilter) Admit(event agentcontract.Event) bool {
	if f == nil {
		return true
	}
	identity, ok := agentcontract.ConditionIdentity(event.Topic, event.Payload)
	if !ok {
		// Not a finding. Execution facts, state transitions and recovery actions
		// are each one row per occurrence by construction, so they pass through.
		return true
	}
	if event.TaskID == "" {
		// The ledger is per task and the projection drops a report with no task,
		// so there is no row for this to occupy. Letting it consume an episode
		// anyway would silence the same finding for whichever task later made it
		// attributable — the failure the observer's own cooldown key was already
		// fixed for once, in the same direction.
		return true
	}
	key := event.TaskID + "\x00" + identity

	f.mu.Lock()
	defer f.mu.Unlock()
	if event.Topic == agentcontract.TopicOpsAnomalyCleared {
		f.forget(key)
		// The closing edge is always a fact: it is the one row that lets a reader
		// bound the episode.
		return true
	}
	if _, alreadyOpen := f.open[key]; alreadyOpen {
		return false
	}
	f.remember(key)
	return true
}

// Open reports how many conditions the filter currently believes are open.
func (f *LedgerFilter) Open() int {
	if f == nil {
		return 0
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.open)
}

// Dropped reports how many tracked conditions were evicted to stay bounded.
func (f *LedgerFilter) Dropped() uint64 {
	if f == nil {
		return 0
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.dropped
}

func (f *LedgerFilter) remember(key string) {
	for len(f.order) >= f.limit {
		oldest := f.order[0]
		f.order = f.order[1:]
		delete(f.open, oldest)
		f.dropped++
	}
	f.open[key] = struct{}{}
	f.order = append(f.order, key)
}

func (f *LedgerFilter) forget(key string) {
	if _, ok := f.open[key]; !ok {
		return
	}
	delete(f.open, key)
	for index, candidate := range f.order {
		if candidate == key {
			f.order = append(f.order[:index], f.order[index+1:]...)
			return
		}
	}
}
