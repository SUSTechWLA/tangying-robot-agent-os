package sqlite

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Retention has one job: bound the ledger without letting a fact expire.
//
// A real deployment's ledger reached 356 MB over sixty-five tasks and 99.3% of it
// was an observer restating one standing condition. The re-observation filter
// fixed where that came from; this is the bound that keeps a future observer from
// doing it again, and the whole risk is that it deletes something a reader came
// for.

func retentionFixture(t *testing.T) (*Store, *tasks.Service, string) {
	t.Helper()
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	return store, service, task.ID
}

// appendEventAt writes one ledger row through the service, which is the path
// every producer uses — including the agent bridge.
func appendEventAt(t *testing.T, service *tasks.Service, taskID, eventType string, at time.Time) {
	t.Helper()
	if _, err := service.AppendEvent(context.Background(), taskID, tasks.TaskEvent{
		Type: eventType, OccurredAt: at, Message: "x",
	}); err != nil {
		t.Fatal(err)
	}
}

func eventTypes(t *testing.T, service *tasks.Service, taskID string) map[string]int {
	t.Helper()
	task, err := service.Get(context.Background(), taskID)
	if err != nil {
		t.Fatal(err)
	}
	counts := map[string]int{}
	for _, event := range task.Events {
		counts[event.Type]++
	}
	return counts
}

func TestATransitionSurvivesAnyWindowAndChatterDoesNot(t *testing.T) {
	store, service, taskID := retentionFixture(t)
	now := time.Now().UTC()
	old := now.Add(-90 * 24 * time.Hour)

	appendEventAt(t, service, taskID, "STATE_CHANGED", old)
	appendEventAt(t, service, taskID, "TASK_APPROVED", old)
	appendEventAt(t, service, taskID, "TOOL_ACTIVITY", old)
	appendEventAt(t, service, taskID, "action.executed", old)
	// Inside the window: kept even though it is chatter.
	appendEventAt(t, service, taskID, "TOOL_ACTIVITY", now.Add(-time.Hour))

	removed, err := store.PruneLedger(context.Background(), LedgerRetention{Verbose: DefaultVerboseRetention}, now)
	if err != nil {
		t.Fatal(err)
	}
	if removed != 2 {
		t.Fatalf("removed = %d, want the two stale chatter rows", removed)
	}
	remaining := eventTypes(t, service, taskID)
	for _, kept := range []string{"STATE_CHANGED", "TASK_APPROVED", "TOOL_ACTIVITY"} {
		if remaining[kept] == 0 {
			t.Errorf("%s was pruned; the ledger's facts must outlive its chatter", kept)
		}
	}
	if remaining["action.executed"] != 0 {
		t.Error("an expirable row survived the window")
	}
}

// A new event type is preserved by default. The other direction would mean adding
// a fact to the ledger silently gave it an expiry date nobody chose.
func TestAnUnknownEventTypeIsPreservedRatherThanExpired(t *testing.T) {
	store, service, taskID := retentionFixture(t)
	now := time.Now().UTC()
	appendEventAt(t, service, taskID, "SOME_FUTURE_FACT", now.Add(-365*24*time.Hour))

	if _, err := store.PruneLedger(context.Background(), LedgerRetention{Verbose: time.Hour}, now); err != nil {
		t.Fatal(err)
	}
	if eventTypes(t, service, taskID)["SOME_FUTURE_FACT"] == 0 {
		t.Fatal("a type nobody listed as verbose was deleted")
	}
}

// A prune with no window is a request to delete the ledger. It is refused.
func TestPruningWithoutAWindowIsRefused(t *testing.T) {
	store, service, taskID := retentionFixture(t)
	appendEventAt(t, service, taskID, "TOOL_ACTIVITY", time.Now().Add(-1000*time.Hour))
	if _, err := store.PruneLedger(context.Background(), LedgerRetention{}, time.Now().UTC()); err == nil {
		t.Fatal("a prune with no retention window was allowed to run")
	}
}

// The size report has to be readable before the disk fills, not after.
func TestTheSizeReportCountsWhatAPruneWouldRemove(t *testing.T) {
	store, service, taskID := retentionFixture(t)
	now := time.Now().UTC()
	for index := 0; index < 3; index++ {
		appendEventAt(t, service, taskID, "TOOL_ACTIVITY", now.Add(-30*24*time.Hour))
	}
	appendEventAt(t, service, taskID, "STATE_CHANGED", now)

	size, err := store.LedgerSize(context.Background(), LedgerRetention{Verbose: DefaultVerboseRetention}, now)
	if err != nil {
		t.Fatal(err)
	}
	if size.Tasks != 1 {
		t.Fatalf("tasks = %d", size.Tasks)
	}
	if size.VerboseEvents != 3 {
		t.Fatalf("verboseEvents = %d, want 3", size.VerboseEvents)
	}
	if size.Bytes == 0 {
		t.Fatal("bytes = 0; the report cannot show growth it does not measure")
	}
}
