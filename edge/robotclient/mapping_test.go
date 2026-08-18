package robotclient

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

func TestCommandToProtoMapsOnlySemanticRuntimeContract(t *testing.T) {
	deadline := time.Date(2026, 8, 18, 12, 0, 0, 0, time.UTC)
	command := runtime.Command{
		SchemaVersion:  "robot.v1",
		CommandID:      "task-1:pick",
		TaskID:         "task-1",
		Capability:     runtime.CapabilityPick,
		TargetRef:      "cup-1",
		Parameters:     map[string]any{"speed": 0.2},
		Deadline:       deadline,
		Lease:          5 * time.Second,
		IdempotencyKey: "task-1:pick:v1",
		SafetyProfile:  "desktop_standard",
		ApprovalID:     "approval-1",
	}

	got, err := commandToProto(command, "fallback-profile")
	if err != nil {
		t.Fatal(err)
	}
	if got.Skill != string(runtime.CapabilityPick) || got.TargetRef != "cup-1" {
		t.Fatalf("mapped capability = %q target = %q", got.Skill, got.TargetRef)
	}
	if got.DeadlineUnixMs != deadline.UnixMilli() || got.LeaseMs != 5000 {
		t.Fatalf("mapped timing = deadline %d lease %d", got.DeadlineUnixMs, got.LeaseMs)
	}
	if got.SafetyProfile != "desktop_standard" || got.ApprovalId != "approval-1" {
		t.Fatalf("mapped safety envelope = %+v", got)
	}
	if got.Parameters.AsMap()["speed"] != 0.2 {
		t.Fatalf("mapped parameters = %#v", got.Parameters.AsMap())
	}
}

func TestCommandToProtoAppliesAdapterDefaultsWithoutTaskGraphKnowledge(t *testing.T) {
	got, err := commandToProto(runtime.Command{
		CommandID:  "task-2:state",
		TaskID:     "task-2",
		Capability: runtime.CapabilityGetState,
		Deadline:   time.Now().Add(time.Minute),
	}, "simulation")
	if err != nil {
		t.Fatal(err)
	}
	if got.SchemaVersion != "robot.v1" || got.SafetyProfile != "simulation" {
		t.Fatalf("defaults = schema %q profile %q", got.SchemaVersion, got.SafetyProfile)
	}
}
