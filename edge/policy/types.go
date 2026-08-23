package policy

import (
	"errors"
	"fmt"
	"math"
	"strings"
	"time"
)

var (
	ErrObservationInvalid     = errors.New("invalid policy observation")
	ErrObservationStale       = errors.New("policy observation is stale")
	ErrObservationUnavailable = errors.New("required policy observation source is unavailable")
	ErrInferenceInvalid       = errors.New("invalid policy inference result")
	ErrInferenceIdentity      = errors.New("policy inference identity mismatch")
	ErrActionUnsafe           = errors.New("policy action is outside declared bounds")
)

type ObservationSource struct {
	Fresh      bool     `json:"fresh"`
	Confidence float64  `json:"confidence"`
	Anomalies  []string `json:"anomalies,omitempty"`
}

type FrameReference struct {
	URI        string    `json:"uri"`
	MIME       string    `json:"mime"`
	SHA256     string    `json:"sha256"`
	Width      int       `json:"width"`
	Height     int       `json:"height"`
	ObservedAt time.Time `json:"observedAt"`
}

type Entity struct {
	EntityID   string            `json:"entityId"`
	Category   string            `json:"category"`
	Attributes map[string]string `json:"attributes,omitempty"`
	Pose       []float64         `json:"pose,omitempty"`
	Relations  map[string]string `json:"relations,omitempty"`
	Confidence float64           `json:"confidence"`
}

type ObservationBundle struct {
	SchemaVersion       string                       `json:"schemaVersion"`
	ObservationID       string                       `json:"observationId"`
	ObservedAt          time.Time                    `json:"observedAt"`
	RobotID             string                       `json:"robotId"`
	Adapter             string                       `json:"adapter"`
	RobotModel          string                       `json:"robotModel"`
	TransformRevision   string                       `json:"transformRevision"`
	CalibrationRevision string                       `json:"calibrationRevision,omitempty"`
	Sources             map[string]ObservationSource `json:"sources"`
	RobotState          map[string]float64           `json:"robotState,omitempty"`
	Entities            []Entity                     `json:"entities,omitempty"`
	Frames              map[string]FrameReference    `json:"frames,omitempty"`
}

type InferenceRequest struct {
	SchemaVersion    string            `json:"schemaVersion"`
	RequestID        string            `json:"requestId"`
	CommandID        string            `json:"commandId"`
	TaskID           string            `json:"taskId"`
	TaskRevision     uint64            `json:"taskRevision"`
	StepID           string            `json:"stepId"`
	RobotID          string            `json:"robotId"`
	Capability       string            `json:"capability"`
	TargetRef        string            `json:"targetRef,omitempty"`
	Parameters       map[string]any    `json:"parameters,omitempty"`
	ManifestRevision string            `json:"manifestRevision"`
	Observation      ObservationBundle `json:"observation"`
	WorldRevision    uint64            `json:"worldRevision"`
	ResourceID       string            `json:"resourceId,omitempty"`
	FencingToken     uint64            `json:"fencingToken,omitempty"`
}

type Action struct {
	Values map[string]float64 `json:"values"`
}

type InferenceResult struct {
	SchemaVersion    string   `json:"schemaVersion"`
	RequestID        string   `json:"requestId"`
	CommandID        string   `json:"commandId"`
	ManifestRevision string   `json:"manifestRevision"`
	InferenceID      string   `json:"inferenceId"`
	ObservationID    string   `json:"observationId"`
	ElapsedMS        float64  `json:"elapsedMs,omitempty"`
	Actions          []Action `json:"actions"`
	Diagnostics      []string `json:"diagnostics,omitempty"`
}

type Decision struct {
	InferenceID      string
	ManifestRevision string
	ObservationID    string
	ElapsedMS        float64
	Actions          []map[string]float64
}

