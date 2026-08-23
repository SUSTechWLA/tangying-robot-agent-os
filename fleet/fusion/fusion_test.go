package fusion

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
)

func sample(robotID string, pose []float64, grid *telemetry.OccupancyGrid) telemetry.Sample {
	return telemetry.Sample{RobotID: robotID, ObservedAt: time.Now().UTC(), Pose: pose, Occupancy: grid}
}

func localGrid(cells int, cellSize float64, occupied ...int) *telemetry.OccupancyGrid {
	grid := &telemetry.OccupancyGrid{
		Width: cells, Height: cells, CellSize: cellSize,
		OriginX: -float64(cells) * cellSize / 2, OriginY: -float64(cells) * cellSize / 2,
		Cells: make([]byte, cells*cells),
	}
	for _, index := range occupied {
		if index >= 0 && index < len(grid.Cells) {
			grid.Cells[index] = 100
		}
	}
	return grid
}

func TestGlobalFusesTwoRobotsIntoOneGrid(t *testing.T) {
	samples := map[string]telemetry.Sample{
		"robot-1": sample("robot-1", []float64{0, 0, 0, 0}, localGrid(11, 0.1, 5*11+5)),
		"robot-2": sample("robot-2", []float64{3, 0, 0, 0}, localGrid(11, 0.1, 5*11+5)),
	}
	global := Global(samples, 0.1)
	if len(global.Robots) != 2 {
		t.Fatalf("robots = %d, want 2", len(global.Robots))
	}
	// Grid spans from ~-0.8 to ~3.8 (two robots, padded by one cell).
	if global.Width < 44 || global.Width > 52 {
		t.Fatalf("grid width = %d, want ~48", global.Width)
	}
	occupied := 0
	for _, cell := range global.Cells {
		if cell > 0 {
			occupied++
		}
	}
	if occupied < 2 {
		t.Fatalf("occupied cells = %d, want >= 2 (both robots contributed)", occupied)
	}
}

func TestGlobalRotatedGridMergesInWorldFrame(t *testing.T) {
	// robot at (1, 1) facing +Y (yaw=pi/2). Its local cell (ix=4, iy=2) is
	// local (0.4, 0.0), which rotates into world offset (0.0, 0.4): the
	// occupied cells must land around world (1.0, 1.4).
	samples := map[string]telemetry.Sample{
		"robot-1": sample("robot-1", []float64{1, 1, 0, 1.5708}, localGrid(5, 0.2, 2*5+4)),
	}
	global := Global(samples, 0.1)
	found := false
	for iy := 0; iy < global.Height; iy++ {
		for ix := 0; ix < global.Width; ix++ {
			if global.Cells[iy*global.Width+ix] > 0 {
				worldX := global.OriginX + (float64(ix)+0.5)*global.CellSize
				worldY := global.OriginY + (float64(iy)+0.5)*global.CellSize
				if worldX > 0.9 && worldX < 1.15 && worldY > 1.3 && worldY < 1.55 {
					found = true
				}
			}
		}
	}
	if !found {
		t.Fatal("rotated local grid did not land in the expected world cells")
	}
}

func TestGlobalDeduplicatesEntitiesByConfidence(t *testing.T) {
	samples := map[string]telemetry.Sample{
		"robot-1": {
			RobotID: "robot-1", Pose: []float64{0, 0, 0, 0},
			Entities: []telemetry.Entity{{EntityID: "red-cup", Category: "cup", Pose: []float64{0.3, 0.5, 0.8}, Confidence: 0.7}},
		},
		"robot-2": {
			RobotID: "robot-2", Pose: []float64{0.05, 0.05, 0, 0},
			Entities: []telemetry.Entity{{EntityID: "red-cup", Category: "cup", Pose: []float64{0.25, 0.45, 0.8}, Confidence: 0.9}},
		},
	}
	global := Global(samples, 0.1)
	if len(global.Entities) != 1 {
		t.Fatalf("entities = %d, want 1 (deduplicated)", len(global.Entities))
	}
	if global.Entities[0].Confidence != 0.9 {
		t.Fatalf("kept confidence = %v, want 0.9 (highest wins)", global.Entities[0].Confidence)
	}
}

func TestGlobalDoesNotTransformAlreadyWorldFrameEntityPosesTwice(t *testing.T) {
	samples := map[string]telemetry.Sample{
		"robot-2": {
			RobotID: "robot-2", Pose: []float64{1, -0.1, 0.03, 1.5708},
			Entities: []telemetry.Entity{{
				EntityID: "red-block", Category: "block",
				Pose: []float64{0.96, 0.34, 0.81}, Confidence: 0.98,
			}},
		},
	}

	global := Global(samples, 0.1)
	if len(global.Entities) != 1 {
		t.Fatalf("entities = %d, want 1", len(global.Entities))
	}
	for index, want := range []float64{0.96, 0.34, 0.81} {
		if got := global.Entities[0].Pose[index]; got != want {
			t.Fatalf("world pose[%d] = %v, want %v (pose was transformed twice)", index, got, want)
		}
	}
}

func TestWithTrajectoriesAttachesPolylines(t *testing.T) {
	samples := map[string]telemetry.Sample{
		"robot-1": sample("robot-1", []float64{0, 0, 0, 0}, nil),
	}
	trajectories := map[string][]telemetry.Sample{
		"robot-1": {
			sample("robot-1", []float64{0, 0, 0, 0}, nil),
			sample("robot-1", []float64{0.1, 0.2, 0, 0}, nil),
			sample("robot-1", []float64{0.2, 0.4, 0, 0}, nil),
		},
	}
	global := WithTrajectories(Global(samples, 0.1), trajectories)
	if len(global.Robots) != 1 || len(global.Robots[0].Trajectory) != 3 {
		t.Fatalf("trajectory = %v, want 3 points", global.Robots[0].Trajectory)
	}
	if global.Robots[0].Trajectory[2][1] != 0.4 {
		t.Fatalf("trajectory order wrong: %v", global.Robots[0].Trajectory)
	}
}
