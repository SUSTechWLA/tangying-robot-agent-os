package tasks_test

import (
	"context"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestSemanticFingerprintIsStableForEquivalentStepIdentity(t *testing.T) {
	left := tasks.RevisionStep{
		Action:                " pick_and_place ",
		RobotID:               " robot-1 ",
		ResourceID:            " red-block ",
		RequiredPostcondition: " red-block in handoff-zone ",
	}
	right := tasks.RevisionStep{
		Action:                "pick_and_place",
		RobotID:               "robot-1",
		ResourceID:            "red-block",
		RequiredPostcondition: "red-block in handoff-zone",
	}
	if tasks.SemanticFingerprint(left) != tasks.SemanticFingerprint(right) {
		t.Fatal("equivalent semantic identities must have one fingerprint")
	}
	right.RobotID = "robot-2"
	if tasks.SemanticFingerprint(left) == tasks.SemanticFingerprint(right) {
		t.Fatal("a different robot binding must change the fingerprint")
	}
}

func TestLegacyTaskReadsAsActiveRevisionOneWithoutMutatingStore(t *testing.T) {
	store := tasks.NewMemoryStore()
	legacy := &tasks.Task{ID: "task-legacy", Request: "move block"}
	if err := store.Create(context.Background(), legacy); err != nil {
		t.Fatal(err)
	}

	first, err := store.Get(context.Background(), legacy.ID)
	if err != nil {
		t.Fatal(err)
	}
	if first.CurrentRevision != 1 || first.AggregateVersion != 1 || first.RevisionState != tasks.RevisionActive {
		t.Fatalf("legacy revision projection = %#v", first)
	}
	first.CurrentRevision = 99

	second, err := store.Get(context.Background(), legacy.ID)
	if err != nil {
		t.Fatal(err)
	}
	if second.CurrentRevision != 1 {
		t.Fatalf("caller mutation leaked into store: revision=%d", second.CurrentRevision)
	}
}
