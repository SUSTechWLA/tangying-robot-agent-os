package tasks_test

import (
	"context"
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

const versionedHandoffRequest = "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区"

func TestCreatePersistsActiveRevisionOne(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	created, err := service.Create(context.Background(), versionedHandoffRequest, "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	history, err := service.ListRevisions(context.Background(), created.ID)
	if err != nil {
		t.Fatal(err)
	}
	if created.CurrentRevision != 1 || created.AggregateVersion != 1 || len(history) != 1 {
		t.Fatalf("created=%#v history=%#v", created, history)
	}
	if history[0].Status != tasks.RevisionActive || history[0].Revision.Request != versionedHandoffRequest || len(history[0].Revision.Steps) != 2 {
		t.Fatalf("initial revision=%#v", history[0])
	}
}

func TestProposalUsesCurrentTaskContextWithoutReplacingActiveRevision(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	created, err := service.Create(context.Background(), versionedHandoffRequest, "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	proposal, err := service.ProposeRevision(context.Background(), tasks.ProposeRevisionCommand{
		TaskID: created.ID, ExpectedRevision: 1, Request: "最后放到右侧蓝色垫子上",
		IdempotencyKey: "update-1", Creator: "operator/admin",
	}, tasks.RevisionBasis{})
	if err != nil {
		t.Fatal(err)
	}
	current, err := service.Get(context.Background(), created.ID)
	if err != nil {
		t.Fatal(err)
	}
	if current.CurrentRevision != 1 || current.Request != versionedHandoffRequest || current.RevisionState != tasks.RevisionProposed {
		t.Fatalf("active task was replaced by proposal: %#v", current)
	}
	if proposal.Revision.Revision != 2 || proposal.Status != tasks.RevisionProposed || proposal.Revision.Intent.Tasks()[1].Destination.Category != "target_zone" {
		t.Fatalf("proposal=%#v", proposal)
	}
	if proposal.Revision.Intent.Tasks()[1].Destination.Attributes["color"] != "blue" || len(proposal.Revision.ChangeSet.Changed) != 1 {
		t.Fatalf("blue target update did not replan receiver: %#v", proposal.Revision)
	}
	if proposal.Revision.Understanding != "1号机器人把red-block放到交接区，然后2号机器人把red-block放到右侧蓝色垫子" {
		t.Fatalf("understanding=%q", proposal.Revision.Understanding)
	}

	replayed, err := service.ProposeRevision(context.Background(), tasks.ProposeRevisionCommand{
		TaskID: created.ID, ExpectedRevision: 1, Request: "最后放到右侧蓝色垫子上",
		IdempotencyKey: "update-1", Creator: "operator/admin",
	}, tasks.RevisionBasis{})
	if err != nil || replayed.Revision.Revision != 2 {
		t.Fatalf("idempotent replay=%#v err=%v", replayed, err)
	}
}

func TestProposalRejectsStaleExpectedRevisionWithCurrentTask(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	created, _ := service.Create(context.Background(), versionedHandoffRequest, "mujoco")
	_, err := service.ProposeRevision(context.Background(), tasks.ProposeRevisionCommand{
		TaskID: created.ID, ExpectedRevision: 9, Request: "最后放到右侧蓝色垫子上", IdempotencyKey: "stale-1",
	}, tasks.RevisionBasis{})
	var conflict *tasks.RevisionConflictError
	if !errors.As(err, &conflict) || conflict.Current == nil || conflict.Current.CurrentRevision != 1 {
		t.Fatalf("conflict=%#v err=%v", conflict, err)
	}
}

func TestConfirmActivatesNewRevisionAndSupersedesOldHistory(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	created, _ := service.Create(context.Background(), versionedHandoffRequest, "mujoco")
	proposal, _ := service.ProposeRevision(context.Background(), tasks.ProposeRevisionCommand{
		TaskID: created.ID, ExpectedRevision: 1, Request: "最后放到右侧蓝色垫子上", IdempotencyKey: "update-1",
	}, tasks.RevisionBasis{})
	active, err := service.ConfirmRevision(context.Background(), tasks.ConfirmRevisionCommand{
		TaskID: created.ID, Revision: proposal.Revision.Revision, ExpectedCurrentRevision: 1,
		IdempotencyKey: "confirm-2", Actor: "operator/admin",
	})
	if err != nil {
		t.Fatal(err)
	}
	current, _ := service.Get(context.Background(), created.ID)
	history, _ := service.ListRevisions(context.Background(), created.ID)
	if active.Status != tasks.RevisionActive || current.CurrentRevision != 2 || current.Request != "最后放到右侧蓝色垫子上" {
		t.Fatalf("active=%#v current=%#v", active, current)
	}
	if history[0].Status != tasks.RevisionSuperseded || history[1].Status != tasks.RevisionActive {
		t.Fatalf("history=%#v", history)
	}
}

func TestConfirmCanWaitForSafePointThenActivate(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	created, _ := service.Create(context.Background(), versionedHandoffRequest, "mujoco")
	proposal, _ := service.ProposeRevision(context.Background(), tasks.ProposeRevisionCommand{
		TaskID: created.ID, ExpectedRevision: 1, Request: "最后放到右侧蓝色垫子上", IdempotencyKey: "update-1",
	}, tasks.RevisionBasis{})
	waiting, err := service.ConfirmRevision(context.Background(), tasks.ConfirmRevisionCommand{
		TaskID: created.ID, Revision: proposal.Revision.Revision, ExpectedCurrentRevision: 1,
		IdempotencyKey: "confirm-wait", WaitForSafePoint: true,
	})
	if err != nil || waiting.Status != tasks.RevisionWaitingSafePoint {
		t.Fatalf("waiting=%#v err=%v", waiting, err)
	}
	activated, err := service.ActivateWaitingRevision(context.Background(), created.ID, 2, "safe-point-2")
	if err != nil || activated.Status != tasks.RevisionActive {
		t.Fatalf("activated=%#v err=%v", activated, err)
	}
}
