// Package policy defines the model-neutral boundary between an Edge Worker
// and a VLA, imitation-learning, reinforcement-learning, or deterministic
// policy provider. Policy output is only a candidate action: Robot Runtime
// safety checks and Harness world evidence remain authoritative.
package policy

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"
)

var (
	ErrManifestInvalid    = errors.New("invalid policy manifest")
	ErrArtifactIdentity   = errors.New("policy artifact identity is required")
	ErrPolicyIncompatible = errors.New("policy is incompatible with execution context")
)

type Framework string

const (
	FrameworkVLA           Framework = "vla"
	FrameworkImitation     Framework = "imitation"
	FrameworkReinforcement Framework = "reinforcement"
	FrameworkDeterministic Framework = "deterministic"
)

type ActionBound struct {
	Minimum float64 `json:"minimum"`
	Maximum float64 `json:"maximum"`
}

type TrainingMetadata struct {
	Dataset        string `json:"dataset,omitempty"`
	Algorithm      string `json:"algorithm,omitempty"`
	Seed           string `json:"seed,omitempty"`
	EvaluationPack string `json:"evaluationPack,omitempty"`
	PromotedBy     string `json:"promotedBy,omitempty"`
}

type Manifest struct {
	SchemaVersion              string                 `json:"schemaVersion"`
	PolicyID                   string                 `json:"policyId"`
	Version                    string                 `json:"version"`
	Framework                  Framework              `json:"framework"`
	ArtifactSHA256             string                 `json:"artifactSha256"`
	Capabilities               []string               `json:"capabilities"`
	RobotModels                []string               `json:"robotModels"`
	Adapters                   []string               `json:"adapters"`
	ObservationSchema          string                 `json:"observationSchema"`
	RequiredObservationSources []string               `json:"requiredObservationSources"`
	MaxObservationAge          time.Duration          `json:"maxObservationAge"`
	ActionSchema               string                 `json:"actionSchema"`
	MaxActionChunkLength       int                    `json:"maxActionChunkLength"`
	ActionBounds               map[string]ActionBound `json:"actionBounds"`
	TransformRevision          string                 `json:"transformRevision,omitempty"`
	CalibrationRevision        string                 `json:"calibrationRevision,omitempty"`
	Training                   TrainingMetadata       `json:"training,omitempty"`
}

type Compatibility struct {
	Capability          string
	RobotModel          string
	Adapter             string
	TransformRevision   string
	CalibrationRevision string
}

var sha256Pattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

func (manifest Manifest) Validate() error {
	if manifest.SchemaVersion != "policy.manifest.v1" || strings.TrimSpace(manifest.PolicyID) == "" ||
		strings.TrimSpace(manifest.Version) == "" || strings.TrimSpace(manifest.ObservationSchema) == "" ||
		strings.TrimSpace(manifest.ActionSchema) == "" || manifest.MaxObservationAge <= 0 ||
		manifest.MaxActionChunkLength <= 0 || len(manifest.Capabilities) == 0 || len(manifest.ActionBounds) == 0 {
		return ErrManifestInvalid
	}
	switch manifest.Framework {
	case FrameworkVLA, FrameworkImitation, FrameworkReinforcement:
		if !sha256Pattern.MatchString(strings.ToLower(manifest.ArtifactSHA256)) {
			return ErrArtifactIdentity
		}
	case FrameworkDeterministic:
		if !strings.HasPrefix(manifest.ArtifactSHA256, "deterministic:") {
			return ErrArtifactIdentity
		}
	default:
		return fmt.Errorf("%w: unsupported framework %q", ErrManifestInvalid, manifest.Framework)
	}
	for name, bound := range manifest.ActionBounds {
		if strings.TrimSpace(name) == "" || !finite(bound.Minimum) || !finite(bound.Maximum) || bound.Minimum > bound.Maximum {
			return fmt.Errorf("%w: invalid action bound %q", ErrManifestInvalid, name)
		}
	}
	return nil
}

func (manifest Manifest) Revision() (string, error) {
	if err := manifest.Validate(); err != nil {
		return "", err
	}
	canonical := manifest
	canonical.Capabilities = canonicalSet(manifest.Capabilities)
	canonical.RobotModels = canonicalSet(manifest.RobotModels)
	canonical.Adapters = canonicalSet(manifest.Adapters)
	canonical.RequiredObservationSources = canonicalSet(manifest.RequiredObservationSources)
	wire, err := json.Marshal(canonical)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(wire)
	return hex.EncodeToString(sum[:]), nil
}

func (manifest Manifest) CheckCompatibility(value Compatibility) error {
	if err := manifest.Validate(); err != nil {
		return err
	}
	checks := []struct {
		label string
		got   string
		set   []string
	}{
		{"capability", value.Capability, manifest.Capabilities},
		{"robot model", value.RobotModel, manifest.RobotModels},
		{"adapter", value.Adapter, manifest.Adapters},
	}
	for _, check := range checks {
		if len(check.set) > 0 && !contains(check.set, check.got) {
			return fmt.Errorf("%w: %s %q", ErrPolicyIncompatible, check.label, check.got)
		}
	}
	if manifest.TransformRevision != "" && manifest.TransformRevision != value.TransformRevision {
		return fmt.Errorf("%w: transform revision %q", ErrPolicyIncompatible, value.TransformRevision)
	}
	if manifest.CalibrationRevision != "" && manifest.CalibrationRevision != value.CalibrationRevision {
		return fmt.Errorf("%w: calibration revision %q", ErrPolicyIncompatible, value.CalibrationRevision)
	}
	return nil
}

func canonicalSet(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if _, exists := seen[value]; exists {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func contains(values []string, expected string) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}
