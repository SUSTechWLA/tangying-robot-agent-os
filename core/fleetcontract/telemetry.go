package fleetcontract

import (
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
)

// Entity is one perception entity as reported by a robot.
type Entity struct {
	EntityID   string            `json:"entityId"`
	Category   string            `json:"category"`
	Attributes map[string]string `json:"attributes,omitempty"`
	Pose       []float64         `json:"pose,omitempty"`
	Confidence float64           `json:"confidence"`
	Relation   string            `json:"relation,omitempty"`
}

// OccupancyGrid is a local occupancy map in the robot frame.
type OccupancyGrid struct {
	Width    int     `json:"width"`
	Height   int     `json:"height"`
	CellSize float64 `json:"cellSizeM"`
	OriginX  float64 `json:"originX"`
	OriginY  float64 `json:"originY"`
	// Row-major cell values: 0 free, 100 occupied, 255 unknown.
	Cells []byte `json:"cells"`
}

// Sample is one telemetry report from one robot.
type Sample struct {
	RobotID          string             `json:"robotId"`
	Adapter          string             `json:"adapter,omitempty"`
	ObservedAt       time.Time          `json:"observedAt"`
	Pose             []float64          `json:"pose,omitempty"` // world [x, y, z, yaw]
	Activity         string             `json:"activity,omitempty"`
	EmergencyStopped bool               `json:"emergencyStopped"`
	Anomalies        []string           `json:"anomalies,omitempty"`
	Entities         []Entity           `json:"entities,omitempty"`
	Occupancy        *OccupancyGrid     `json:"occupancy,omitempty"`
	State            map[string]float64 `json:"state,omitempty"`
	// Held is the entity currently grasped by the robot (harness feedback).
	Held string `json:"held,omitempty"`
	// Placements is the object -> destination log (harness feedback).
	Placements map[string]string `json:"placements,omitempty"`
	// Frame is the live scene frame rendered by the Robot Runtime. JSON
	// transport carries it as base64 automatically (encoding/json); the
	// gRPC Link path carries the raw bytes; /v1/scene/frames serves the
	// raw bytes for the god-view console.
	Frame          []byte `json:"frame,omitempty"`
	FrameMediaType string `json:"frameMediaType,omitempty"`
	// Robot contracts are consumed by the local world projector. Raw geometry
	// stays off the public low-rate telemetry transport.
	Reconstruction *robotcontract.Reconstruction `json:"-"`
	RobotProfile   *robotcontract.Profile        `json:"-"`
	// Faults is small and is exactly what a fleet operator needs to see, so it
	// travels on the ordinary telemetry transport as well.
	Faults *robotcontract.FaultReport `json:"faults,omitempty"`
}
