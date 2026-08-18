package sqlite

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var _ tasks.Repository = (*Store)(nil)
var _ middleware.ExecutionStore = (*Store)(nil)

func TestExecutionRecordSurvivesReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	record := middleware.StepRecord{TaskID: "task-1", StepID: "pick", IdempotencyKey: "pick-1"}
	if err := store.MarkStepStarted(context.Background(), record); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	status, err := reopened.StepStatus(context.Background(), "task-1", "pick")
	if err != nil || status != middleware.StepStarted {
		t.Fatalf("status = %s, err = %v", status, err)
	}
}
