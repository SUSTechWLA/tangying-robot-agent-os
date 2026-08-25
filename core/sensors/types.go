// Package sensors defines transport-neutral, synchronized sensor evidence.
package sensors

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"
)

const (
	SchemaVersionV1 = "sensor.capture.v1"
	ModalityRGB     = "rgb"
	ModalityDepth   = "depth"

	AnomalyInvalidCapture = "INVALID_SENSOR_CAPTURE"
)

// Frame contains one calibrated image plane from a synchronized capture.
// Data is carried out-of-band from JSON APIs, but is preserved by the robot,
// edge and Fleet transports.
type Frame struct {
	SensorID      string    `json:"sensorId"`
	Modality      string    `json:"modality"`
	MediaType     string    `json:"mediaType"`
	Width         int       `json:"width"`
	Height        int       `json:"height"`
	SHA256        string    `json:"sha256"`
	URI           string    `json:"uri,omitempty"`
	DepthScaleM   float64   `json:"depthScaleM,omitempty"`
	MinRangeM     float64   `json:"minRangeM,omitempty"`
	MaxRangeM     float64   `json:"maxRangeM,omitempty"`
	Intrinsics    []float64 `json:"intrinsics"`
	CameraToWorld []float64 `json:"cameraToWorld"`
	Data          []byte    `json:"-"`
}

// Capture binds RGB-D evidence to one robot, episode and world revision.
type Capture struct {
	SchemaVersion     string    `json:"schemaVersion"`
	CaptureID         string    `json:"captureId"`
	RobotID           string    `json:"robotId"`
	EpisodeID         string    `json:"episodeId"`
	SimulationStep    uint64    `json:"simulationStep"`
	SourceSequence    uint64    `json:"sourceSequence"`
	CapturedAt        time.Time `json:"capturedAt"`
	FrameID           string    `json:"frameId"`
	TransformRevision string    `json:"transformRevision"`
	WorldRevision     uint64    `json:"worldRevision"`
	Frames            []Frame   `json:"frames"`
}

// Validate ensures consumers never combine evidence from different frames or
// accept corrupt image bytes as Harness input.
func (c *Capture) Validate() error {
	if c == nil {
		return errors.New("sensor capture is nil")
	}
	if c.SchemaVersion != SchemaVersionV1 {
		return fmt.Errorf("unsupported sensor capture schema %q", c.SchemaVersion)
	}
	if c.CaptureID == "" || c.RobotID == "" || c.EpisodeID == "" || c.FrameID == "" || c.TransformRevision == "" {
		return errors.New("sensor capture identity is incomplete")
	}
	if c.SourceSequence == 0 {
		return errors.New("sensor capture source sequence must be positive")
	}
	if c.CapturedAt.IsZero() {
		return errors.New("sensor capture timestamp is required")
	}

	modalities := make(map[string]Frame, len(c.Frames))
	for index, frame := range c.Frames {
		if err := validateFrame(frame); err != nil {
			return fmt.Errorf("sensor frame %d: %w", index, err)
		}
		if _, duplicate := modalities[frame.Modality]; duplicate {
			return fmt.Errorf("sensor modality %q is duplicated", frame.Modality)
		}
		modalities[frame.Modality] = frame
	}
	rgb, hasRGB := modalities[ModalityRGB]
	depth, hasDepth := modalities[ModalityDepth]
	if !hasRGB || !hasDepth {
		return errors.New("sensor capture requires synchronized rgb and depth frames")
	}
	if rgb.Width != depth.Width || rgb.Height != depth.Height {
		return errors.New("rgb and depth dimensions do not match")
	}
	return nil
}

func validateFrame(frame Frame) error {
	if frame.SensorID == "" || frame.Modality == "" || frame.MediaType == "" {
		return errors.New("identity, modality and media type are required")
	}
	if frame.Width <= 0 || frame.Height <= 0 {
		return errors.New("dimensions must be positive")
	}
	if len(frame.Data) == 0 {
		return errors.New("transported frame bytes are required")
	}
	if len(frame.SHA256) != sha256.Size*2 || frame.SHA256 != strings.ToLower(frame.SHA256) {
		return errors.New("sha256 must be 64 lowercase hexadecimal characters")
	}
	decoded, err := hex.DecodeString(frame.SHA256)
	if err != nil {
		return errors.New("sha256 must be hexadecimal")
	}
	digest := sha256.Sum256(frame.Data)
	if !equalBytes(decoded, digest[:]) {
		return errors.New("sha256 does not match frame bytes")
	}
	if len(frame.Intrinsics) != 9 || !finite(frame.Intrinsics) {
		return errors.New("intrinsics must be a finite 3x3 matrix")
	}
	if len(frame.CameraToWorld) != 16 || !finite(frame.CameraToWorld) {
		return errors.New("cameraToWorld must be a finite 4x4 matrix")
	}
	if frame.Modality == ModalityDepth {
		if !finite([]float64{frame.DepthScaleM, frame.MinRangeM, frame.MaxRangeM}) ||
			frame.DepthScaleM <= 0 || frame.MinRangeM < 0 || frame.MaxRangeM <= frame.MinRangeM {
			return errors.New("depth calibration range is invalid")
		}
	}
	return nil
}

func finite(values []float64) bool {
	for _, value := range values {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			return false
		}
	}
	return true
}

func equalBytes(left, right []byte) bool {
	if len(left) != len(right) {
		return false
	}
	var difference byte
	for index := range left {
		difference |= left[index] ^ right[index]
	}
	return difference == 0
}

// Clone returns independent storage suitable for crossing transport and store
// ownership boundaries.
func (c *Capture) Clone() *Capture {
	if c == nil {
		return nil
	}
	clone := *c
	clone.Frames = make([]Frame, len(c.Frames))
	for index, frame := range c.Frames {
		clone.Frames[index] = frame
		clone.Frames[index].Intrinsics = append([]float64(nil), frame.Intrinsics...)
		clone.Frames[index].CameraToWorld = append([]float64(nil), frame.CameraToWorld...)
		clone.Frames[index].Data = append([]byte(nil), frame.Data...)
	}
	return &clone
}
