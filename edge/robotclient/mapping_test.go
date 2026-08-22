package robotclient

import (
	"slices"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
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

func TestCommandToProtoCarriesRevisionStepAndFencingIdentity(t *testing.T) {
	got, err := commandToProto(runtime.Command{
		SchemaVersion: "robot.v1", CommandID: "cmd-1", TaskID: "task-1", RobotID: "robot-2",
		Capability: runtime.CapabilityPick, TaskRevision: 2, AggregateVersion: 7,
		StepID: "handoff/receiver", FencingToken: 3, Deadline: time.Now().Add(time.Minute),
	}, "simulation")
	if err != nil {
		t.Fatal(err)
	}
	if got.TaskRevision != 2 || got.AggregateVersion != 7 || got.StepId != "handoff/receiver" || got.FencingToken != 3 {
		t.Fatalf("wire identity = %#v", got)
	}
}

func TestSnapshotFromProtoMapsHumanToolMetadata(t *testing.T) {
	snapshot := snapshotFromProto(&robotv1.RuntimeInfo{Capabilities: []*robotv1.CapabilityInfo{{
		Name: "manipulation.pick", DisplayName: "拿稳物品", Purpose: "安全拿起指定物品",
		SafeArgumentNames: []string{"targetRef"}, Available: true,
	}}})
	if snapshot.Capabilities[0].DisplayName != "拿稳物品" || snapshot.Capabilities[0].Purpose != "安全拿起指定物品" ||
		!slices.Equal(snapshot.Capabilities[0].SafeArgumentNames, []string{"targetRef"}) {
		t.Fatalf("snapshot capability = %#v", snapshot.Capabilities[0])
	}
}
