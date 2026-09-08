// Package telemetry defines the user-observable, low-rate snapshot shared by
// the Robot Runtime, Local Agent and cloud console. It contains semantic and
// sensor-derived data only; raw camera/LiDAR streams remain on the robot.
package telemetry

import (
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"time"
)

type Entity struct {
	EntityID   string            `json:"entityId"`
	Category   string            `json:"category"`
	Attributes map[string]string `json:"attributes,omitempty"`
	Pose       []float64         `json:"pose,omitempty"`
	Confidence float64           `json:"confidence"`
	Relation   string            `json:"relation,omitempty"`
}

type Snapshot struct {
	RobotProfile     *robotcontract.Profile        `json:"robotProfile,omitempty"`
	Reconstruction   *robotcontract.Reconstruction `json:"reconstruction,omitempty"`
	SchemaVersion    string                        `json:"schemaVersion"`
	ObservedAt       time.Time                     `json:"observedAt"`
	TaskID           string                        `json:"taskId,omitempty"`
	TaskRevision     uint64                        `json:"taskRevision,omitempty"`
	StepID           string                        `json:"stepId,omitempty"`
	Adapter          string                        `json:"adapter"`
	RobotID          string                        `json:"robotId"`
	SoftwareVersion  string                        `json:"softwareVersion,omitempty"`
	Activity         string                        `json:"activity"`
	Mode             string                        `json:"mode,omitempty"`
	EmergencyStopped bool                          `json:"emergencyStopped"`
	Anomalies        []string                      `json:"anomalies,omitempty"`
	LastError        string                        `json:"lastError,omitempty"`
	Entities         []Entity                      `json:"entities,omitempty"`
	RobotState       map[string]any                `json:"robotState,omitempty"`
	// Frame is cached separately from JSON telemetry so low-rate API responses
	// remain small. Callers must treat the bytes as immutable.
	Frame               []byte `json:"-"`
	FrameMediaType      string `json:"-"`
	DepthFrame          []byte `json:"-"`
	DepthFrameMediaType string `json:"-"`
	ColorFrameAvailable bool   `json:"colorFrameAvailable"`
	DepthFrameAvailable bool   `json:"depthFrameAvailable"`
}
