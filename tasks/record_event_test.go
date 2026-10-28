package tasks_test

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestRecordEventFallbackRetainsSequenceIdentityAndObserver(t *testing.T) {
	service, task := newTaskService(t)
	recorder := &eventRecorder{}
	service.ObserveEvents(recorder.observe)
	at := time.Date(2026, 10, 28, 1, 2, 3, 0, time.UTC)
	first, err := service.RecordEvent(context.Background(), task.ID, tasks.TaskEvent{Type: "TOOL_ACTIVITY", OccurredAt: at})
	if err != nil {
		t.Fatal(err)
	}
	second, err := service.RecordEvent(context.Background(), task.ID, tasks.TaskEvent{Type: "POLICY_PREPARATION_FAILED", Payload: map[string]any{"eventId": "external-fact"}})
	if err != nil {
		t.Fatal(err)
	}
	_, observed := recorder.snapshot()
	stored, err := service.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if first.Sequence != 1 || first.Payload["eventId"] != task.ID+"#1" || !first.OccurredAt.Equal(at) || second.Sequence != 2 || second.Payload["eventId"] != "external-fact" || len(observed) != 2 || len(stored.Events) != 2 {
		t.Fatalf("facts changed: first=%+v second=%+v observed=%+v stored=%+v", first, second, observed, stored)
	}
}
