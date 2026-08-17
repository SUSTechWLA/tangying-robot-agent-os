package robotclient

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

func TestCommandIdentityIsScopedToTask(t *testing.T) {
	step := taskgraph.SkillStep{ID: "observe"}
	commandID, idempotencyKey := commandIdentity("task-42", step)
	if commandID != "task-42:observe" {
		t.Fatalf("commandID = %q", commandID)
	}
	if idempotencyKey != "task-42:read:observe" {
		t.Fatalf("idempotencyKey = %q", idempotencyKey)
	}
}

func TestCommandIdentityPreservesExplicitIdempotencyKey(t *testing.T) {
	step := taskgraph.SkillStep{ID: "pick", IdempotencyKey: "task-42-pick-1"}
	_, idempotencyKey := commandIdentity("task-42", step)
	if idempotencyKey != step.IdempotencyKey {
		t.Fatalf("idempotencyKey = %q", idempotencyKey)
	}
}
