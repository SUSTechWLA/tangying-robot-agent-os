package mysql

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestTaskRevisionSchemaContainsCASAndImmutableHistoryTables(t *testing.T) {
	for _, fragment := range []string{
		"aggregate_version BIGINT UNSIGNED NOT NULL DEFAULT 1",
		"CREATE TABLE IF NOT EXISTS task_revisions",
		"PRIMARY KEY (task_id, revision)",
		"UNIQUE KEY uq_task_revision_idempotency",
		"CREATE TABLE IF NOT EXISTS task_revision_events",
	} {
		if !strings.Contains(taskRevisionSchema, fragment) {
			t.Fatalf("task revision schema missing %q", fragment)
		}
	}
}

func TestMySQLStoreImplementsRevisionRepository(t *testing.T) {
	var _ tasks.Repository = (*Store)(nil)
}

func TestTaskDataForRevisionCommitIncludesTaskEventOnce(t *testing.T) {
	task := &tasks.Task{ID: "task-1", Events: []tasks.TaskEvent{{Sequence: 1, Type: "TASK_CREATED"}}}
	event := &tasks.TaskEvent{Sequence: 2, Type: "REVISION_PROPOSED"}
	wire, err := taskDataForRevisionCommit(task, event)
	if err != nil {
		t.Fatal(err)
	}
	var stored tasks.Task
	if err := json.Unmarshal(wire, &stored); err != nil {
		t.Fatal(err)
	}
	if len(stored.Events) != 2 || stored.Events[1].Type != "REVISION_PROPOSED" {
		t.Fatalf("stored events = %#v", stored.Events)
	}
	if len(task.Events) != 1 {
		t.Fatalf("helper mutated caller task = %#v", task.Events)
	}
}
