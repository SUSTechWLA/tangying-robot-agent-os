// Package fusion builds the multi-robot global map from per-robot telemetry
// samples: each robot reports its world-frame pose and a local occupancy
// grid; fusion transforms every local grid into the shared world frame and
// merges it into one global occupancy map, then attaches per-robot
// trajectories and deduplicated perception entities.
//
// The merge is intentionally a deterministic, stateless function so it can
// be unit tested and replayed: GlobalMap(samples) == GlobalMap(samples) for
// the same input order.
package fusion

import (
	"math"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
)

// DefaultCellSize is used when the caller does not choose one.
const DefaultCellSize = 0.1

// GlobalMap is the fused world view served to the console.
type GlobalMap struct {
	CellSize  float64      `json:"cellSizeM"`
	OriginX   float64      `json:"originX"`
	OriginY   float64      `json:"originY"`
	Width     int          `json:"width"`
	Height    int          `json:"height"`
	Cells     []byte       `json:"cells"`
	Robots    []RobotView  `json:"robots"`
	Entities  []EntityView `json:"entities"`
	UpdatedAt string       `json:"updatedAt"`
}

// RobotView is one robot's fused presence: current pose, trajectory and
// harness-visible semantics (held object, placement log).
type RobotView struct {
	RobotID    string            `json:"robotId"`
	Pose       []float64         `json:"pose"`
	Trajectory [][]float64       `json:"trajectory"`
	Activity   string            `json:"activity,omitempty"`
	Held       string            `json:"held,omitempty"`
	Placements map[string]string `json:"placements,omitempty"`
}

// EntityView is a deduplicated perception entity in world coordinates.
type EntityView struct {
	EntityID   string            `json:"entityId"`
	Category   string            `json:"category"`
	Attributes map[string]string `json:"attributes,omitempty"`
	Pose       []float64         `json:"pose"`
	Confidence float64           `json:"confidence"`
	Relation   string            `json:"relation,omitempty"`
}

type bounds struct {
	minX, minY, maxX, maxY float64
}

// Global fuses the given per-robot samples (each robot once, latest first)
// into a global occupancy map plus robot trajectories and entities.
func Global(samples map[string]telemetry.Sample, cellSize float64) GlobalMap {
	if cellSize <= 0 {
		cellSize = DefaultCellSize
	}
	world := computeBounds(samples)
	width := max(1, int(math.Ceil((world.maxX-world.minX)/cellSize)))
	height := max(1, int(math.Ceil((world.maxY-world.minY)/cellSize)))
	// Pad by one cell so local grids near the border are not clipped.
	width += 2
	height += 2
	originX := world.minX - cellSize
	originY := world.minY - cellSize
	cells := make([]byte, width*height)

	robots := make([]RobotView, 0, len(samples))
	entities := map[string]EntityView{}
	for robotID, sample := range samples {
		pose := pose2D(sample.Pose)
		robot := RobotView{
			RobotID:    robotID,
			Pose:       append([]float64(nil), pose...),
			Activity:   sample.Activity,
			Held:       sample.Held,
			Placements: sample.Placements,
			Trajectory: [][]float64{},
		}
		for _, entity := range sample.Entities {
			worldPose := append([]float64(nil), entity.Pose...)
			existing, seen := entities[entity.EntityID]
			if !seen || entity.Confidence > existing.Confidence {
				entities[entity.EntityID] = EntityView{
					EntityID:   entity.EntityID,
					Category:   entity.Category,
					Attributes: entity.Attributes,
					Pose:       worldPose,
					Confidence: entity.Confidence,
					Relation:   entity.Relation,
				}
			}
		}
		if sample.Occupancy != nil {
			mergeLocalGrid(cells, width, height, originX, originY, cellSize, sample.Occupancy, pose)
		}
		robots = append(robots, robot)
	}

	entityList := make([]EntityView, 0, len(entities))
	for _, entity := range entities {
		entityList = append(entityList, entity)
	}
	return GlobalMap{
		CellSize: cellSize, OriginX: originX, OriginY: originY,
		Width: width, Height: height, Cells: cells,
		Robots: robots, Entities: entityList,
	}
}

// WithTrajectories attaches per-robot trajectories (oldest first) to the map.
func WithTrajectories(base GlobalMap, trajectories map[string][]telemetry.Sample) GlobalMap {
	for index := range base.Robots {
		robotID := base.Robots[index].RobotID
		var points [][]float64
		for _, sample := range trajectories[robotID] {
			if len(sample.Pose) >= 2 {
				points = append(points, []float64{sample.Pose[0], sample.Pose[1]})
			}
		}
		base.Robots[index].Trajectory = points
	}
	return base
}

func computeBounds(samples map[string]telemetry.Sample) bounds {
	b := bounds{minX: math.Inf(1), minY: math.Inf(1), maxX: math.Inf(-1), maxY: math.Inf(-1)}
	have := false
	for _, sample := range samples {
		if len(sample.Pose) < 2 {
			continue
		}
		have = true
		x, y := sample.Pose[0], sample.Pose[1]
		b.minX = math.Min(b.minX, x)
		b.minY = math.Min(b.minY, y)
		b.maxX = math.Max(b.maxX, x)
		b.maxY = math.Max(b.maxY, y)
		if grid := sample.Occupancy; grid != nil {
			radius := math.Hypot(float64(grid.Width)*grid.CellSize, float64(grid.Height)*grid.CellSize) / 2
			b.minX = math.Min(b.minX, x-radius)
			b.minY = math.Min(b.minY, y-radius)
			b.maxX = math.Max(b.maxX, x+radius)
			b.maxY = math.Max(b.maxY, y+radius)
		}
	}
	if !have {
		return bounds{minX: -1, minY: -1, maxX: 1, maxY: 1}
	}
	return b
}

// mergeLocalGrid rasterizes one robot-local occupancy grid into the global
// cells with a max-merge (occupied wins over free; unknown keeps its value).
func mergeLocalGrid(cells []byte, width, height int, originX, originY, cellSize float64, grid *telemetry.OccupancyGrid, pose []float64) {
	if grid == nil || grid.Width <= 0 || grid.Height <= 0 || len(grid.Cells) < grid.Width*grid.Height {
		return
	}
	cosYaw, sinYaw := math.Cos(pose[3]), math.Sin(pose[3])
	for iy := 0; iy < grid.Height; iy++ {
		for ix := 0; ix < grid.Width; ix++ {
			value := grid.Cells[iy*grid.Width+ix]
			if value == 0 || value == 255 {
				continue
			}
			// Local cell center, then rotate into world frame.
			localX := grid.OriginX + (float64(ix)+0.5)*grid.CellSize
			localY := grid.OriginY + (float64(iy)+0.5)*grid.CellSize
			worldX := pose[0] + cosYaw*localX - sinYaw*localY
			worldY := pose[1] + sinYaw*localX + cosYaw*localY
			gx := int(math.Floor((worldX - originX) / cellSize))
			gy := int(math.Floor((worldY - originY) / cellSize))
			if gx < 0 || gy < 0 || gx >= width || gy >= height {
				continue
			}
			index := gy*width + gx
			if value > cells[index] {
				cells[index] = value
			}
		}
	}
}

func pose2D(pose []float64) []float64 {
	result := []float64{0, 0, 0, 0}
	if len(pose) >= 1 {
		result[0] = pose[0]
	}
	if len(pose) >= 2 {
		result[1] = pose[1]
	}
	if len(pose) >= 3 {
		result[2] = pose[2]
	}
	if len(pose) >= 4 {
		result[3] = pose[3]
	}
	return result
}
