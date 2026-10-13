package agent

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func semanticReceiptFixture(mode string) (runtime.Command, runtime.Result, manipulation.GroundedTask, time.Time) {
	now := time.Now()
	r := strings.Repeat("a", 64)
	target := &manipulation.SemanticTarget{Identity: manipulation.SemanticIdentity{RobotID: "robot-a", MapID: "map", MapRevision: r, CalibrationRevision: r, CloudSHA256: r, FrameID: "map"},
		SemanticRevision: r, AnnotationID: "target-a", Name: "检测台", MaxDistanceM: 2, VerificationMode: mode}
	wire, _ := json.Marshal(target.Identity)
	var identity map[string]any
	_ = json.Unmarshal(wire, &identity)
	pose := []float64{1, 2, 0, 1, 0, 0, 0}
	command := runtime.Command{CommandID: "task/verify", RobotID: "robot-a", Capability: "verify_arrival", Parameters: map[string]any{"goalPose": pose, "semanticBinding": "signed-token"}}
	hash := fmt.Sprintf("%x", sha256.Sum256([]byte("signed-token")))
	execution := map[string]any{"identity": identity, "semanticRevision": r, "annotationId": "target-a", "worldFrameRevision": "world-a",
		"verificationMode": mode, "allowStaleSearch": false, "targetFresh": true}
	route := map[string]any{"commandId": "task/navigate", "semanticBindingSha256": hash, "semanticExecution": execution, "goalPose": pose}
	verification := map[string]any{"commandId": command.CommandID, "navigationCommandId": "task/navigate", "semanticBindingSha256": hash,
		"semanticExecution": execution, "arrivalVerified": true, "freshCaptureAtUnixMs": now.UnixMilli(), "semanticConfirmed": false}
	if mode == "object" {
		verification["semanticConfirmed"], verification["observationId"], verification["sourceId"], verification["observedAtUnixMs"] = true, "detector-capture", "head-rgbd", now.UnixMilli()
		verification["observationEvidence"] = map[string]any{"identity": identity, "semanticRevision": r, "annotationId": "target-a", "worldFrameRevision": "world-a",
			"semanticConfirmed": true, "observationId": "detector-capture", "sourceId": "head-rgbd", "observedAtUnixMs": now.UnixMilli(),
			"matchCount": 1, "matches": []any{map[string]any{"entityId": "seen-cup", "category": "cup"}}, "matchesTruncated": false, "evidenceHash": r}
	}
	result := runtime.Result{Success: true, ObservationID: "capture", VerificationConfidence: 1,
		Evidence: &telemetry.Snapshot{RobotID: "robot-a", ObservedAt: now,
			Reconstruction: &robotcontract.Reconstruction{ObservationID: "capture"},
			RobotState:     map[string]any{"map_route": route, "semantic_verification": verification}}}
	grounded := manipulation.GroundedTask{GoalEvidence: map[string]any{"semanticTarget": target, "worldFrameRevision": "world-a"}}
	return command, result, grounded, now.Add(-time.Millisecond)
}

func TestSemanticArrivalRequiresExactOriginalCommandEvidence(t *testing.T) {
	for _, change := range []string{"none", "missing capture", "wrong robot", "old capture", "capture id", "token", "navigation command", "verification command", "different goal", "map", "semantic revision", "world frame", "stale target", "arrival false", "old frame", "invented recognition"} {
		t.Run(change, func(t *testing.T) {
			command, result, grounded, dispatch := semanticReceiptFixture("arrival")
			route := result.Evidence.RobotState["map_route"].(map[string]any)
			verification := result.Evidence.RobotState["semantic_verification"].(map[string]any)
			execution := route["semanticExecution"].(map[string]any)
			switch change {
			case "missing capture":
				result.Evidence = nil
			case "wrong robot":
				result.Evidence.RobotID = "other"
			case "old capture":
				result.Evidence.ObservedAt = dispatch.Add(-time.Millisecond)
			case "capture id":
				result.ObservationID = "different"
			case "token":
				verification["semanticBindingSha256"] = "forged"
			case "navigation command":
				verification["navigationCommandId"] = "other-command"
			case "verification command":
				verification["commandId"] = "other-command"
			case "different goal":
				route["goalPose"] = []float64{2, 2, 0, 1, 0, 0, 0}
			case "map":
				execution["identity"].(map[string]any)["mapId"] = "other-map"
			case "semantic revision":
				execution["semanticRevision"] = strings.Repeat("b", 64)
			case "world frame":
				execution["worldFrameRevision"] = "old-world"
			case "stale target":
				execution["targetFresh"] = false
			case "arrival false":
				verification["arrivalVerified"] = false
			case "old frame":
				verification["freshCaptureAtUnixMs"] = dispatch.Add(-time.Millisecond).UnixMilli()
			case "invented recognition":
				verification["semanticConfirmed"] = true
			}
			err := verifySemanticResult(command, result, grounded, "task/navigate", dispatch)
			if (err == nil) != (change == "none") {
				t.Fatalf("change=%s error=%v", change, err)
			}
		})
	}
}

func TestSemanticObjectRequiresUniqueFreshBoundDetectorEvidence(t *testing.T) {
	for _, change := range []string{"none", "missing", "false", "multiple", "old", "wrong target", "wrong observation", "wrong source", "bad digest"} {
		t.Run(change, func(t *testing.T) {
			command, result, grounded, dispatch := semanticReceiptFixture("object")
			verification := result.Evidence.RobotState["semantic_verification"].(map[string]any)
			observation := verification["observationEvidence"].(map[string]any)
			switch change {
			case "missing":
				delete(verification, "observationEvidence")
			case "false":
				observation["semanticConfirmed"] = false
			case "multiple":
				observation["matchCount"] = 2
			case "old":
				observation["observedAtUnixMs"] = dispatch.Add(-time.Millisecond).UnixMilli()
			case "wrong target":
				observation["annotationId"] = "other-target"
			case "wrong observation":
				verification["observationId"] = "other-capture"
			case "wrong source":
				verification["sourceId"] = "other-source"
			case "bad digest":
				observation["evidenceHash"] = "not-a-digest"
			}
			err := verifySemanticResult(command, result, grounded, "task/navigate", dispatch)
			if (err == nil) != (change == "none") {
				t.Fatalf("change=%s error=%v", change, err)
			}
		})
	}
}
