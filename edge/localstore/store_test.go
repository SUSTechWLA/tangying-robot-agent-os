package localstore_test

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/localstore"
)

func TestCompletedStepSurvivesStoreReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := localstore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.MarkCompleted(context.Background(), "task-1", "pick", "task-1-pick-1"); err != nil {
		t.Fatal(err)
	}
	store.Close()

	reopened, err := localstore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	completed, err := reopened.Completed(context.Background(), "task-1", "pick")
	if err != nil || !completed {
		t.Fatalf("completed = %v, err = %v", completed, err)
	}
}
