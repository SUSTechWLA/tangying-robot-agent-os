package robotclient_test

import (
	"context"
	"net"
	"strings"
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

func (s *observedScene) GetRuntimeInfo(context.Context, *robotv1.GetRuntimeInfoRequest) (*robotv1.RuntimeInfo, error) {
	return &robotv1.RuntimeInfo{RobotId: "test-robot", Adapter: "mujoco", ProtocolVersion: "1.0"}, nil
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

// A grounding failure must say which world the robot actually observes. A bare
// "objects=0 destinations=0" left operators and reviewers unable to tell an
// unsupported scene (for example the four-room home scene, which commissions no
// pickable fixtures) from a camera or matching defect.
func TestGroundingFailureNamesTheObservedSceneAndTheOperatorOverride(t *testing.T) {
	for _, tc := range []struct {
		name     string
		entities []*robotv1.SceneEntity
		contains []string
	}{
		{
			name:     "scene commissions no pickable objects",
			entities: nil,
			contains: []string{
				"objects=0 destinations=0",
				"robot=test-robot adapter=mujoco observed=0",
				"commissions no pickable objects",
				"restart --perception rgbd --scene tabletop",
			},
		},
		{
			name: "no candidate is ambiguous",
			entities: []*robotv1.SceneEntity{
				{EntityId: "blue-bottle", Category: "bottle", Attributes: map[string]string{"color": "blue"}, Confidence: 1},
			},
			contains: []string{
				"grounding absent: objects=0 destinations=0",
				"visible=blue-bottle(bottle/blue)",
			},
		},
		{
			name: "camera sees other objects only",
			entities: []*robotv1.SceneEntity{
				{EntityId: "kitchen-bin", Category: "storage_bin", Attributes: map[string]string{"color": "blue"}, Confidence: 1},
				{EntityId: "door", Category: "fixture", Confidence: 1},
			},
			contains: []string{
				"observed=2",
				"visible=door(fixture),kitchen-bin(storage_bin/blue)",
			},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			observation := &robotv1.Observation{
				ObservationId: "obs-empty", WallTimeUnixMs: time.Now().UnixMilli(), Entities: tc.entities,
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
			_, err = client.Ground(ctx, manipulation.Intent{
				Action:      "pick_and_place",
				Object:      manipulation.EntitySelector{Category: "cup", Attributes: map[string]string{"color": "red"}},
				Destination: manipulation.EntitySelector{Category: "storage_bin", Relation: "right_side"},
			})
			if err == nil {
				t.Fatal("an unobserved target became an executable grounding")
			}
			for _, want := range tc.contains {
				if !strings.Contains(err.Error(), want) {
					t.Fatalf("grounding error %q does not explain %q", err.Error(), want)
				}
			}
		})
	}
}
