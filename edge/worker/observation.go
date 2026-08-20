package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

// BuildObservations converts one Robot Runtime snapshot into typed world
// facts. Sequences are monotonic independently for every advertised source.
func (w *Worker) BuildObservations(ctx context.Context) ([]observation.Envelope, error) {
	sample, err := w.buildSample(ctx)
	if err != nil {
		return nil, err
	}
	return w.observationsFromSample(sample), nil
}

// ObservationsFromTelemetry converts an already sampled local runtime view to
// the same typed, monotonic world events sent over the cloud Fleet Link. It is
// used by Local Brain so future Harness Agents do not need a second schema.
func (w *Worker) ObservationsFromTelemetry(snapshot telemetry.Snapshot) []observation.Envelope {
	return w.observationsFromSample(w.sampleFromTelemetry(snapshot))
}

func (w *Worker) observationsFromSample(sample fleettelemetry.Sample) []observation.Envelope {
	receivedAt := time.Now().UTC()
	if receivedAt.Before(sample.ObservedAt) {
		receivedAt = sample.ObservedAt
	}
	provenance := observation.Provenance{Adapter: w.config.Adapter, Version: w.config.AdapterVersion}
	robotSource := w.config.RobotID + "/proprioception"
	robotSequence := w.nextObservationSequence(robotSource)
	result := []observation.Envelope{{
		SchemaVersion: "world.observation.v1", ObservationID: observationID(robotSource, robotSequence, sample.ObservedAt),
		WorldID: w.config.WorldID, SourceID: robotSource, RobotID: w.config.RobotID,
		SourceType: observation.SourceProprioception, SourceSequence: robotSequence,
		ObservedAt: sample.ObservedAt, ReceivedAt: receivedAt, FrameID: "world",
		TransformRevision: w.config.TransformRevision, Kind: observation.RobotStateUpsert,
		Payload: observation.RobotPayload{
			RobotID: w.config.RobotID, Pose: append([]float64(nil), sample.Pose...), Activity: sample.Activity,
			Held: sample.Held, EmergencyStopped: sample.EmergencyStopped, State: cloneNumericState(sample.State),
		},
		Confidence: 1, Quality: observation.Quality{LatencyMS: float64(receivedAt.Sub(sample.ObservedAt).Microseconds()) / 1000, Anomalies: append([]string(nil), sample.Anomalies...)},
		Provenance: provenance,
	}}
	entitySource := w.config.RobotID + "/scene"
	for _, entity := range sample.Entities {
		sequence := w.nextObservationSequence(entitySource)
		relations := map[string]string{}
		if destination := sample.Placements[entity.EntityID]; destination != "" {
			relations["inside"] = destination
		} else if entity.Relation != "" {
			relations["relation"] = entity.Relation
		}
		if len(relations) == 0 {
			relations = nil
		}
		result = append(result, observation.Envelope{
			SchemaVersion: "world.observation.v1", ObservationID: observationID(entitySource, sequence, sample.ObservedAt),
			WorldID: w.config.WorldID, SourceID: entitySource, RobotID: w.config.RobotID,
			SourceType: observation.SourceSimGroundTruth, SourceSequence: sequence,
			ObservedAt: sample.ObservedAt, ReceivedAt: receivedAt, FrameID: "world",
			TransformRevision: w.config.TransformRevision, Kind: observation.EntityUpsert,
			Payload: observation.EntityPayload{
				EntityID: entity.EntityID, Category: entity.Category, Attributes: cloneAttributes(entity.Attributes),
				Pose: append([]float64(nil), entity.Pose...), Relations: relations,
			},
			Confidence: entity.Confidence, Quality: observation.Quality{LatencyMS: float64(receivedAt.Sub(sample.ObservedAt).Microseconds()) / 1000},
			Provenance: provenance,
		})
	}
	return result
}

func (w *Worker) nextObservationSequence(sourceID string) uint64 {
	w.observation.Lock()
	defer w.observation.Unlock()
	if _, exists := w.observation.sequence[sourceID]; !exists {
		w.observation.sequence[sourceID] = w.observation.base
	}
	w.observation.sequence[sourceID]++
	return w.observation.sequence[sourceID]
}

func observationID(sourceID string, sequence uint64, observedAt time.Time) string {
	return fmt.Sprintf("%s/%020d/%d", sourceID, sequence, observedAt.UnixNano())
}

func cloneNumericState(source map[string]float64) map[string]float64 {
	if source == nil {
		return nil
	}
	result := make(map[string]float64, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func cloneAttributes(source map[string]string) map[string]string {
	if source == nil {
		return nil
	}
	result := make(map[string]string, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func observationToProto(envelope observation.Envelope) (*fleetv1.ObservationEnvelope, error) {
	var payload *structpb.Struct
	if envelope.Payload != nil {
		wire, err := json.Marshal(envelope.Payload)
		if err != nil {
			return nil, err
		}
		var values map[string]any
		if err := json.Unmarshal(wire, &values); err != nil {
			return nil, err
		}
		payload, err = structpb.NewStruct(values)
		if err != nil {
			return nil, err
		}
	}
	result := &fleetv1.ObservationEnvelope{
		SchemaVersion: envelope.SchemaVersion, ObservationId: envelope.ObservationID,
		WorldId: envelope.WorldID, SourceId: envelope.SourceID, RobotId: envelope.RobotID,
		SourceType: string(envelope.SourceType), SourceSequence: envelope.SourceSequence,
		ObservedUnixMs: envelope.ObservedAt.UnixMilli(), ReceivedUnixMs: envelope.ReceivedAt.UnixMilli(),
		FrameId: envelope.FrameID, TransformRevision: envelope.TransformRevision, Kind: string(envelope.Kind),
		Payload: payload, Confidence: envelope.Confidence,
		Quality:    &fleetv1.ObservationQuality{LatencyMs: envelope.Quality.LatencyMS, Anomalies: append([]string(nil), envelope.Quality.Anomalies...)},
		Causation:  &fleetv1.ObservationCausation{TaskId: envelope.Causation.TaskID, CommandId: envelope.Causation.CommandID},
		Provenance: &fleetv1.ObservationProvenance{Adapter: envelope.Provenance.Adapter, Version: envelope.Provenance.Version, Sensor: envelope.Provenance.Sensor},
	}
	if envelope.FrameRef != nil {
		result.FrameRef = &fleetv1.FrameReference{
			Uri: envelope.FrameRef.URI, Mime: envelope.FrameRef.MIME, Sha256: envelope.FrameRef.SHA256,
			Width: uint32(envelope.FrameRef.Width), Height: uint32(envelope.FrameRef.Height),
			ObservedUnixMs: envelope.FrameRef.ObservedAt.UnixMilli(),
		}
	}
	return result, nil
}
