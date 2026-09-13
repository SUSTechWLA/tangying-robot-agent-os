package runtime

import (
	"context"
	"time"
)

// MaxDispatchBudget bounds a single invocation even if an adapter declares an
// excessive default. Longer work must be split into separately observed tools.
const MaxDispatchBudget = 10 * time.Minute

// CommandAtDispatch gives a not-yet-started tool the connected capability's
// execution budget. Planned deadlines are freshness placeholders, not task
// deadlines: only the caller context can impose an earlier task cutoff. This
// must never be applied to retries of commands that may already have executed.
func CommandAtDispatch(ctx context.Context, command Command, snapshot Snapshot, now time.Time) Command {
	budget := command.Lease
	if capability, ok := snapshot.Capability(string(command.Capability)); ok && capability.DefaultTimeout > 0 {
		budget = capability.DefaultTimeout
	}
	if budget <= 0 {
		budget = 5 * time.Second
	}
	if budget > MaxDispatchBudget {
		budget = MaxDispatchBudget
	}
	command.Deadline = now.Add(budget)
	if deadline, ok := ctx.Deadline(); ok && deadline.Before(command.Deadline) {
		command.Deadline = deadline
		budget = deadline.Sub(now)
	}
	command.Lease = budget
	return command
}