func ValidateObservation(manifest Manifest, observation ObservationBundle, now time.Time) error {
	if err := manifest.Validate(); err != nil {
		return err
	}
	if observation.SchemaVersion != manifest.ObservationSchema || strings.TrimSpace(observation.ObservationID) == "" ||
		strings.TrimSpace(observation.RobotID) == "" || observation.ObservedAt.IsZero() ||
		observation.ObservedAt.After(now) {
		return ErrObservationInvalid
	}
	if now.Sub(observation.ObservedAt) > manifest.MaxObservationAge {
		return ErrObservationStale
	}
	if manifest.TransformRevision != "" && observation.TransformRevision != manifest.TransformRevision {
		return fmt.Errorf("%w: transform revision", ErrObservationInvalid)
	}
	for _, sourceID := range manifest.RequiredObservationSources {
		source, exists := observation.Sources[sourceID]
		if !exists || !source.Fresh || !finite(source.Confidence) || source.Confidence < 0 || source.Confidence > 1 || len(source.Anomalies) > 0 {
			return fmt.Errorf("%w: %s", ErrObservationUnavailable, sourceID)
		}
	}
	for _, value := range observation.RobotState {
		if !finite(value) {
			return ErrObservationInvalid
		}
	}
	for _, entity := range observation.Entities {
		if entity.EntityID == "" || entity.Category == "" || !finite(entity.Confidence) {
			return ErrObservationInvalid
		}
		for _, value := range entity.Pose {
			if !finite(value) {
				return ErrObservationInvalid
			}
		}
	}
	return nil
}

func ValidateInference(manifest Manifest, request InferenceRequest, result InferenceResult) (Decision, error) {
	if err := manifest.Validate(); err != nil {
		return Decision{}, err
	}
	expectedRevision, err := manifest.Revision()
	if err != nil {
		return Decision{}, err
	}
	if request.SchemaVersion != "policy.inference.request.v1" || request.RequestID == "" || request.CommandID == "" ||
		request.TaskID == "" || request.RobotID == "" || request.Capability == "" ||
		request.ManifestRevision != expectedRevision {
		return Decision{}, ErrInferenceInvalid
	}
	if result.SchemaVersion != "policy.inference.result.v1" || result.InferenceID == "" ||
		result.RequestID != request.RequestID || result.CommandID != request.CommandID ||
		result.ManifestRevision != request.ManifestRevision || result.ObservationID != request.Observation.ObservationID {
		return Decision{}, ErrInferenceIdentity
	}
	if len(result.Actions) == 0 || len(result.Actions) > manifest.MaxActionChunkLength {
		return Decision{}, ErrActionUnsafe
	}
	actions := make([]map[string]float64, len(result.Actions))
	for index, action := range result.Actions {
		if len(action.Values) == 0 {
			return Decision{}, ErrActionUnsafe
		}
		actions[index] = make(map[string]float64, len(action.Values))
		for name, value := range action.Values {
			bound, exists := manifest.ActionBounds[name]
			if !exists || !finite(value) || value < bound.Minimum || value > bound.Maximum {
				return Decision{}, fmt.Errorf("%w: %s", ErrActionUnsafe, name)
			}
			actions[index][name] = value
		}
	}
	return Decision{
		InferenceID: result.InferenceID, ManifestRevision: result.ManifestRevision,
		ObservationID: result.ObservationID, ElapsedMS: result.ElapsedMS, Actions: actions,
	}, nil
}

// ValidateDecision revalidates the normalized result at the Worker trust
// boundary. Custom Provider implementations cannot bypass manifest action
// bounds merely by returning a Decision directly.
func ValidateDecision(manifest Manifest, request InferenceRequest, decision Decision) error {
	if decision.ManifestRevision != request.ManifestRevision ||
		decision.ObservationID != request.Observation.ObservationID || decision.InferenceID == "" {
		return ErrInferenceIdentity
	}
	if len(decision.Actions) == 0 || len(decision.Actions) > manifest.MaxActionChunkLength {
		return ErrActionUnsafe
	}
	for _, action := range decision.Actions {
		if len(action) == 0 {
			return ErrActionUnsafe
		}
		for name, value := range action {
			bound, exists := manifest.ActionBounds[name]
			if !exists || !finite(value) || value < bound.Minimum || value > bound.Maximum {
				return fmt.Errorf("%w: %s", ErrActionUnsafe, name)
			}
		}
	}
	return nil
}

func finite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}
