package tasks

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestTelemetrySnapshotsCannotMutateCachedReconstructionOrRobotState(t *testing.T) {
	hub := NewTelemetryHub()
	sample := telemetry.Snapshot{Adapter: "mujoco", Entities: []telemetry.Entity{{EntityID: "cup", Attributes: map[string]string{"color": "red"}, Pose: []float64{1, 2, 3}}}, RobotState: map[string]any{"joints": map[string]any{"arm": 1.0}}, Reconstruction: &robotcontract.Reconstruction{Points: [][]float64{{1, 2, 3}}}, RobotProfile: &robotcontract.Profile{Tools: []string{"observe_scene"}}}
	hub.Publish(sample)
	sample.Entities[0].Pose[0] = 999
	sample.Entities[0].Attributes["color"] = "blue"
	sample.Reconstruction.Points[0][0] = 999
	sample.RobotProfile.Tools[0] = "forged"
	sample.RobotState["joints"].(map[string]any)["arm"] = 999.0
	latest, _ := hub.Latest("mujoco")
	if latest.Entities[0].Pose[0] != 1 || latest.Entities[0].Attributes["color"] != "red" || latest.Reconstruction.Points[0][0] != 1 || latest.RobotProfile.Tools[0] != "observe_scene" || latest.RobotState["joints"].(map[string]any)["arm"] != 1.0 {
		t.Fatal("snapshot aliases publisher memory")
	}
	latest.Reconstruction.Points[0][0] = 777
	history := hub.History("mujoco", 1)
	if history[0].Reconstruction.Points[0][0] != 1 {
		t.Fatal("latest aliases history")
	}
	history[0].Entities[0].Attributes["color"] = "green"
	again, _ := hub.Latest("mujoco")
	if again.Entities[0].Attributes["color"] != "red" {
		t.Fatal("history aliases cache")
	}
}

func TestTelemetryOwnsPointColorsAcrossPublisherLatestAndHistory(t *testing.T) {
	hub := NewTelemetryHub()
	sample := telemetry.Snapshot{Adapter: "rgbd", Reconstruction: &robotcontract.Reconstruction{
		Points: [][]float64{{1, 2, 3}}, PointColors: [][]int{{255, 0, 128}},
	}}
	hub.Publish(sample)
	sample.Reconstruction.PointColors[0][0] = 17
	latest, ok := hub.Latest("rgbd")
	if !ok || latest.Reconstruction.PointColors[0][0] != 255 {
		t.Fatal("publisher changed cached RGB point color")
	}
	latest.Reconstruction.PointColors[0][1] = 99
	history := hub.History("rgbd", 1)
	if len(history) != 1 || history[0].Reconstruction.PointColors[0][1] != 0 {
		t.Fatal("latest changed RGB point color in history")
	}
	history[0].Reconstruction.PointColors[0][2] = 11
	again, _ := hub.Latest("rgbd")
	if again.Reconstruction.PointColors[0][2] != 128 {
		t.Fatal("history changed cached RGB point color")
	}
}
