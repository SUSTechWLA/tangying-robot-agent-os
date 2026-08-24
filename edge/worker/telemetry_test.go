package worker

import (
	"math"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestPoseFromRobotStateBasePose(t *testing.T) {
	// base_pose: [x, y, z, qw, qx, qy, qz] with yaw 90 degrees.
	pose := poseFromRobotState(map[string]any{
		"base_pose": []any{1.0, 2.0, 0.035, math.Cos(math.Pi / 4), 0.0, 0.0, math.Sin(math.Pi / 4)},
	})
	if len(pose) != 4 {
		t.Fatalf("pose = %v", pose)
	}
	if pose[0] != 1.0 || pose[1] != 2.0 || pose[2] != 0.035 {
		t.Fatalf("pose xyz = %v", pose)
	}
	if math.Abs(pose[3]-math.Pi/2) > 1e-6 {
		t.Fatalf("yaw = %v, want pi/2", pose[3])
	}
}

func TestPoseFromRobotStateMissingFallsBackToZero(t *testing.T) {
	pose := poseFromRobotState(map[string]any{})
	if pose[0] != 0 || pose[3] != 0 {
		t.Fatalf("fallback pose = %v", pose)
	}
}

func TestRasterizeMarksObjectsAroundRobot(t *testing.T) {
	entities := []telemetry.Entity{
		{EntityID: "red-cup", Category: "cup", Pose: []float64{0.3, 0.4, 0.8}},
		{EntityID: "blue-bottle", Category: "bottle", Pose: []float64{-0.25, 0.5, 0.82}},
		{EntityID: "xlerobot", Category: "robot", Pose: []float64{0, 0, 0}},
		{EntityID: "floor", Category: "environment", Pose: []float64{0, 0, 0}},
	}
	grid := Rasterize(entities, []float64{0, 0, 0, 0}, 15, 0.1)
	if grid.Width != 15 || grid.Height != 15 || len(grid.Cells) != 225 {
		t.Fatalf("grid = %dx%d", grid.Width, grid.Height)
	}
	occupied := 0
	for _, cell := range grid.Cells {
		if cell == 100 {
			occupied++
		}
	}
	// Two objects * 3x3 blobs = 18 cells (robot and floor excluded).
	if occupied != 18 {
		t.Fatalf("occupied = %d, want 18", occupied)
	}
}

func TestRasterizeRotatesWithRobotHeading(t *testing.T) {
	// Robot faces +Y; an object at world (0.2, 0) is on the robot's left
	// (local -X) and must land in cells with ix < center.
	entities := []telemetry.Entity{
		{EntityID: "red-cup", Category: "cup", Pose: []float64{0.2, 0.0, 0.8}},
	}
	grid := Rasterize(entities, []float64{0, 0, 0, math.Pi / 2}, 15, 0.1)
	center := 7
	found := false
	for iy := 0; iy < grid.Height; iy++ {
		for ix := 0; ix < grid.Width; ix++ {
			if grid.Cells[iy*grid.Width+ix] == 100 && ix < center {
				found = true
			}
		}
	}
	if !found {
		t.Fatal("object rotated into the expected local cells")
	}
}

func TestAddWorldOffset(t *testing.T) {
	pose := addWorldOffset([]float64{0, 0, 0, 0}, []float64{2, 0, 0, 0})
	if pose[0] != 2 || pose[3] != 0 {
		t.Fatalf("offset pose = %v", pose)
	}
	if offset := addWorldOffset([]float64{0, 0, 0, 0}, nil); offset[0] != 0 {
		t.Fatalf("nil offset must be a no-op: %v", offset)
	}
}

func TestSampleActivityReflectsTheWorkersInFlightCommand(t *testing.T) {
	worker := New(Config{RobotID: "robot-1", Adapter: "robocasa"})
	worker.setCurrent("command-1")

	executing := worker.sampleFromTelemetry(telemetry.Snapshot{Activity: "IDLE"})
	if executing.Activity != "EXECUTING" {
		t.Fatalf("activity = %q, want EXECUTING", executing.Activity)
	}

	emergencyStopped := worker.sampleFromTelemetry(telemetry.Snapshot{Activity: "EMERGENCY_STOPPED"})
	if emergencyStopped.Activity != "EMERGENCY_STOPPED" {
		t.Fatalf("emergency activity = %q", emergencyStopped.Activity)
	}

	worker.clearCurrent("command-1")
	idle := worker.sampleFromTelemetry(telemetry.Snapshot{Activity: "IDLE"})
	if idle.Activity != "IDLE" {
		t.Fatalf("activity after completion = %q, want IDLE", idle.Activity)
	}
}

func TestSampleWorldTransformKeepsRobotAndEntitiesInOneFrame(t *testing.T) {
	worker := New(Config{RobotID: "robot-2", Adapter: "real", WorldPose: []float64{10, 20, 1, math.Pi / 2}})
	sample := worker.sampleFromTelemetry(telemetry.Snapshot{
		RobotState: map[string]any{
			"base_pose": []any{1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0},
		},
		Entities: []telemetry.Entity{{
			EntityID: "red-block", Category: "block", Pose: []float64{1, 1, 0.8, 1, 0, 0, 0},
		}},
	})

	wantRobot := []float64{10, 21, 1, math.Pi / 2}
	for index, want := range wantRobot {
		if math.Abs(sample.Pose[index]-want) > 1e-9 {
			t.Fatalf("robot world pose[%d] = %v, want %v", index, sample.Pose[index], want)
		}
	}
	wantEntity := []float64{9, 21, 1.8}
	for index, want := range wantEntity {
		if math.Abs(sample.Entities[0].Pose[index]-want) > 1e-9 {
			t.Fatalf("entity world pose[%d] = %v, want %v", index, sample.Entities[0].Pose[index], want)
		}
	}
}

func TestNumericRobotStateKeepsScalarsOnly(t *testing.T) {
	state := numericRobotState(map[string]any{
		"reward":                  1.5,
		"pick_count":              2,
		"verification_confidence": 0.95,
		"base_pose":               []any{0.0, 0.0, 0.0},
		"grippers":                map[string]any{"left": "open"},
	}, "robot-1")
	if state["reward"] != 1.5 || state["pick_count"] != 2 || state["verification_confidence"] != 0.95 {
		t.Fatalf("numeric state = %v", state)
	}
	if _, ok := state["base_pose"]; ok {
		t.Fatal("non-scalar state must be excluded")
	}
}

func TestNumericRobotStateCanonicalizesJoints(t *testing.T) {
	got := numericRobotState(map[string]any{"joint_positions": map[string]any{
		"robot-1__Rotation_L":     0.25,
		"robot-1__Jaw_R":          0.8,
		"robot-1__head_pan_joint": -0.1,
		"robot-2__Pitch_L":        0.5,
		"robot-1__Elbow_L":        math.Inf(1),
	}}, "robot-1")
	if got["joint.left.rotation"] != 0.25 || got["joint.right.jaw"] != 0.8 || got["joint.head.pan"] != -0.1 {
		t.Fatalf("canonical joints = %#v", got)
	}
	if _, ok := got["joint.left.pitch"]; ok {
		t.Fatalf("foreign robot joint was retained: %#v", got)
	}
	if _, ok := got["joint.left.elbow"]; ok {
		t.Fatalf("non-finite joint was retained: %#v", got)
	}

	floatMap := numericRobotState(map[string]any{"joint_positions": map[string]float64{
		"Rotation_R": 0.4,
	}}, "robot-1")
	if floatMap["joint.right.rotation"] != 0.4 {
		t.Fatalf("map[string]float64 joints = %#v", floatMap)
	}
}
