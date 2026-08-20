package observation

import (
	"errors"
	"fmt"
	"math"
	"strings"
	"time"
)

var (
	ErrInvalidEnvelope   = errors.New("invalid observation envelope")
	ErrInvalidConfidence = errors.New("invalid observation confidence")
	ErrAmbiguousPayload  = errors.New("observation cannot contain payload and frame reference")
	ErrPayloadType       = errors.New("observation payload does not match kind")
)

type SourceType string

const (
	SourceSimGroundTruth     SourceType = "sim_ground_truth"
	SourceProprioception     SourceType = "robot_proprioception"
	SourceRGBDCamera         SourceType = "rgbd_camera"
	SourceLidar              SourceType = "lidar"
	SourceSLAMPose           SourceType = "slam_pose"
	SourceToolResultEvidence SourceType = "tool_result_evidence"
	SourceOperatorAnnotation SourceType = "operator_annotation"
)

type Kind string

const (
	EntityUpsert     Kind = "entity_upsert"
	EntityDelete     Kind = "entity_delete"
	RobotStateUpsert Kind = "robot_state_upsert"
	ResourceUpsert   Kind = "resource_upsert"
	SourceHealth     Kind = "source_health"
	FrameReference   Kind = "frame_reference"
)

type Envelope struct {
	SchemaVersion     string     `json:"schemaVersion"`
	ObservationID     string     `json:"observationId"`
	WorldID           string     `json:"worldId"`
	SourceID          string     `json:"sourceId"`
	RobotID           string     `json:"robotId,omitempty"`
	SourceType        SourceType `json:"sourceType"`
	SourceSequence    uint64     `json:"sourceSequence"`
	ObservedAt        time.Time  `json:"observedAt"`
	ReceivedAt        time.Time  `json:"receivedAt"`
	FrameID           string     `json:"frameId"`
	TransformRevision string     `json:"transformRevision"`
	Kind              Kind       `json:"kind"`
	Payload           any        `json:"payload,omitempty"`
	FrameRef          *FrameRef  `json:"frameRef,omitempty"`
	Confidence        float64    `json:"confidence"`
	Quality           Quality    `json:"quality"`
	Causation         Causation  `json:"causation,omitempty"`
	Provenance        Provenance `json:"provenance"`
}

type EntityPayload struct {
	EntityID   string            `json:"entityId"`
	Category   string            `json:"category,omitempty"`
	Attributes map[string]string `json:"attributes,omitempty"`
	Pose       []float64         `json:"pose,omitempty"`
	Relations  map[string]string `json:"relations,omitempty"`
}

type RobotPayload struct {
	RobotID          string             `json:"robotId"`
	Pose             []float64          `json:"pose,omitempty"`
	Activity         string             `json:"activity,omitempty"`
	Held             string             `json:"held,omitempty"`
	EmergencyStopped bool               `json:"emergencyStopped"`
	State            map[string]float64 `json:"state,omitempty"`
}

type ResourcePayload struct {
	ResourceID   string    `json:"resourceId"`
	Owner        string    `json:"owner"`
	FencingToken uint64    `json:"fencingToken"`
	ExpiresAt    time.Time `json:"expiresAt,omitempty"`
}

type SourceHealthPayload struct {
	Status    string   `json:"status"`
	Anomalies []string `json:"anomalies,omitempty"`
}

type FrameRef struct {
	URI        string    `json:"uri"`
	MIME       string    `json:"mime"`
	SHA256     string    `json:"sha256"`
	Width      int       `json:"width"`
	Height     int       `json:"height"`
	ObservedAt time.Time `json:"observedAt"`
}

type Quality struct {
	LatencyMS float64  `json:"latencyMs,omitempty"`
	Anomalies []string `json:"anomalies,omitempty"`
}

type Causation struct {
	TaskID    string `json:"taskId,omitempty"`
	CommandID string `json:"commandId,omitempty"`
}

type Provenance struct {
	Adapter string `json:"adapter"`
	Version string `json:"version"`
	Sensor  string `json:"sensor,omitempty"`
}

func (e Envelope) Validate() error {
	if e.SchemaVersion != "world.observation.v1" || strings.TrimSpace(e.ObservationID) == "" ||
		strings.TrimSpace(e.WorldID) == "" || strings.TrimSpace(e.SourceID) == "" || e.SourceType == "" ||
		e.SourceSequence == 0 || e.ObservedAt.IsZero() || e.ReceivedAt.IsZero() ||
		strings.TrimSpace(e.FrameID) == "" || strings.TrimSpace(e.TransformRevision) == "" ||
		strings.TrimSpace(e.Provenance.Adapter) == "" || strings.TrimSpace(e.Provenance.Version) == "" {
		return ErrInvalidEnvelope
	}
	if e.ReceivedAt.Before(e.ObservedAt) {
		return fmt.Errorf("%w: received before observed", ErrInvalidEnvelope)
	}
	if math.IsNaN(e.Confidence) || math.IsInf(e.Confidence, 0) || e.Confidence < 0 || e.Confidence > 1 {
		return ErrInvalidConfidence
	}
	if e.FrameRef != nil && e.Payload != nil {
		return ErrAmbiguousPayload
	}
	switch e.Kind {
	case EntityUpsert:
		payload, ok := e.Payload.(EntityPayload)
		if !ok || payload.EntityID == "" || payload.Category == "" || !finiteSlice(payload.Pose) {
			return ErrPayloadType
		}
	case EntityDelete:
		payload, ok := e.Payload.(EntityPayload)
		if !ok || payload.EntityID == "" {
			return ErrPayloadType
		}
	case RobotStateUpsert:
		payload, ok := e.Payload.(RobotPayload)
		if !ok || payload.RobotID == "" || !finiteSlice(payload.Pose) {
			return ErrPayloadType
		}
	case ResourceUpsert:
		payload, ok := e.Payload.(ResourcePayload)
		if !ok || payload.ResourceID == "" || payload.FencingToken == 0 {
			return ErrPayloadType
		}
	case SourceHealth:
		if _, ok := e.Payload.(SourceHealthPayload); !ok {
			return ErrPayloadType
		}
	case FrameReference:
		if e.FrameRef == nil || e.FrameRef.URI == "" || e.FrameRef.MIME == "" || e.FrameRef.SHA256 == "" ||
			e.FrameRef.Width <= 0 || e.FrameRef.Height <= 0 || e.FrameRef.ObservedAt.IsZero() {
			return ErrPayloadType
		}
	default:
		return ErrPayloadType
	}
	return nil
}

func finiteSlice(values []float64) bool {
	for _, value := range values {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			return false
		}
	}
	return true
}
