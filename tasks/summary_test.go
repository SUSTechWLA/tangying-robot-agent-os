package tasks

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

func TestSummaryReadsMetadataWithoutCloningHistory(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryStore()
	// A non-JSON value intentionally catches attempts to clone/decode payloads.
	store.tasks["failed"] = &Task{ID: "failed", State: taskgraph.StateRecoverableFailure, CurrentRevision: 3, AggregateVersion: 2,
		Events: []TaskEvent{{Sequence: 4, Type: "CAPABILITY_FAILED", Payload: map[string]any{"opaque": make(chan int)}},
			{Sequence: 5, Type: "ops.recovery_executed"}, {Sequence: 6, Type: "agent.health_changed"}}}
	service := NewService(&summaryOnlyStore{MemoryStore: store}, nil)
	rows, err := service.ListSummaries(ctx)
	if err != nil || len(rows) != 1 {
		t.Fatalf("summary list = %v, %v", rows, err)
	}
	row, err := service.GetSummary(ctx, "failed")
	if err != nil || !reflect.DeepEqual(row, rows[0]) || row.CurrentRevision != 3 || row.EventSequence != 6 || row.SourceSequence != 4 || row.State != taskgraph.StateRecoverableFailure {
		t.Fatalf("metadata changed: %+v, %v", row, err)
	}
	if _, err := service.GetSummary(ctx, "missing"); !errors.Is(err, ErrTaskNotFound) {
		t.Fatalf("missing task = %v", err)
	}
}

type summaryOnlyStore struct{ *MemoryStore }

func (*summaryOnlyStore) List(context.Context) ([]*Task, error) {
	return nil, errors.New("full history list is forbidden for index reads")
}
func (*summaryOnlyStore) Get(context.Context, string) (*Task, error) {
	return nil, errors.New("full history get is forbidden for index reads")
}

func TestSummaryFallbackPreservesRepositoryCompatibility(t *testing.T) {
	store := NewMemoryStore()
	ctx := context.Background()
	if err := store.Create(ctx, &Task{ID: "legacy", Events: []TaskEvent{{Sequence: 1, Type: "TOOL_ACTIVITY"}}}); err != nil {
		t.Fatal(err)
	}
	// The embedded interface deliberately hides the optional SummaryReader.
	service := NewService(struct{ Repository }{store}, nil)
	rows, err := service.ListSummaries(ctx)
	if err != nil || len(rows) != 1 || rows[0].SourceSequence != 1 {
		t.Fatalf("legacy index = %+v, %v", rows, err)
	}
	row, err := service.GetSummary(ctx, "legacy")
	if err != nil || !reflect.DeepEqual(row, rows[0]) {
		t.Fatalf("legacy single = %+v, %v", row, err)
	}
}
