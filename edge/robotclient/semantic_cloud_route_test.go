package robotclient

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/types/known/structpb"
)

type semanticPlanStub struct {
	robotv1.RobotRuntimeClient
	result   map[string]any
	requests []*robotv1.ServiceRequest
}

func (s *semanticPlanStub) CallService(ctx context.Context, r *robotv1.ServiceRequest, _ ...grpc.CallOption) (*robotv1.ServiceResponse, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	s.requests = append(s.requests, r)
	value, err := structpb.NewStruct(s.result)
	return &robotv1.ServiceResponse{Ok: true, Result: value}, err
}
func semanticCloudFixture() (manipulation.Intent, map[string]any) {
	r := strings.Repeat("a", 64)
	target := &manipulation.SemanticTarget{Identity: manipulation.SemanticIdentity{RobotID: "robot-a", MapID: "map", MapRevision: r, CalibrationRevision: r, CloudSHA256: r, FrameID: "map"},
		SemanticRevision: r, AnnotationID: "desk-a", Name: "检测台", MaxDistanceM: 2.5, VerificationMode: "arrival"}
	wire, _ := json.Marshal(target.Identity)
	var identity map[string]any
	_ = json.Unmarshal(wire, &identity)
	return manipulation.Intent{Action: manipulation.ActionHomeRoute, RobotID: "robot-a", RouteRooms: []string{"检测台"}, SemanticTarget: target},
		map[string]any{"identity": identity, "activeMap": map[string]any{"mapId": "map", "mapRevision": r, "calibrationRevision": r},
			"semanticRevision": r, "annotationId": "desk-a", "frameId": "world", "worldFrameRevision": "world-current",
			"poseObservedAtUnixMs": float64(time.Now().UnixMilli()), "targetFresh": true, "executionBindingSupported": true, "verificationMode": "arrival",
			"candidates": []any{map[string]any{"goalPose": []any{1., 2., .035, 1., 0., 0., 0.}, "geometryVerified": true, "targetDistanceM": .65,
				"pathLengthM": 1.1, "pathSha256": r, "footprintRadiusM": .32, "semanticBinding": "provider-signed-goal"}}}
}

func TestPointCloudGroundingUsesProviderAttestationAndNoModelCoordinates(t *testing.T) {
	intent, result := semanticCloudFixture()
	stub := &semanticPlanStub{result: result}
	client := &Client{robot: stub}
	grounded, err := client.groundSemanticCloud(t.Context(), semanticTestInfo("vendor-mobile"), intent)
	if err != nil {
		t.Fatal(err)
	}
	if grounded.RouteGoals[0][0] != 1 || grounded.SemanticBindings[0] != "provider-signed-goal" || grounded.GoalEvidence["semanticTarget"] != intent.SemanticTarget {
		t.Fatalf("lost provenance: %+v", grounded)
	}
	request := stub.requests[0]
	if request.Name != "semantic.plan" || request.Parameters.AsMap()["annotationId"] != "desk-a" || request.Parameters.AsMap()["goalPose"] != nil {
		t.Fatalf("unsafe request: %+v", request)
	}
}

func TestPointCloudGroundingRejectsChangedIdentityAndUnsafeCandidates(t *testing.T) {
	for _, name := range []string{"robot", "cloud", "map", "semantic", "annotation", "frame", "world frame", "unsupported provider", "old pose", "future pose", "stale target", "verification mode", "unattested", "no geometry", "workspace", "bad pose", "excess distance", "implicit return"} {
		t.Run(name, func(t *testing.T) {
			intent, result := semanticCloudFixture()
			candidate := result["candidates"].([]any)[0].(map[string]any)
			switch name {
			case "robot":
				result["identity"].(map[string]any)["robotId"] = "other"
			case "cloud":
				result["identity"].(map[string]any)["cloudSha256"] = strings.Repeat("b", 64)
			case "map":
				result["activeMap"].(map[string]any)["mapId"] = "other"
			case "semantic":
				result["semanticRevision"] = strings.Repeat("b", 64)
			case "annotation":
				result["annotationId"] = "other"
			case "frame":
				result["frameId"] = "map"
			case "world frame":
				result["worldFrameRevision"] = ""
			case "unsupported provider":
				result["executionBindingSupported"] = false
			case "old pose":
				result["poseObservedAtUnixMs"] = float64(time.Now().Add(-3 * time.Second).UnixMilli())
			case "future pose":
				result["poseObservedAtUnixMs"] = float64(time.Now().Add(time.Second).UnixMilli())
			case "stale target":
				result["targetFresh"] = false
			case "verification mode":
				result["verificationMode"] = "object"
			case "unattested":
				delete(candidate, "semanticBinding")
			case "no geometry":
				candidate["geometryVerified"] = false
			case "workspace":
				candidate["goalPose"].([]any)[0] = 500.
			case "bad pose":
				candidate["goalPose"].([]any)[3] = 2.
			case "excess distance":
				candidate["targetDistanceM"] = 3.
			case "implicit return":
				intent.ReturnToStart = true
			}
			client := &Client{robot: &semanticPlanStub{result: result}}
			if _, err := client.groundSemanticCloud(t.Context(), semanticTestInfo("custom"), intent); err == nil {
				t.Fatal("unsafe plan became an executable goal")
			}
		})
	}
}

func TestPointCloudGroundingRefreshesEachLegAndKeepsStaleSearchTruth(t *testing.T) {
	intent, result := semanticCloudFixture()
	intent.SemanticTarget.AllowStaleSearch = true
	result["targetFresh"] = false
	stub := &semanticPlanStub{result: result}
	client := &Client{robot: stub}
	for i := 0; i < 2; i++ {
		result["candidates"].([]any)[0].(map[string]any)["goalPose"].([]any)[0] = float64(i)
		grounded, err := client.groundSemanticCloud(t.Context(), semanticTestInfo("custom"), intent)
		if err != nil || grounded.RouteGoals[0][0] != float64(i) || grounded.GoalEvidence["targetFresh"] != false {
			t.Fatalf("grounded=%+v err=%v", grounded, err)
		}
	}
	if len(stub.requests) != 2 {
		t.Fatal("later stage reused an earlier plan")
	}
}
