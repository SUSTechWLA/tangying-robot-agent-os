package sqlite

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// Ledger retention.
//
// The per-task ledger is append-only by design, and that is right for the facts
// it holds: what was decided, when a task changed state, what a person concluded.
// It was not right for everything that reached it. A real deployment's ledger
// grew to 356 MB over sixty-five tasks, of which 99.3% was an observer restating a
// standing condition, and the size was not the worst of it — the record that
// exists to be read and to become training data was mostly one sentence repeated.
//
// The re-observation filter (agentruntime/ledgerfilter.go) fixed where that came
// from. This is the second line: a bound on how long the chatter is kept, so the
// ledger cannot grow without limit even if a future observer is noisy for a reason
// nobody anticipated.
//
// # Which rows may expire
//
// The rule is not "high volume". It is: **a row may expire only if another table
// holds the same fact.** The ledger's projections of execution — a tool activity
// line, its `action.executed` twin, a state transition's agent-vocabulary twin,
// an evidence pointer — each duplicate a row that `step_runs`,
// `observation_evidence` or `tasks` keeps. Those may go.
//
// Everything else is kept, and the list of what goes is written down rather than
// the list of what stays. That direction matters: a new event type is preserved by
// default, so adding a fact to the ledger cannot silently give it an expiry date
// nobody chose. Expiring something is a decision somebody writes here.
//
// It also means this is a backstop, not the primary control. What made a real
// ledger 99.3% observer output was a standing condition being re-reported every
// sixty seconds, and that is fixed where it came from
// (agentruntime/ledgerfilter.go). This bounds what a future observer nobody
// anticipated could do.

// LedgerRetention says how long the verbose half of the ledger is kept.
type LedgerRetention struct {
	// Verbose is how long an expirable row stays. Zero means "keep everything",
	// which is the behaviour of every deployment that has not asked for anything
	// else.
	Verbose time.Duration
}

// DefaultVerboseRetention is the window used when a deployment asks for pruning
// but does not choose one. A fortnight covers the debugging range of a household
// task — long enough to compare this week against last — without letting a noisy
// observer accumulate for a year.
const DefaultVerboseRetention = 14 * 24 * time.Hour

// expirableEventTypes are the rows another table already holds.
//
// Each entry names the table that keeps the same fact, because that is the reason
// it is allowed to expire. An entry whose duplicate disappears — a projection
// dropped, a table no longer written — becomes a fact with an expiry date and
// nothing behind it, which is the failure this list is shaped to prevent.
var expirableEventTypes = []struct {
	eventType string
	// duplicateIn is where the same fact lives, for a reader checking the claim.
	duplicateIn string
}{
	// A tool activity line duplicates the step run's own record.
	{"TOOL_ACTIVITY", "step_runs"},
	// The agent-vocabulary projection of the same dispatch.
	{"action.executed", "step_runs"},
	// The agent-vocabulary projection of the state change beside it.
	{"state.transition", "tasks.state plus the STATE_CHANGED row"},
	// A pointer to bytes the evidence store holds in full.
	{"evidence.collected", "observation_evidence"},
}

// LedgerSize is what the ledger currently holds.
type LedgerSize struct {
	Tasks  int64 `json:"tasks"`
	Events int64 `json:"events"`
	// VerboseEvents is how many rows are past their window and would be removed by
	// a prune with the same policy.
	VerboseEvents int64 `json:"verboseEvents"`
	// Bytes is the payload size of the event table as SQLite reports it, so a
	// deployment can see growth without a separate tool.
	Bytes int64 `json:"bytes"`
}

// LedgerSize reports the ledger's size against a retention policy.
//
// It exists to be checked before pruning as well as after: "the ledger is 4 GB"
// should be a number a deployment can read, not something discovered when a disk
// fills.
func (s *Store) LedgerSize(ctx context.Context, retention LedgerRetention, now time.Time) (LedgerSize, error) {
	var size LedgerSize
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM tasks`).Scan(&size.Tasks); err != nil {
		return LedgerSize{}, err
	}
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM task_events`).Scan(&size.Events); err != nil {
		return LedgerSize{}, err
	}
	// page_count * page_size is the whole database; the event table's share is
	// read from dbstat when the build has it, and falls back to the payload sum
	// otherwise. Either is a bound a deployment can act on, and neither is worth
	// failing a health check over.
	var bytes int64
	if err := s.db.QueryRowContext(ctx,
		`SELECT COALESCE(SUM(LENGTH(payload_json) + LENGTH(message)), 0) FROM task_events`).Scan(&bytes); err == nil {
		size.Bytes = bytes
	}
	if retention.Verbose > 0 {
		query, arguments := verboseEventQuery(retention, now)
		if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM task_events WHERE `+query, arguments...).Scan(&size.VerboseEvents); err != nil {
			return LedgerSize{}, err
		}
	}
	return size, nil
}

// PruneLedger removes verbose rows older than the policy's window.
//
// It returns how many rows went, and it refuses to run without a window: a policy
// of zero means "keep everything", and a prune that silently deleted the ledger
// would be the opposite of what the caller asked for.
func (s *Store) PruneLedger(ctx context.Context, retention LedgerRetention, now time.Time) (int64, error) {
	if retention.Verbose <= 0 {
		return 0, fmt.Errorf("refusing to prune: no retention window was given")
	}
	query, arguments := verboseEventQuery(retention, now)
	result, err := s.db.ExecContext(ctx, `DELETE FROM task_events WHERE `+query, arguments...)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

// verboseEventQuery builds the predicate for rows that are past their window and
// expirable.
//
// The types are passed as query parameters rather than interpolated, so the list
// stays a Go value a reader can see and a test can assert on. The cutoff comes
// first because it is the first placeholder in the statement.
func verboseEventQuery(retention LedgerRetention, now time.Time) (string, []any) {
	if len(expirableEventTypes) == 0 {
		// Nothing is expirable, so nothing matches. `IN ()` is not valid SQL, and
		// a predicate that matched everything would be the opposite of the list.
		return "1 = 0", nil
	}
	placeholders := make([]string, len(expirableEventTypes))
	arguments := make([]any, 0, len(expirableEventTypes)+1)
	arguments = append(arguments, now.Add(-retention.Verbose).UTC().Format(time.RFC3339Nano))
	for index, entry := range expirableEventTypes {
		placeholders[index] = "?"
		arguments = append(arguments, entry.eventType)
	}
	return "occurred_at < ? AND type IN (" + strings.Join(placeholders, ", ") + ")", arguments
}
