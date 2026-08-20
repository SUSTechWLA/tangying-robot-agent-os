// Package telemetry is the cloud Fleet telemetry sink. Each edge worker
// reports one low-rate sample per interval: world-frame pose, perception
// entities and a local occupancy grid. The sink keeps the latest sample and
// a bounded trajectory per robot so the console and the global fusion
// service can read them without touching the robot.
package telemetry

import (
	"context"
	"errors"
	"sync"
	"time"
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
}

// Store persists telemetry samples. Implementations must be safe for
// concurrent use.
type Store interface {
	Ingest(context.Context, Sample) error
	Latest(context.Context, string) (Sample, bool, error)
	Trajectory(context.Context, string, int) ([]Sample, error)
	Robots(context.Context) ([]string, error)
	// Frame returns the latest live scene frame for one robot.
	Frame(context.Context, string) (Frame, bool, error)
}

// Frame is one cached live scene frame.
type Frame struct {
	Data      []byte
	MediaType string
}

// MemoryStore keeps latest samples and bounded trajectories in process.
// It is the default for tests and the no-Redis profile.
type MemoryStore struct {
	mu          sync.RWMutex
	latest      map[string]Sample
	trajectory  map[string][]Sample
	trajectoryN int
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		latest:      map[string]Sample{},
		trajectory:  map[string][]Sample{},
		trajectoryN: 600,
	}
}

func (s *MemoryStore) Ingest(_ context.Context, sample Sample) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if sample.RobotID == "" {
		return errors.New("telemetry sample is missing robot id")
	}
	// The in-memory store keeps the frame inside the sample so Latest and
	// Frame stay consistent without a second key.
	s.latest[sample.RobotID] = sample
	trajectory := append(s.trajectory[sample.RobotID], sample)
	if len(trajectory) > s.trajectoryN {
		trajectory = trajectory[len(trajectory)-s.trajectoryN:]
	}
	s.trajectory[sample.RobotID] = trajectory
	return nil
}

func (s *MemoryStore) Latest(_ context.Context, robotID string) (Sample, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	sample, ok := s.latest[robotID]
	return sample, ok, nil
}

func (s *MemoryStore) Trajectory(_ context.Context, robotID string, limit int) ([]Sample, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	trajectory := s.trajectory[robotID]
	if limit > 0 && len(trajectory) > limit {
		trajectory = trajectory[len(trajectory)-limit:]
	}
	result := append([]Sample(nil), trajectory...)
	return result, nil
}

func (s *MemoryStore) Frame(_ context.Context, robotID string) (Frame, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	sample, ok := s.latest[robotID]
	if !ok || len(sample.Frame) == 0 {
		return Frame{}, false, nil
	}
	return Frame{Data: append([]byte(nil), sample.Frame...), MediaType: sample.FrameMediaType}, true, nil
}

func (s *MemoryStore) Robots(_ context.Context) ([]string, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := make([]string, 0, len(s.latest))
	for robotID := range s.latest {
		result = append(result, robotID)
	}
	return result, nil
}
