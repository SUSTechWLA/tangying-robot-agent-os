package sensors

import (
	"crypto/sha256"
	"encoding/hex"
	"math"
	"strings"
	"testing"
	"time"
)

func TestCaptureValidateAcceptsSynchronizedRGBD(t *testing.T) {
	capture := validCapture()
	if err := capture.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestCaptureValidateAllowsInitialSimulationStepBeforeCloudCorrelation(t *testing.T) {
	capture := validCapture()
	capture.SimulationStep = 0
	capture.WorldRevision = 0
	if err := capture.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestCaptureValidateRejectsInvalidEvidence(t *testing.T) {
	tests := map[string]func(*Capture){
		"schema":          func(c *Capture) { c.SchemaVersion = "sensor.capture.v0" },
		"identity":        func(c *Capture) { c.EpisodeID = "" },
		"source sequence": func(c *Capture) { c.SourceSequence = 0 },
		"captured at":     func(c *Capture) { c.CapturedAt = time.Time{} },
		"duplicate":       func(c *Capture) { c.Frames[1].Modality = ModalityRGB },
		"missing bytes":   func(c *Capture) { c.Frames[0].Data = nil },
		"uppercase hash":  func(c *Capture) { c.Frames[0].SHA256 = strings.ToUpper(c.Frames[0].SHA256) },
		"wrong hash":      func(c *Capture) { c.Frames[0].SHA256 = strings.Repeat("0", 64) },
		"dimension skew":  func(c *Capture) { c.Frames[1].Width++ },
		"bad intrinsics":  func(c *Capture) { c.Frames[0].Intrinsics[0] = math.Inf(1) },
		"bad transform":   func(c *Capture) { c.Frames[1].CameraToWorld[0] = math.NaN() },
		"bad depth scale": func(c *Capture) { c.Frames[1].DepthScaleM = 0 },
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			capture := validCapture()
			mutate(capture)
			if err := capture.Validate(); err == nil {
				t.Fatal("Validate() error = nil, want invalid capture")
			}
		})
	}
}

func TestCaptureCloneDeepCopiesFrames(t *testing.T) {
	original := validCapture()
	clone := original.Clone()
	clone.Frames[0].Data[0] = 99
	clone.Frames[0].Intrinsics[0] = 99

	if original.Frames[0].Data[0] == 99 || original.Frames[0].Intrinsics[0] == 99 {
		t.Fatal("Clone() shared mutable frame storage")
	}
}

func validCapture() *Capture {
	rgb := []byte("rgb-frame")
	depth := []byte("depth-frame")
	return &Capture{
		SchemaVersion:     SchemaVersionV1,
		CaptureID:         "capture-17",
		RobotID:           "robot1",
		EpisodeID:         "episode-9",
		SimulationStep:    42,
		SourceSequence:    17,
		CapturedAt:        time.Unix(1_700_000_000, 0).UTC(),
		FrameID:           "robot1/rgbd_head",
		TransformRevision: "transform-3",
		WorldRevision:     8,
		Frames: []Frame{
			{
				SensorID: "robot1/rgbd_head", Modality: ModalityRGB, MediaType: "image/jpeg",
				Width: 64, Height: 48, SHA256: digest(rgb), Data: rgb,
				Intrinsics: identity3(), CameraToWorld: identity4(),
			},
			{
				SensorID: "robot1/rgbd_head", Modality: ModalityDepth, MediaType: "application/x-depth-f32",
				Width: 64, Height: 48, SHA256: digest(depth), Data: depth,
				DepthScaleM: 1, MinRangeM: 0.05, MaxRangeM: 5,
				Intrinsics: identity3(), CameraToWorld: identity4(),
			},
		},
	}
}

func digest(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func identity3() []float64 { return []float64{1, 0, 0, 0, 1, 0, 0, 0, 1} }
func identity4() []float64 { return []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1} }
