package worker

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
)

func TestWorldNotReadyRetriesCompletionOnly(t *testing.T) {
	attempts := 0
	err := retryWorldCompletion(context.Background(), 4, time.Millisecond, func(context.Context) error {
		attempts++
		if attempts < 3 {
			return &cloudclient.APIError{StatusCode: 409, Code: "WORLD_NOT_READY", Message: "unstable"}
		}
		return nil
	})
	if err != nil || attempts != 3 {
		t.Fatalf("attempts=%d err=%v", attempts, err)
	}

	attempts = 0
	want := errors.New("lease lost")
	err = retryWorldCompletion(context.Background(), 4, time.Millisecond, func(context.Context) error {
		attempts++
		return want
	})
	if !errors.Is(err, want) || attempts != 1 {
		t.Fatalf("non-world error attempts=%d err=%v", attempts, err)
	}
}

func TestCommandFreezesClaimIdentityAndWorldBasis(t *testing.T) {
	node := &coordinator.IntentNode{
		RobotID: "robot-2", CatalogRevision: "catalog-v7", WorldRevision: 42,
		ResourceID: "block:red-block", FencingToken: 9,
	}
	step := taskgraph.SkillStep{
		ID: "pick", Skill: "manipulation.pick", RobotID: "robot-2",
		Arguments: map[string]any{"objectId": "red-block"}, SafetyLevel: "physical_motion",
	}

	command := commandForIntentNode("task-1", step, node, "robot-2")

	if command.RobotID != "robot-2" || command.CatalogRevision != "catalog-v7" ||
		command.WorldRevisionBasis != 42 || command.ResourceID != "block:red-block" ||
		command.FencingToken != 9 {
		t.Fatalf("command identity=%#v", command)
	}
}
