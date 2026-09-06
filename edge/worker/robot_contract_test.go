package worker

import (
	"context"
	"encoding/json"
	"math"
	"reflect"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

func contractSnapshot() telemetry.Snapshot {
	return telemetry.Snapshot{
		RobotID: "mobile-7", ObservedAt: time.UnixMilli(1_780_000_000_000).UTC(),
		RobotProfile: &robotcontract.Profile{
			RobotID: "mobile-7", AdapterID: "generic_ros2", AdapterVersion: "2.3.0", ModelID: "mobile-six-axis",
			Sensors: []robotcontract.Sensor{{SourceID: "mobile-7/lidar", SourceType: "lidar", FrameID: "lidar_link", TransformRevision: "lidar-cal-8", MaxAgeMS: 700}},
		},
		Reconstruction: &robotcontract.Reconstruction{
			RobotID: "mobile-7", ObservationID: "lidar-frame-42", SourceID: "mobile-7/lidar", SourceType: "lidar",
			SourceFrameID: "lidar_link", FrameID: "world", TransformRevision: "lidar-cal-8", Units: "m", Sequence: 42,
			ObservedAtUnixMS: 1_780_000_000_000,
			Entities:         []robotcontract.Entity{{EntityID: "package-1", Category: "block", Pose: []float64{4, 5, 0.8, 1, 0, 0, 0}, Confidence: .98, Relation: "on:rack-1"}},
			Points:           [][]float64{{4, 5, .8}},
		},
		RobotState: map[string]any{"reward": 1.0},
	}
}

func TestReconstructionWorldProjectionPreservesAcquisitionAndSource(t *testing.T) {
	snapshot := contractSnapshot()
	w := New(Config{RobotID: "mobile-7", Adapter: "generic_ros2", AdapterVersion: "2.3.0", WorldPose: []float64{10, 20, 1, 0}, TransformRevision: "base-cal-2"})
	sample := w.sampleFromTelemetry(snapshot)
	if len(sample.Entities) != 1 || !reflect.DeepEqual(sample.Entities[0].Pose, []float64{4, 5, .8, 1, 0, 0, 0}) {
		t.Fatalf("world coordinates must not be offset again: %#v", sample.Entities)
	}
	if sample.Reconstruction == nil || !reflect.DeepEqual(sample.Reconstruction.Points, [][]float64{{4, 5, .8}}) {
		t.Fatal("reconstruction points must remain available for internal projection")
	}
	wire, err := json.Marshal(sample)
	if err != nil {
		t.Fatal(err)
	}
	var public map[string]any
	if err := json.Unmarshal(wire, &public); err != nil {
		t.Fatal(err)
	}
	if _, found := public["Reconstruction"]; found {
		t.Fatal("raw reconstruction must not leak into low-rate fleet JSON")
	}
	event := w.observationsFromSample(sample)[1]
	if event.SourceID != "mobile-7/lidar" || event.SourceType != observation.SourceLidar || event.FrameID != "world" || event.TransformRevision != "lidar-cal-8" || event.Provenance.Sensor != "lidar_link" {
		t.Fatalf("lost source identity: %#v", event)
	}
	if !event.ObservedAt.Equal(snapshot.ObservedAt) || event.Provenance.Adapter != "generic_ros2" || event.Provenance.Version != "2.3.0" {
		t.Fatalf("lost acquisition/provenance: %#v", event)
	}
	if event.Payload.(observation.EntityPayload).Relations["on"] != "rack-1" {
		t.Fatalf("lost semantic relation: %#v", event.Payload)
	}
	repeated := w.observationsFromSample(sample)[1]
	if repeated.ObservationID != event.ObservationID || !repeated.ObservedAt.Equal(event.ObservedAt) {
		t.Fatal("resampling the same frame must not invent fresh evidence")
	}
}

func TestLegacyPhysicalObservationUsesCameraSourceAndKeepsMissingTime(t *testing.T) {
	w := New(Config{RobotID: "robot-1", Adapter: "xlerobot_direct"})
	sample := w.sampleFromTelemetry(telemetry.Snapshot{Entities: []telemetry.Entity{{EntityID: "cup", Category: "cup"}}})
	if !sample.ObservedAt.IsZero() {
		t.Fatal("missing runtime acquisition time must not be replaced by the polling time")
	}
	sample.ObservedAt = time.Unix(100, 0).UTC()
	if source := w.observationsFromSample(sample)[1].SourceType; source != observation.SourceRGBDCamera {
		t.Fatalf("physical perception mislabeled as %q", source)
	}
}

func TestPolicyReconstructionUsesActualModelAndTransform(t *testing.T) {
	snapshot := contractSnapshot()
	w := New(Config{RobotID: "mobile-7", Adapter: "generic_ros2", WorldPose: []float64{10, 20, 1, 0}, TransformRevision: "base-cal-2", Observer: staticObserver{snapshot}})
	bundle, err := w.buildPolicyObservation(context.Background(), runtime.Command{CommandID: "cmd-1"})
	if err != nil {
		t.Fatal(err)
	}
	if bundle.RobotModel != "mobile-six-axis" || bundle.Adapter != "generic_ros2" || bundle.TransformRevision != "lidar-cal-8" {
		t.Fatalf("policy compatibility used defaults: %#v", bundle)
	}
	if len(bundle.Entities) != 1 || !reflect.DeepEqual(bundle.Entities[0].Pose, []float64{4, 5, .8, 1, 0, 0, 0}) || !bundle.ObservedAt.Equal(snapshot.ObservedAt) {
		t.Fatalf("policy observation lost normalized geometry/time: %#v", bundle)
	}
	w.config.RobotModel = "xlerobot-dual-arm"
	if _, err := w.buildPolicyObservation(context.Background(), runtime.Command{CommandID: "cmd-2"}); err == nil {
		t.Fatal("configured model must not override the connected robot profile")
	}
}

func TestProfileJointStateRetainsDeclaredGenericAxesOnly(t *testing.T) {
	snapshot := contractSnapshot()
	snapshot.RobotProfile.Joints = []robotcontract.Joint{{Name: "axis_1", Kind: "revolute", Unit: "rad", Lower: -3, Upper: 3}, {Name: "axis_2", Kind: "prismatic", Unit: "m", Lower: 0, Upper: 1}, {Name: "missing_axis", Kind: "revolute", Unit: "rad", Lower: -3, Upper: 3}}
	snapshot.RobotState = map[string]any{"joint_positions": map[string]any{"axis_1": .4, "axis_2": math.Inf(1), "foreign_axis": .5, "Rotation_L": .6}}
	w := New(Config{RobotID: "mobile-7", Adapter: "generic_ros2", Observer: staticObserver{snapshot}})
	sample := w.sampleFromTelemetry(snapshot)
	if !reflect.DeepEqual(sample.State, map[string]float64{"joint.axis_1": .4}) {
		t.Fatalf("generic joint state=%#v", sample.State)
	}
	bundle, err := w.buildPolicyObservation(context.Background(), runtime.Command{CommandID: "cmd-joints"})
	if err != nil || !reflect.DeepEqual(bundle.RobotState, sample.State) || !bundle.Sources["proprioception"].Fresh {
		t.Fatalf("bundle=%#v error=%v", bundle, err)
	}
	snapshot.RobotState = map[string]any{"joints": map[string]float64{"axis_1": 0}}
	if got := w.sampleFromTelemetry(snapshot).State; !reflect.DeepEqual(got, map[string]float64{"joint.axis_1": 0}) {
		t.Fatalf("zero is a real measurement, missing axes are not: %#v", got)
	}
}

func TestPointsOnlyReconstructionReportsSourceHealthWithoutInventingEntities(t *testing.T) {
	snapshot := contractSnapshot()
	snapshot.Reconstruction.Entities = nil
	w := New(Config{RobotID: "mobile-7", Adapter: "generic_ros2"})
	events := w.ObservationsFromTelemetry(snapshot)
	if len(events) != 2 || events[1].Kind != observation.SourceHealth || events[1].SourceID != "mobile-7/lidar" {
		t.Fatalf("point-only source was omitted or fabricated entities: %#v", events)
	}
	if health := events[1].Payload.(observation.SourceHealthPayload); health.Status != "OK" {
		t.Fatalf("health=%#v", health)
	}
}

func TestReconstructionIdentityDistinguishesReusedOpaqueIDAtNewSequence(t *testing.T) {
	snapshot := contractSnapshot()
	w := New(Config{RobotID: "mobile-7", Adapter: "generic_ros2"})
	first := w.ObservationsFromTelemetry(snapshot)[1]
	snapshot.Reconstruction.Sequence = 43
	snapshot.Reconstruction.ObservationID = "intermediate-frame"
	_ = w.ObservationsFromTelemetry(snapshot)
	snapshot.Reconstruction.Sequence = 44
	snapshot.Reconstruction.ObservationID = "lidar-frame-42"
	snapshot.Reconstruction.Entities[0].Pose[0] = 5
	latest := w.ObservationsFromTelemetry(snapshot)[1]
	if first.ObservationID == latest.ObservationID {
		t.Fatal("a new source sequence must not be discarded as an old opaque frame ID")
	}
	if latest.Payload.(observation.EntityPayload).Pose[0] != 5 {
		t.Fatal("latest capture was not projected")
	}
}
