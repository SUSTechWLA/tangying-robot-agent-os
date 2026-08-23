package robotclient

import (
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

func TestCommandToProtoCarriesFrozenExecutionIdentity(t *testing.T) {
	command := runtime.Command{
		SchemaVersion:      "robot.v1",
		CommandID:          "cmd-1",
		TaskID:             "task-1",
		RobotID:            "robot-2",
		Capability:         runtime.CapabilityPick,
		CatalogRevision:    strings.Repeat("a", 64),
		WorldRevisionBasis: 42,
		ResourceID:         "red-block",
		FencingToken:       9,
		Deadline:           time.Now().Add(time.Minute),
		Lease:              time.Second,
		IdempotencyKey:     "task-1/pick",
		SafetyProfile:      "simulation",
	}

	wire, err := commandToProto(command, "simulation")
	if err != nil {
		t.Fatal(err)
	}
	if wire.RobotId != "robot-2" ||
		wire.CatalogRevision != command.CatalogRevision ||
		wire.WorldRevisionBasis != 42 ||
		wire.ResourceId != "red-block" ||
		wire.FencingToken != 9 {
		t.Fatalf("wire identity lost: %#v", wire)
	}
}
