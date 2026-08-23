package policy

import (
	"context"
	"fmt"
	"sort"
	"time"
)

// DeterministicProvider exists for simulation audit and CI. It must not be
// silently selected for a physical adapter.
type DeterministicProvider struct {
	manifest Manifest
	revision string
}

func NewDeterministicProvider(manifest Manifest) (*DeterministicProvider, error) {
	if manifest.Framework != FrameworkDeterministic {
		return nil, fmt.Errorf("%w: deterministic provider requires deterministic manifest", ErrManifestInvalid)
	}
	revision, err := manifest.Revision()
	if err != nil {
		return nil, err
	}
	return &DeterministicProvider{manifest: manifest, revision: revision}, nil
}

func (provider *DeterministicProvider) Manifest(context.Context) (Manifest, error) {
	return provider.manifest, nil
}

func (provider *DeterministicProvider) Infer(ctx context.Context, request InferenceRequest) (Decision, error) {
	if err := ctx.Err(); err != nil {
		return Decision{}, err
	}
	if request.ManifestRevision != provider.revision {
		return Decision{}, ErrManifestDrift
	}
	if err := provider.manifest.CheckCompatibility(Compatibility{
		Capability: request.Capability, RobotModel: request.Observation.RobotModel,
		Adapter: request.Observation.Adapter, TransformRevision: request.Observation.TransformRevision,
		CalibrationRevision: request.Observation.CalibrationRevision,
	}); err != nil {
		return Decision{}, err
	}
	if err := ValidateObservation(provider.manifest, request.Observation, time.Now().UTC()); err != nil {
		return Decision{}, err
	}
	names := make([]string, 0, len(provider.manifest.ActionBounds))
	for name := range provider.manifest.ActionBounds {
		names = append(names, name)
	}
	sort.Strings(names)
	values := make(map[string]float64, len(names))
	for _, name := range names {
		bound := provider.manifest.ActionBounds[name]
		value := (bound.Minimum + bound.Maximum) / 2
		if request.Capability == "manipulation.pick" && name == "left_arm_gripper.pos" {
			value = bound.Maximum
		}
		if request.Capability == "manipulation.place" && name == "left_arm_gripper.pos" {
			value = bound.Minimum
		}
		values[name] = value
	}
	result := InferenceResult{
		SchemaVersion: "policy.inference.result.v1", RequestID: request.RequestID,
		CommandID: request.CommandID, ManifestRevision: provider.revision,
		InferenceID:   "deterministic/" + request.CommandID,
		ObservationID: request.Observation.ObservationID,
		Actions:       []Action{{Values: values}},
	}
	return ValidateInference(provider.manifest, request, result)
}
