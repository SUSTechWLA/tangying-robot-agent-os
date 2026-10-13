package robotclient

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"google.golang.org/protobuf/types/known/structpb"
)

// The edge resolves an approved semantic identity, never a language-model
// coordinate. Each single-target intent obtains a new localized provider plan.
func (c *Client) groundSemanticCloud(ctx context.Context, info runtime.Snapshot, intent manipulation.Intent) (manipulation.GroundedTask, error) {
	target := intent.SemanticTarget
	if err := target.Validate(info.RobotID); err != nil {
		return manipulation.GroundedTask{}, err
	}
	if intent.Action != manipulation.ActionHomeRoute || intent.RobotID != info.RobotID || intent.ReturnToStart || len(intent.RouteRooms) != 1 || intent.RouteRooms[0] != target.Name {
		return manipulation.GroundedTask{}, errors.New("semantic checkpoint must contain exactly its approved target and no implicit return")
	}
	parameters, _ := structpb.NewStruct(map[string]any{"mapRevision": target.Identity.MapRevision,
		"semanticRevision": target.SemanticRevision, "annotationId": target.AnnotationID,
		"maxDistanceM": target.MaxDistanceM, "allowStaleSearch": target.AllowStaleSearch, "verificationMode": target.VerificationMode})
	readCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	planStarted := time.Now()
	reply, err := c.CallService(readCtx, &robotv1.ServiceRequest{RobotId: info.RobotID, Name: "semantic.plan", Parameters: parameters})
	if err != nil {
		return manipulation.GroundedTask{}, fmt.Errorf("semantic plan: %w", err)
	}
	if reply == nil || !reply.Ok || reply.Result == nil {
		if reply != nil {
			return manipulation.GroundedTask{}, fmt.Errorf("semantic plan rejected: %s %s", reply.Code, reply.Message)
		}
		return manipulation.GroundedTask{}, errors.New("semantic plan returned no result")
	}
	result := reply.Result.AsMap()
	identityWire, _ := json.Marshal(target.Identity)
	var identity map[string]any
	_ = json.Unmarshal(identityWire, &identity)
	active := map[string]any{"mapId": target.Identity.MapID, "mapRevision": target.Identity.MapRevision, "calibrationRevision": target.Identity.CalibrationRevision}
	if capability.Fingerprint(result["identity"]) != capability.Fingerprint(identity) ||
		capability.Fingerprint(result["activeMap"]) != capability.Fingerprint(active) ||
		result["frameId"] != "world" || result["semanticRevision"] != target.SemanticRevision ||
		result["annotationId"] != target.AnnotationID || result["verificationMode"] != target.VerificationMode {
		return manipulation.GroundedTask{}, errors.New("semantic plan differs from the approved map/annotation binding")
	}
	worldFrame, ok := result["worldFrameRevision"].(string)
	if !ok || strings.TrimSpace(worldFrame) == "" || len(worldFrame) > 256 || result["executionBindingSupported"] != true {
		return manipulation.GroundedTask{}, errors.New("provider cannot attest executable semantic goals in a world frame")
	}
	fresh, ok := result["targetFresh"].(bool)
	if !ok || (!fresh && !target.AllowStaleSearch) {
		return manipulation.GroundedTask{}, errors.New("semantic target stale without approved search mode")
	}
	stamp, ok := result["poseObservedAtUnixMs"].(float64)
	age := float64(time.Now().UnixMilli()) - stamp
	if !ok || math.IsNaN(stamp) || math.IsInf(stamp, 0) || math.Trunc(stamp) != stamp || age < 0 || age > 2000 {
		return manipulation.GroundedTask{}, fmt.Errorf("semantic plan requires fresh localization (poseAgeMs=%.0f, planElapsedMs=%d)", age, time.Since(planStarted).Milliseconds())
	}
	candidates, ok := result["candidates"].([]any)
	if !ok || len(candidates) == 0 || len(candidates) > 4 {
		return manipulation.GroundedTask{}, errors.New("semantic plan has no bounded candidates")
	}
	for _, raw := range candidates {
		candidate, ok := raw.(map[string]any)
		if !ok || candidate["geometryVerified"] != true {
			continue
		}
		pose, err := semanticPose(candidate["goalPose"])
		if err != nil || withinWorkspace(info, pose) != nil {
			continue
		}
		token, ok := candidate["semanticBinding"].(string)
		if !ok || token == "" || len(token) > 2048 {
			continue
		}
		distance, ok := candidate["targetDistanceM"].(float64)
		if !ok || math.IsNaN(distance) || math.IsInf(distance, 0) || distance < 0 || distance > target.MaxDistanceM+1e-6 {
			continue
		}
		return manipulation.GroundedTask{Action: manipulation.ActionHomeRoute, RobotID: info.RobotID,
			RouteRooms: []string{target.Name}, RouteGoals: [][]float64{pose}, SemanticBindings: []string{token},
			GoalEvidence: map[string]any{"goalSource": "versioned_semantic_cloud", "semanticTarget": target,
				"worldFrameRevision": worldFrame, "poseObservedAtUnixMs": stamp, "targetFresh": fresh,
				"verificationMode": target.VerificationMode, "candidate": candidate}}, nil
	}
	return manipulation.GroundedTask{}, errors.New("semantic plan contains no attested candidate within the robot workspace")
}
