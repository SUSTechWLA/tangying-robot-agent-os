package worker

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

var ErrPolicyRequired = errors.New("runtime capability requires a policy action chunk")

func (w *Worker) buildPolicyObservation(ctx context.Context, command runtime.Command) (policy.ObservationBundle, error) {
	if w.config.PolicyObservation != nil {
		return w.config.PolicyObservation.ObservePolicy(ctx, command)
	}
	observer := w.config.Observer
	if observer == nil {
		observer = w.config.Runtime
	}
	if observer == nil {
		return policy.ObservationBundle{}, errors.New("robot observation provider is required")
	}
	snapshot, err := observer.Telemetry(ctx, command.TaskID)
	if err != nil {
		return policy.ObservationBundle{}, fmt.Errorf("policy observation: %w", err)
	}
	adapter, robotModel, transformRevision := w.config.Adapter, w.config.RobotModel, w.config.TransformRevision
	if profile := snapshot.RobotProfile; profile != nil {
		if profile.RobotID != w.config.RobotID || (adapter != "" && adapter != profile.AdapterID) || (robotModel != "" && robotModel != profile.ModelID) {
			return policy.ObservationBundle{}, errors.New("policy robot identity differs from connected profile")
		}
		adapter, robotModel = profile.AdapterID, profile.ModelID
	}
	if reconstruction := snapshot.Reconstruction; reconstruction != nil {
		transformRevision = reconstruction.TransformRevision
		snapshot.ObservedAt = time.UnixMilli(reconstruction.ObservedAtUnixMS).UTC()
	}
	if snapshot.ObservedAt.IsZero() {
		return policy.ObservationBundle{}, errors.New("policy observation acquisition time is missing")
	}
	anomalies := append([]string(nil), snapshot.Anomalies...)
	healthy := len(anomalies) == 0
	robotState := numericSnapshotState(snapshot, w.config.RobotID)
	entities := make([]policy.Entity, 0, len(snapshot.Entities))
	for _, entity := range w.worldEntities(snapshot) {
		entities = append(entities, policy.Entity{
			EntityID: entity.EntityID, Category: entity.Category,
			Attributes: cloneAttributes(entity.Attributes), Pose: append([]float64(nil), entity.Pose...),
			Relations: sceneRelations(entity.Relation), Confidence: entity.Confidence,
		})
	}
	return policy.ObservationBundle{
		SchemaVersion: "policy.observation.v1",
		ObservationID: fmt.Sprintf("policy/%s/%s/%d", w.config.RobotID, command.CommandID, snapshot.ObservedAt.UnixNano()),
		ObservedAt:    snapshot.ObservedAt, RobotID: w.config.RobotID, Adapter: adapter,
		RobotModel: robotModel, TransformRevision: transformRevision,
		CalibrationRevision: w.config.CalibrationRevision,
		Sources: map[string]policy.ObservationSource{
			"scene":          {Fresh: healthy && len(entities) > 0, Confidence: minimumEntityConfidence(entities), Anomalies: anomalies},
			"proprioception": {Fresh: healthy && len(robotState) > 0, Confidence: confidenceForPresence(len(robotState) > 0), Anomalies: anomalies},
		},
		RobotState: robotState, Entities: entities,
	}, nil
}

func (w *Worker) preparePolicyCommand(
	ctx context.Context,
	command runtime.Command,
	snapshot runtime.Snapshot,
) (runtime.Command, error) {
	capability, advertised := snapshot.Capability(string(command.Capability))
	required := advertised && containsString(capability.InputParameters, "action_chunk")
	learnedPhysical := command.Capability == runtime.CapabilityPick || command.Capability == runtime.CapabilityPlace
	if w.config.Policy == nil {
		if required {
			return runtime.Command{}, ErrPolicyRequired
		}
		return command, nil
	}
	if !learnedPhysical {
		return command, nil
	}
	manifest, err := w.config.Policy.Manifest(ctx)
	if err != nil {
		return runtime.Command{}, err
	}
	revision, err := manifest.Revision()
	if err != nil {
		return runtime.Command{}, err
	}
	observation, err := w.buildPolicyObservation(ctx, command)
	if err != nil {
		return runtime.Command{}, err
	}
	if err := manifest.CheckCompatibility(policy.Compatibility{
		Capability: string(command.Capability), RobotModel: observation.RobotModel,
		Adapter: observation.Adapter, TransformRevision: observation.TransformRevision,
		CalibrationRevision: observation.CalibrationRevision,
	}); err != nil {
		return runtime.Command{}, err
	}
	if err := policy.ValidateObservation(manifest, observation, time.Now().UTC()); err != nil {
		return runtime.Command{}, err
	}
	request := policy.InferenceRequest{
		SchemaVersion: "policy.inference.request.v1", RequestID: command.CommandID + "/policy",
		CommandID: command.CommandID, TaskID: command.TaskID, TaskRevision: command.TaskRevision,
		StepID: command.StepID, RobotID: command.RobotID, Capability: string(command.Capability),
		TargetRef: command.TargetRef, Parameters: safePolicyParameters(command.Parameters),
		ManifestRevision: revision, Observation: observation,
		WorldRevision: command.WorldRevisionBasis, ResourceID: command.ResourceID, FencingToken: command.FencingToken,
	}
	decision, err := w.config.Policy.Infer(ctx, request)
	if err != nil {
		return runtime.Command{}, err
	}
	if err := policy.ValidateDecision(manifest, request, decision); err != nil {
		return runtime.Command{}, err
	}
	parameters := cloneAnyMap(command.Parameters)
	parameters["action_chunk"] = wireActions(decision.Actions)
	parameters["policy_execution"] = map[string]any{
		"policyId": manifest.PolicyID, "policyVersion": manifest.Version,
		"framework": string(manifest.Framework), "artifactSha256": manifest.ArtifactSHA256,
		"manifestRevision": revision, "inferenceId": decision.InferenceID,
		"observationId": decision.ObservationID, "elapsedMs": decision.ElapsedMS,
	}
	command.Parameters = parameters
	return command, nil
}

func minimumEntityConfidence(entities []policy.Entity) float64 {
	if len(entities) == 0 {
		return 0
	}
	minimum := 1.0
	for _, entity := range entities {
		if entity.Confidence < minimum {
			minimum = entity.Confidence
		}
	}
	return minimum
}

func confidenceForPresence(present bool) float64 {
	if present {
		return 1
	}
	return 0
}

func containsString(values []string, expected string) bool {
	for _, value := range values {
		if strings.TrimSpace(value) == expected {
			return true
		}
	}
	return false
}

func safePolicyParameters(parameters map[string]any) map[string]any {
	result := cloneAnyMap(parameters)
	delete(result, "action_chunk")
	return result
}

func cloneAnyMap(source map[string]any) map[string]any {
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func wireActions(actions []map[string]float64) []any {
	result := make([]any, 0, len(actions))
	for _, action := range actions {
		values := make(map[string]any, len(action))
		for name, value := range action {
			values[name] = value
		}
		result = append(result, values)
	}
	return result
}
