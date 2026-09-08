package robotclient

import (
	"context"
	"net"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/types/known/structpb"
)

type strictSceneServer struct {
	robotv1.UnimplementedRobotRuntimeServer
	info     *robotv1.RuntimeInfo
	obs      *robotv1.Observation
	requests chan []string
	terminal *robotv1.SkillEvent
}

func (s *strictSceneServer) ExecuteSkill(command *robotv1.SkillCommand, stream robotv1.RobotRuntime_ExecuteSkillServer) error {
	event := *s.terminal
	event.CommandId = command.CommandId
	return stream.Send(&event)
}

func TestCommandReturnsTheExactVerificationObservationWithoutRepolling(t *testing.T) {
	c, s := strictScene(t)
	s.obs.CompressedImage = []byte("original-rgb")
	s.obs.CompressedDepthImage = []byte("original-depth")
	s.terminal = &robotv1.SkillEvent{Type: robotv1.SkillEventType_SKILL_EVENT_SUCCEEDED, ObservationId: s.obs.ObservationId, EvidenceObservation: s.obs, VerificationConfidence: .9}
	// A concurrent live observation may already be newer than command evidence.
	c.captures = map[string]captureCursor{"depth": {sequence: 2, timestamp: time.Now().UnixMilli(), id: "newer-live-capture"}}
	result, err := c.Invoke(t.Context(), runtime.Command{CommandID: "verify", Capability: "verify_placement", Deadline: time.Now().Add(time.Second), Lease: time.Second, IdempotencyKey: "verify"})
	if err != nil {
		t.Fatal(err)
	}
	if result.Evidence == nil {
		t.Fatal("command's original verification image was discarded")
	}
	if result.Evidence.Reconstruction.ObservationID != result.ObservationID || string(result.Evidence.Frame) != "original-rgb" || string(result.Evidence.DepthFrame) != "original-depth" {
		t.Fatal("command evidence replaced")
	}
	if c.captures["depth"].sequence != 2 {
		t.Fatal("historical command frame rewound live observation cursor")
	}
}

func TestCommandRejectsMismatchedOrStaleVerificationCapture(t *testing.T) {
	for _, which := range []string{"identity", "old", "source"} {
		t.Run(which, func(t *testing.T) {
			c, s := strictScene(t)
			s.terminal = &robotv1.SkillEvent{Type: robotv1.SkillEventType_SKILL_EVENT_SUCCEEDED, ObservationId: s.obs.ObservationId, EvidenceObservation: s.obs}
			switch which {
			case "identity":
				s.terminal.ObservationId = "another-frame"
			case "old":
				s.obs.WallTimeUnixMs = time.Now().Add(-time.Hour).UnixMilli()
			case "source":
				s.obs.Reconstruction.Fields["robotId"] = structpb.NewStringValue("another-robot")
			}
			_, err := c.Invoke(t.Context(), runtime.Command{CommandID: "verify", Capability: "verify_placement", Deadline: time.Now().Add(time.Second), Lease: time.Second, IdempotencyKey: "verify"})
			if err == nil {
				t.Fatal("invalid command evidence accepted")
			}
		})
	}
}

