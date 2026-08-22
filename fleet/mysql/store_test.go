package mysql

import (
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
