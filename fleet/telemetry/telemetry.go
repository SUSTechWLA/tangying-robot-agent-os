// Package telemetry is the cloud Fleet telemetry sink. Each edge worker
// reports one low-rate sample per interval: world-frame pose, perception
// entities and a local occupancy grid. The sink keeps the latest sample and
// a bounded trajectory per robot so the console and the global fusion
// service can read them without touching the robot.
package telemetry

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
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
	Capture          *sensors.Capture   `json:"capture,omitempty"`
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
	LatestCapture(context.Context, string) (sensors.Capture, bool, error)
	Capture(context.Context, string) (sensors.Capture, bool, error)
	CorrelateCapture(context.Context, string, uint64) error
}

// Frame is one cached live scene frame.
type Frame struct {
	Data      []byte
	MediaType string
}

// MemoryStore keeps latest samples and bounded trajectories in process.
// It is the default for tests and the no-Redis profile.
type MemoryStore struct {
	mu                 sync.RWMutex
	latest             map[string]Sample
	trajectory         map[string][]Sample
	trajectoryN        int
	captures           map[string]*sensors.Capture
	captureIDs         map[string][]string
	latestCaptureID    map[string]string
	pendingCorrelation map[string]uint64
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		latest:             map[string]Sample{},
		trajectory:         map[string][]Sample{},
		trajectoryN:        600,
		captures:           map[string]*sensors.Capture{},
		captureIDs:         map[string][]string{},
		latestCaptureID:    map[string]string{},
		pendingCorrelation: map[string]uint64{},
	}
}

func (s *MemoryStore) Ingest(_ context.Context, sample Sample) error {
	var capture *sensors.Capture
	if sample.Capture != nil {
		capture = sample.Capture.Clone()
		if err := capture.Validate(); err != nil {
			return fmt.Errorf("invalid sensor capture: %w", err)
		}
		if capture.RobotID != sample.RobotID {
			return fmt.Errorf("sensor capture robot %q does not match sample robot %q", capture.RobotID, sample.RobotID)
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if sample.RobotID == "" {
		return errors.New("telemetry sample is missing robot id")
	}
	if capture != nil {
		if existing := s.captures[capture.CaptureID]; existing != nil && existing.RobotID != capture.RobotID {
			return fmt.Errorf("sensor capture id %q is already owned by robot %q", capture.CaptureID, existing.RobotID)
		}
		s.ingestCapture(capture)
		if latest := s.captures[s.latestCaptureID[sample.RobotID]]; latest != nil {
			sample.Capture = latest.Clone()
		} else {
			return errors.New("sensor capture was not retained for the sample robot")
		}
	}
	sample.Frame = append([]byte(nil), sample.Frame...)
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
	return cloneSample(sample), ok, nil
}

func (s *MemoryStore) Trajectory(_ context.Context, robotID string, limit int) ([]Sample, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	trajectory := s.trajectory[robotID]
	if limit > 0 && len(trajectory) > limit {
		trajectory = trajectory[len(trajectory)-limit:]
	}
	result := make([]Sample, len(trajectory))
	for index, sample := range trajectory {
		result[index] = cloneSample(sample)
	}
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

const captureRetention = 64

func (s *MemoryStore) ingestCapture(capture *sensors.Capture) {
	if _, duplicate := s.captures[capture.CaptureID]; duplicate {
		return
	}
	if revision := s.pendingCorrelation[capture.CaptureID]; revision > capture.WorldRevision {
		capture.WorldRevision = revision
	}
	delete(s.pendingCorrelation, capture.CaptureID)
	s.captures[capture.CaptureID] = capture.Clone()
	robotID := capture.RobotID
	s.captureIDs[robotID] = append(s.captureIDs[robotID], capture.CaptureID)
	latest := s.captures[s.latestCaptureID[robotID]]
	if latest == nil || capture.SourceSequence > latest.SourceSequence {
		s.latestCaptureID[robotID] = capture.CaptureID
	}
	for len(s.captureIDs[robotID]) > captureRetention {
		oldestIndex := 0
		oldestSequence := uint64(^uint64(0))
		for index, captureID := range s.captureIDs[robotID] {
			if candidate := s.captures[captureID]; candidate != nil && candidate.SourceSequence < oldestSequence {
				oldestIndex = index
				oldestSequence = candidate.SourceSequence
			}
		}
		oldestID := s.captureIDs[robotID][oldestIndex]
		delete(s.captures, oldestID)
		s.captureIDs[robotID] = append(s.captureIDs[robotID][:oldestIndex], s.captureIDs[robotID][oldestIndex+1:]...)
	}
}

func (s *MemoryStore) LatestCapture(_ context.Context, robotID string) (sensors.Capture, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	capture := s.captures[s.latestCaptureID[robotID]]
	if capture == nil {
		return sensors.Capture{}, false, nil
	}
	return *capture.Clone(), true, nil
}

func (s *MemoryStore) Capture(_ context.Context, captureID string) (sensors.Capture, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	capture := s.captures[captureID]
	if capture == nil {
		return sensors.Capture{}, false, nil
	}
	return *capture.Clone(), true, nil
}

func (s *MemoryStore) CorrelateCapture(_ context.Context, captureID string, worldRevision uint64) error {
	if captureID == "" || worldRevision == 0 {
		return errors.New("capture id and positive world revision are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if capture := s.captures[captureID]; capture != nil {
		if worldRevision > capture.WorldRevision {
			capture.WorldRevision = worldRevision
		}
		return nil
	}
	if worldRevision > s.pendingCorrelation[captureID] {
		s.pendingCorrelation[captureID] = worldRevision
	}
	return nil
}

func cloneSample(sample Sample) Sample {
	sample.Frame = append([]byte(nil), sample.Frame...)
	sample.Capture = sample.Capture.Clone()
	return sample
}