func TestGroundingMobileAdapterRequiresItsBoundedNavigationGoal(t *testing.T) {
	for _, which := range []string{"valid", "missing", "outside", "bad quaternion"} {
		t.Run(which, func(t *testing.T) {
			c, s := strictScene(t)
			profile := s.info.RobotProfile.AsMap()
			profile["tools"] = []any{"observe_scene", "emergency_stop", "navigation.navigate"}
			profile["actionLimits"] = map[string]any{"navigation.x": map[string]any{"min": -.2, "max": .2, "unit": "m"}, "navigation.y": map[string]any{"min": 0, "max": .2, "unit": "m"}, "navigation.z": map[string]any{"min": 0, "max": .1, "unit": "m"}}
			s.info.RobotProfile, _ = structpb.NewStruct(profile)
			s.info.Capabilities = append(s.info.Capabilities, &robotv1.CapabilityInfo{Name: "navigation.navigate", SafetyLevel: "physical_motion", Available: true})
			goal := []any{0, .1, .035, 1, 0, 0, 0}
			if which == "outside" {
				goal[1] = 5
			}
			if which == "bad quaternion" {
				goal[3] = 0
			}
			if which != "missing" {
				s.obs.RobotState, _ = structpb.NewStruct(map[string]any{"navigation": map[string]any{"approach_goal_pose": goal}})
			}
			grounded, err := c.Ground(t.Context(), manipulation.Intent{Object: manipulation.EntitySelector{Category: "cup"}, Destination: manipulation.EntitySelector{Category: "bin"}})
			if which != "valid" {
				if err == nil {
					t.Fatal("mobile plan can bypass invalid navigation goal")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if len(grounded.NavigationGoal) != 7 || grounded.NavigationGoal[1] != .1 {
				t.Fatal("declared navigation goal missing from grounded plan")
			}
		})
	}
}

func (s *strictSceneServer) GetRuntimeInfo(context.Context, *robotv1.GetRuntimeInfoRequest) (*robotv1.RuntimeInfo, error) {
	return s.info, nil
}
func (s *strictSceneServer) Observe(request *robotv1.ObserveRequest, stream robotv1.RobotRuntime_ObserveServer) error {
	if s.requests != nil {
		s.requests <- append([]string(nil), request.Streams...)
	}
	return stream.Send(s.obs)
}

func TestTelemetryRequestsColorDepthAndReconstructionWhileGroundingAvoidsImageStreams(t *testing.T) {
	client, server := strictScene(t)
	server.requests = make(chan []string, 2)
	if _, err := client.Telemetry(t.Context(), ""); err != nil {
		t.Fatal(err)
	}
	if got := <-server.requests; !slices.Equal(got, []string{"entities", "rgb", "depth", "reconstruction", "robot_state"}) {
		t.Fatalf("telemetry streams=%v", got)
	}
	if _, err := client.Ground(t.Context(), manipulation.Intent{Object: manipulation.EntitySelector{Category: "cup"}, Destination: manipulation.EntitySelector{Category: "bin"}}); err != nil {
		t.Fatal(err)
	}
	if got := <-server.requests; !slices.Equal(got, []string{"entities", "reconstruction"}) {
		t.Fatalf("ground streams=%v", got)
	}
}
func strictScene(t *testing.T) (*Client, *strictSceneServer) {
	t.Helper()
	profile := map[string]any{"schemaVersion": "robot.profile.v1", "robotId": "robot-a", "adapterId": "test-arm", "adapterVersion": "1", "modelId": "generic6", "embodiment": "arm", "joints": []any{}, "endEffectors": []any{}, "sensors": []any{map[string]any{"sourceId": "depth", "sourceType": "rgbd_camera", "frameId": "optical", "transformRevision": "cal1", "maxAgeMs": 1000}}, "actionLimits": map[string]any{}, "tools": []any{"observe_scene", "emergency_stop"}}
	scene := map[string]any{"schemaVersion": "scene.reconstruction.v1", "robotId": "robot-a", "observationId": "capture-a", "sourceId": "depth", "sourceType": "rgbd_camera", "sourceFrameId": "optical", "frameId": "world", "transformRevision": "cal1", "observedAtUnixMs": time.Now().Add(-100 * time.Millisecond).UnixMilli(), "sequence": 1, "units": "m", "entities": []any{map[string]any{"entityId": "cup", "category": "cup", "pose": []any{1, 2, 3, 1, 0, 0, 0}, "confidence": 0.9}, map[string]any{"entityId": "bin", "category": "bin", "pose": []any{2, 3, 4, 1, 0, 0, 0}, "confidence": 0.9}}}
	p, err := structpb.NewStruct(profile)
	if err != nil {
		t.Fatal(err)
	}
	r, err := structpb.NewStruct(scene)
	if err != nil {
		t.Fatal(err)
	}
	service := &strictSceneServer{info: &robotv1.RuntimeInfo{RobotId: "robot-a", Adapter: "test-arm", AdapterVersion: "1", ProtocolVersion: "1.0", RobotProfile: p, Capabilities: []*robotv1.CapabilityInfo{{Name: "observe_scene", Available: true, SafetyLevel: "read_only"}, {Name: "emergency_stop", Available: true, SafetyLevel: "physical_motion"}}}, obs: &robotv1.Observation{ObservationId: "capture-a", WallTimeUnixMs: int64(scene["observedAtUnixMs"].(int64)), Reconstruction: r}}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := grpc.NewServer()
	robotv1.RegisterRobotRuntimeServer(server, service)
	go server.Serve(listener)
	t.Cleanup(server.Stop)
	client, err := New(Config{Address: listener.Addr().String(), DevInsecure: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Close() })
	return client, service
}
func TestStrictTelemetryUsesCanonicalSceneAndCaptureTime(t *testing.T) {
	c, s := strictScene(t)
	s.obs.Entities = []*robotv1.SceneEntity{{EntityId: "spoof", Category: "cup"}}
	snapshot, err := c.Telemetry(t.Context(), "")
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.ObservedAt.UnixMilli() != s.obs.WallTimeUnixMs || len(snapshot.Entities) != 2 || snapshot.Entities[0].EntityID != "cup" || snapshot.RobotProfile == nil || snapshot.Reconstruction == nil {
		t.Fatalf("lost authoritative capture: %+v", snapshot)
	}
}
func TestStrictGroundRejectsStaleAndMissingReconstruction(t *testing.T) {
	for _, which := range []string{"stale", "missing", "wrong robot", "bad quaternion"} {
		t.Run(which, func(t *testing.T) {
			c, s := strictScene(t)
			values := s.obs.Reconstruction.AsMap()
			switch which {
			case "stale":
				values["observedAtUnixMs"] = time.Now().Add(-time.Hour).UnixMilli()
			case "missing":
				s.obs.Reconstruction = nil
			case "wrong robot":
				values["robotId"] = "other"
			case "bad quaternion":
				values["entities"].([]any)[0].(map[string]any)["pose"] = []any{1, 2, 3, 0, 0, 0, 0}
			}
			if which != "missing" {
				s.obs.Reconstruction, _ = structpb.NewStruct(values)
			}
			_, err := c.Ground(t.Context(), manipulation.Intent{Object: manipulation.EntitySelector{Category: "cup"}, Destination: manipulation.EntitySelector{Category: "bin"}})
			if err == nil {
				t.Fatal("unsafe scene reached grounding")
			}
		})
	}
}

func TestStrictGroundUsesReconstructionWithoutLegacyEntities(t *testing.T) {
	c, _ := strictScene(t)
	result, err := c.Ground(t.Context(), manipulation.Intent{Object: manipulation.EntitySelector{Category: "cup"}, Destination: manipulation.EntitySelector{Category: "bin"}})
	if err != nil || result.Object.ID != "cup" || result.Destination.ID != "bin" {
		t.Fatalf("canonical scene cannot be grounded: %+v %v", result, err)
	}
}

func TestStrictClientRejectsModifiedImmutableCapture(t *testing.T) {
	c, s := strictScene(t)
	if _, err := c.Telemetry(t.Context(), ""); err != nil {
		t.Fatal(err)
	}
	values := s.obs.Reconstruction.AsMap()
	values["entities"].([]any)[0].(map[string]any)["pose"] = []any{4, 5, 6, 1, 0, 0, 0}
	s.obs.Reconstruction, _ = structpb.NewStruct(values)
	if _, err := c.Telemetry(t.Context(), ""); err == nil || !strings.Contains(err.Error(), "immutable") {
		t.Fatalf("modified capture accepted: %v", err)
	}
}

func TestStrictClientRejectsRepaintedPointColorsWithUnchangedCaptureSequence(t *testing.T) {
	c, s := strictScene(t)
	values := s.obs.Reconstruction.AsMap()
	values["points"] = []any{[]any{1, 2, 3}}
	values["pointColors"] = []any{[]any{255, 0, 128}}
	var err error
	s.obs.Reconstruction, err = structpb.NewStruct(values)
	if err != nil {
		t.Fatal(err)
	}
	first, err := c.Telemetry(t.Context(), "")
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Reconstruction.PointColors) != 1 || !slices.Equal(first.Reconstruction.PointColors[0], []int{255, 0, 128}) {
		t.Fatalf("lost canonical RGB point colors: %+v", first.Reconstruction)
	}
	values["pointColors"] = []any{[]any{17, 0, 128}}
	s.obs.Reconstruction, err = structpb.NewStruct(values)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.Telemetry(t.Context(), ""); err == nil || !strings.Contains(err.Error(), "immutable") {
		t.Fatalf("same capture accepted different RGB point colors: %v", err)
	}
}

func TestStrictClientRejectsExplicitNullPointColorsAndChannels(t *testing.T) {
	for name, colors := range map[string]any{"null colors": nil, "null row": []any{nil}, "null channel": []any{[]any{nil, 0, 0}}} {
		t.Run(name, func(t *testing.T) {
			c, s := strictScene(t)
			values := s.obs.Reconstruction.AsMap()
			values["points"] = []any{[]any{1, 2, 3}}
			values["pointColors"] = colors
			var err error
			s.obs.Reconstruction, err = structpb.NewStruct(values)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := c.Telemetry(t.Context(), ""); err == nil {
				t.Fatal("explicit null point colors reached telemetry")
			}
		})
	}
}

func TestStrictClientRejectsStopMisclassifiedAsReadOnly(t *testing.T) {
	c, s := strictScene(t)
	s.info.Capabilities[1].SafetyLevel = "read_only"
	if _, err := c.Info(t.Context()); err == nil {
		t.Fatal("misclassified emergency stop admitted")
	}
}
func TestStrictClientRejectsProfileDowngradeAndChangedCapture(t *testing.T) {
	c, s := strictScene(t)
	if _, err := c.Telemetry(t.Context(), ""); err != nil {
		t.Fatal(err)
	}
	values := s.obs.Reconstruction.AsMap()
	values["units"] = "mm"
	s.obs.Reconstruction, _ = structpb.NewStruct(values)
	if _, err := c.Telemetry(t.Context(), ""); err == nil {
		t.Fatal("changed capture accepted")
	}
	s.info.RobotProfile = nil
	if _, err := c.Info(t.Context()); err == nil || !strings.Contains(err.Error(), "profile") {
		t.Fatalf("downgrade accepted: %v", err)
	}
}
