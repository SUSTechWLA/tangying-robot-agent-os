package recoveryexec

import "context"

// taskContextKey carries the task an execution is about into the tools.
//
// # Why the task id travels in the context rather than in the arguments
//
// Some tools need to know which task they are working on — `execution.read-history`
// reads that task's step records. That id is not a decision: it is a fact about
// the execution, established before the loop starts.
//
// Routing it as a tool argument would make it something a decider supplies, and a
// decider that can supply a task id can supply *another* task's id. It would also
// make the tool take a parameter, which the deterministic single-tool decider
// refuses to call precisely because it will not invent values. Both problems
// disappear when the runtime states the fact and the tool reads it.
//
// An empty task id is a real state — a robot-level execution — and tools must
// handle it rather than treat it as an error.
type taskContextKey struct{}

// WithTaskID records which task an execution is about.
func WithTaskID(ctx context.Context, taskID string) context.Context {
	if taskID == "" {
		return ctx
	}
	return context.WithValue(ctx, taskContextKey{}, taskID)
}

// TaskIDFrom returns the task an execution is about, or "" for a robot-level one.
func TaskIDFrom(ctx context.Context) string {
	taskID, _ := ctx.Value(taskContextKey{}).(string)
	return taskID
}
