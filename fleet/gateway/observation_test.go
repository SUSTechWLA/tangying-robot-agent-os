package gateway

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

func TestObservationFromProtoBuildsTypedEntityPayload(t *testing.T) {
	payload, err := structpb.NewStruct(map[string]any{
		"entityId": "red-block", "category": "block", "pose": []any{0.5, 0.3, 0.8},
		"relations": map[string]any{"inside": "handoff-zone"},
	})
	if err != nil {
		t.Fatal(err)
	}
	wire := &fleetv1.ObservationEnvelope{
		SchemaVersion: "world.observation.v1", ObservationId: "obs-1", WorldId: "world-test",
		SourceId: "robot-1/scene", RobotId: "robot-1", SourceType: string(observation.SourceSimGroundTruth),
		SourceSequence: 1, ObservedUnixMs: time.Unix(100, 0).UnixMilli(), ReceivedUnixMs: time.Unix(100, 0).UnixMilli(),
		FrameId: "world", TransformRevision: "scene-v1", Kind: string(observation.EntityUpsert), Payload: payload,
		Confidence: 1, Provenance: &fleetv1.ObservationProvenance{Adapter: "mujoco", Version: "0.1.0"},
	}
	envelope, err := observationFromProto(wire)
	if err != nil {
		t.Fatal(err)
	}
	entity, ok := envelope.Payload.(observation.EntityPayload)
	if !ok || entity.Relations["inside"] != "handoff-zone" || entity.Pose[0] != 0.5 {
		t.Fatalf("envelope=%#v", envelope)
	}
}
