package tasks

import (
	"context"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
)

func TestProposalClearsSatisfiedStateWhenHarnessEvidenceIsInvalid(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryStore()
	service := NewService(store, intent.NewDeterministicParser())
	created, err := service.Create(ctx, "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	store.mu.Lock()
	stepID := store.revisions[created.ID][1].Steps[0].StepID
	store.revisions[created.ID][1].Steps[0].Status = StepSatisfied
	store.revisions[created.ID][1].Steps[0].HarnessEvidenceIDs = []string{"observation-old"}
	store.mu.Unlock()

	proposal, err := service.ProposeRevision(ctx, ProposeRevisionCommand{
		TaskID: created.ID, ExpectedRevision: 1, Request: "最后放到右侧蓝色垫子上",
		IdempotencyKey: "invalid-evidence", Creator: "owner",
	}, RevisionBasis{EvidenceValidity: map[string]bool{stepID: false}})
	if err != nil {
		t.Fatal(err)
	}
	step := proposal.Revision.Steps[0]
	if step.Status == StepSatisfied || len(step.HarnessEvidenceIDs) != 0 {
		t.Fatalf("invalid evidence leaked into proposal: %#v", step)
	}
}
