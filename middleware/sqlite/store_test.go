package sqlite_test

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
)

func TestCompletedStepSurvivesStoreReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	record := middleware.StepRecord{TaskID: "task-1", StepID: "pick", IdempotencyKey: "task-1-pick-1"}
	if err := store.MarkStepCompleted(context.Background(), record); err != nil {
		t.Fatal(err)
	}
	store.Close()

	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	status, err := reopened.StepStatus(context.Background(), "task-1", "pick")
	if err != nil || status != middleware.StepCompleted {
		t.Fatalf("status = %s, err = %v", status, err)
	}
}
