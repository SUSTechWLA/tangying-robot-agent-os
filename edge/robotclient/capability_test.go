package robotclient

import (
	"testing"

	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

func TestSnapshotFromProtoPrefersRichCapabilityDescriptors(t *testing.T) {
	snapshot := snapshotFromProto(&robotv1.RobotCapabilities{
		RobotId:           "robot-1",
		Adapter:           "xlerobot_direct",
		ManipulationReady: false,
		Blockers:          []string{"CALIBRATION_REQUIRED"},
		Capabilities: []*robotv1.CapabilityInfo{
			{
				Name: "manipulation.pick", Available: false, Blockers: []string{"CALIBRATION_REQUIRED"},
				InputParameters: []string{"target_ref", "action_chunk"},
			},
			{Name: "observe_scene", Available: true, OutputParameters: []string{"entities"}},
		},
	})
	if snapshot.RobotID != "robot-1" || snapshot.Adapter != "xlerobot_direct" {
		t.Fatalf("snapshot = %+v", snapshot)
	}
	if snapshot.CapabilityNames()[0] != "manipulation.pick" {
		t.Fatalf("capabilities = %#v", snapshot.Capabilities)
	}
	if err := snapshot.CanExecute("observe_scene"); err != nil {
		t.Fatal(err)
	}
	if err := snapshot.CanExecute("manipulation.pick"); err == nil {
		t.Fatal("unavailable capability must be rejected")
	}
	observe, ok := snapshot.Capability("observe_scene")
	if !ok || len(observe.OutputParameters) != 1 || observe.OutputParameters[0] != "entities" {
		t.Fatalf("observe capability = %+v, ok=%v", observe, ok)
	}
	pick, _ := snapshot.Capability("manipulation.pick")
	if len(pick.InputParameters) != 2 {
		t.Fatalf("pick inputs = %#v", pick.InputParameters)
	}
}

func TestSnapshotFromProtoFallsBackToFlatSkills(t *testing.T) {
	snapshot := snapshotFromProto(&robotv1.RobotCapabilities{
		RobotId: "legacy-robot",
		Skills:  []string{"observe_scene", "manipulation.pick"},
	})
	if err := snapshot.CanExecute("observe_scene"); err != nil {
		t.Fatal(err)
	}
	if err := snapshot.CanExecute("manipulation.pick"); err != nil {
		t.Fatal(err)
	}
}
