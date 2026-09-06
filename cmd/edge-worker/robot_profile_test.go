package main

import (
	"reflect"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

func runtimeWithProfile() runtime.Snapshot {
	return runtime.Snapshot{
		RobotID: "mobile-7", Adapter: "generic_ros2", AdapterVersion: "2.3.0",
		RobotProfile: &robotcontract.Profile{
			RobotID: "mobile-7", AdapterID: "generic_ros2", AdapterVersion: "2.3.0", ModelID: "mobile-six-axis",
			Sensors: []robotcontract.Sensor{
				{SourceID: "mobile-7/lidar", SourceType: "lidar", FrameID: "lidar_link", TransformRevision: "lidar-cal-8", MaxAgeMS: 700},
				{SourceID: "mobile-7/wrist-depth", SourceType: "rgbd_camera", FrameID: "wrist_optical", TransformRevision: "wrist-cal-9", MaxAgeMS: 350},
			},
		},
	}
}

func TestRuntimeRegistrationUsesConnectedProfileWithoutXLeRobotDefaults(t *testing.T) {
	info := runtimeWithProfile()
	registration, err := resolveRuntimeRegistration(info, "", "", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if registration.robotID != "mobile-7" || registration.adapter != "generic_ros2" || registration.robotModel != "mobile-six-axis" || registration.adapterVersion != "2.3.0" || registration.transformRevision != "lidar-cal-8" {
		t.Fatalf("registration = %#v", registration)
	}
	_, sources, err := observationAdvertisements(registration.robotID, registration.adapter, registration.adapterVersion, registration.transformRevision, info.RobotProfile)
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) != 3 {
		t.Fatalf("expected proprioception and both declared sensors: %#v", sources)
	}
	if sources[1].SourceId != "mobile-7/lidar" || sources[1].SourceType != "lidar" || sources[1].FreshnessBudgetMs != 700 || !reflect.DeepEqual(sources[1].FrameIds, []string{"lidar_link", "world"}) {
		t.Fatalf("lidar registration = %#v", sources[1])
	}
	if sources[2].SourceId != "mobile-7/wrist-depth" || sources[2].TransformRevision != "wrist-cal-9" || sources[2].FreshnessBudgetMs != 350 {
		t.Fatalf("wrist registration = %#v", sources[2])
	}
	if !reflect.DeepEqual(sources[1].PayloadKinds, []string{"entity_upsert", "source_health"}) {
		t.Fatalf("points-only health reports would be rejected: %#v", sources[1])
	}
}

func TestRuntimeRegistrationRejectsExplicitProfileConflicts(t *testing.T) {
	for _, test := range []struct{ name, robotID, adapter, model string }{
		{"robot", "other-robot", "", ""},
		{"adapter", "", "mujoco", ""},
		{"model", "", "", "xlerobot-dual-arm"},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, err := resolveRuntimeRegistration(runtimeWithProfile(), test.robotID, test.adapter, test.model, ""); err == nil {
				t.Fatal("explicit profile conflict must stop registration")
			}
		})
	}
}

func TestRuntimeRegistrationAllowsSeparateBaseTransform(t *testing.T) {
	registration, err := resolveRuntimeRegistration(runtimeWithProfile(), "mobile-7", "generic_ros2", "mobile-six-axis", "base-cal-2")
	if err != nil || registration.transformRevision != "base-cal-2" {
		t.Fatalf("registration=%#v error=%v", registration, err)
	}
}

func TestLegacyRuntimeRegistrationRetainsExplicitRobotAndSimulationDefaults(t *testing.T) {
	registration, err := resolveRuntimeRegistration(runtime.Snapshot{AdapterVersion: "old"}, "robot-1", "", "", "")
	if err != nil || registration.adapter != "mujoco" || registration.robotModel != "xlerobot-sim" || registration.transformRevision != "mujoco-world-v1" {
		t.Fatalf("registration=%#v error=%v", registration, err)
	}
	if _, err := resolveRuntimeRegistration(runtime.Snapshot{}, "", "", "", ""); err == nil {
		t.Fatal("legacy runtime still requires EDGE_ROBOT_ID")
	}
}
