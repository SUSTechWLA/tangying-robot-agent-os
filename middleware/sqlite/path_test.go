package sqlite

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

func TestOpenRelativeNestedAndEscapedFilePaths(t *testing.T) {
	// Keep the process working directory unchanged: other package tests may
	// open files concurrently. This directory remains relative at the API.
	relativeRoot, err := os.MkdirTemp(".", "sqlite-relative-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(relativeRoot) })
	for _, path := range []string{
		filepath.Join(relativeRoot, "artifacts", "episode", "agent.db"),
		filepath.Join(relativeRoot, "space dir", "question?dir", "agent #100%.db"),
		filepath.Join(t.TempDir(), "space dir", "question?dir", "agent #100%.db"),
	} {
		t.Run(path, func(t *testing.T) {
			if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
				t.Fatal(err)
			}
			store, err := Open(path)
			if err != nil {
				t.Fatalf("Open(%q): %v", path, err)
			}
			record := middleware.StepRecord{TaskID: "path-task", StepID: "step-1", IdempotencyKey: "path-task/step-1"}
			if err := store.MarkStepCompleted(context.Background(), record); err != nil {
				_ = store.Close()
				t.Fatal(err)
			}
			if err := store.Close(); err != nil {
				t.Fatal(err)
			}
			if _, err := os.Stat(path); err != nil {
				t.Fatalf("database not at exact file name %q: %v", path, err)
			}
			reopened, err := Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer reopened.Close()
			state, err := reopened.StepStatus(context.Background(), "path-task", "step-1")
			if err != nil || state != middleware.StepCompleted {
				t.Fatalf("reopened state=%v err=%v", state, err)
			}
		})
	}
}

func TestOpenMemoryRemainsPrivateAndDoesNotCreateAFile(t *testing.T) {
	store, err := Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	record := middleware.StepRecord{TaskID: "memory-task", StepID: "step-1", IdempotencyKey: "memory-task/step-1"}
	if err := store.MarkStepCompleted(context.Background(), record); err != nil {
		t.Fatal(err)
	}
	var name, file string
	var sequence int
	if err := store.db.QueryRow("PRAGMA database_list").Scan(&sequence, &name, &file); err != nil {
		t.Fatal(err)
	}
	if file != "" {
		t.Fatalf("memory database became a file: %q", file)
	}
	if store.db.Stats().MaxOpenConnections != 1 {
		t.Fatal("private memory database permits independent pooled databases")
	}
	independent, err := Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer independent.Close()
	state, err := independent.StepStatus(context.Background(), "memory-task", "step-1")
	if err != nil || state == middleware.StepCompleted {
		t.Fatalf("independent memory stores shared data: state=%v err=%v", state, err)
	}
}
