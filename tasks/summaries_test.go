package tasks_test

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestListSummariesReturnsNewestTasksWithoutHeavyHistory(t *testing.T) {
	store := tasks.NewMemoryStore()
	base := time.Unix(1_800_000_000, 0).UTC()
	for index := 0; index < 3; index++ {
		task := &tasks.Task{
			ID: fmt.Sprintf("task-%d", index), Request: "handoff", Adapter: "mujoco",
			State: taskgraph.StateReady, CurrentRevision: 1, AggregateVersion: uint64(index + 1),
			UpdatedAt: base.Add(time.Duration(index) * time.Minute),
			Events:    []tasks.TaskEvent{{Type: "LARGE_HISTORY", Payload: map[string]any{"blob": string(make([]byte, 4096))}}},
		}
		if err := store.Create(context.Background(), task); err != nil {
			t.Fatal(err)
		}
	}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	summaries, err := service.ListSummaries(context.Background(), 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(summaries) != 2 || summaries[0].ID != "task-2" || summaries[1].ID != "task-1" {
		t.Fatalf("summaries = %#v", summaries)
	}
	if summaries[0].AggregateVersion != 3 || summaries[0].Request != "handoff" {
		t.Fatalf("summary lost task identity: %#v", summaries[0])
	}
}

func TestListSummariesCapsRequestedLimit(t *testing.T) {
	store := tasks.NewMemoryStore()
	for index := 0; index < 110; index++ {
		if err := store.Create(context.Background(), &tasks.Task{ID: fmt.Sprintf("task-%03d", index)}); err != nil {
			t.Fatal(err)
		}
	}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	summaries, err := service.ListSummaries(context.Background(), 1000)
	if err != nil {
		t.Fatal(err)
	}
	if len(summaries) != 100 {
		t.Fatalf("summary count = %d, want 100", len(summaries))
	}
}
