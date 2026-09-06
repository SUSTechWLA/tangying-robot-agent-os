package robotclient_test

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"google.golang.org/grpc"
)

type observedScene struct {
	robotv1.UnimplementedRobotRuntimeServer
	observation *robotv1.Observation
}

func (s *observedScene) Observe(_ *robotv1.ObserveRequest, stream robotv1.RobotRuntime_ObserveServer) error {
	return stream.Send(s.observation)
}

func TestGroundingEnforcesTheRequestedSourceBeforePlanningMotion(t *testing.T) {
	for _, tc := range []struct {
		name, relation string
		sourceCount    int
		valid          bool
	}{
		{"inside requested source", "inside:handoff-zone", 1, true},
		{"on requested source", "on:handoff-zone", 1, true},
		{"object is somewhere else", "inside:left-start-zone", 1, false},
		{"location unknown", "", 1, false},
		{"held object is not at source", "held_by:robot-1", 1, false},
		{"source missing", "inside:handoff-zone", 0, false},
		{"source ambiguous", "inside:handoff-zone", 2, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			observation := &robotv1.Observation{ObservationId: "obs-test", WallTimeUnixMs: time.Now().UnixMilli(), Entities: []*robotv1.SceneEntity{
				{EntityId: "red-block", Category: "block", Attributes: map[string]string{"color": "red"}, Relation: tc.relation, Confidence: .99},
				{EntityId: "right-target-zone", Category: "target_zone", Relation: "right_side", Confidence: 1},
			}}
			for range tc.sourceCount {
				observation.Entities = append(observation.Entities, &robotv1.SceneEntity{EntityId: "handoff-zone", Category: "handoff_zone", Confidence: 1})
			}
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			server := grpc.NewServer()
			robotv1.RegisterRobotRuntimeServer(server, &observedScene{observation: observation})
			go server.Serve(listener)
			t.Cleanup(server.Stop)
			client, err := robotclient.New(robotclient.Config{Address: listener.Addr().String(), DevInsecure: true})
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = client.Close() })
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			result, err := client.Ground(ctx, manipulation.Intent{Action: "pick_and_place", Object: manipulation.EntitySelector{Category: "block", Attributes: map[string]string{"color": "red"}}, Source: manipulation.EntitySelector{Category: "handoff_zone"}, Destination: manipulation.EntitySelector{Category: "target_zone", Relation: "right_side"}})
			if !tc.valid && err == nil {
				t.Fatalf("unproven source became an executable grounding: %+v", result)
			}
			if tc.valid && (err != nil || result.Object.ID != "red-block" || result.Destination.ID != "right-target-zone") {
				t.Fatalf("valid grounding rejected: %+v %v", result, err)
			}
		})
	}
}
