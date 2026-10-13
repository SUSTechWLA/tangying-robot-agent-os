package agent

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func verifySemanticResult(command runtime.Command, result runtime.Result, grounded manipulation.GroundedTask, navigationID string, dispatched time.Time) error {
	fail := func(reason string) error {
		return fmt.Errorf("%w: semantic evidence %s", ErrVerificationFailed, reason)
	}
	token, ok := command.Parameters["semanticBinding"].(string)
	if !ok || token == "" || navigationID == "" {
		return fail("has no command binding")
	}
	var target manipulation.SemanticTarget
	wire, err := json.Marshal(grounded.GoalEvidence["semanticTarget"])
	if err != nil || json.Unmarshal(wire, &target) != nil || target.Validate(command.RobotID) != nil {
		return fail("has no approved target")
	}
	worldFrame, ok := grounded.GoalEvidence["worldFrameRevision"].(string)
	if !ok || worldFrame == "" {
		return fail("has no frozen world frame")
	}
	if result.Evidence == nil || result.Evidence.RobotID != command.RobotID || result.Evidence.Reconstruction == nil ||
		result.ObservationID == "" || result.ObservationID != result.Evidence.Reconstruction.ObservationID ||
		result.Evidence.ObservedAt.UnixMilli() < dispatched.UnixMilli() {
		return fail("requires the command's fresh robot capture")
	}
	tokenSHA := fmt.Sprintf("%x", sha256.Sum256([]byte(token)))
	checkExecution := func(value any) bool {
		m, ok := value.(map[string]any)
		if !ok {
			return false
		}
		var identity manipulation.SemanticIdentity
		wire, err := json.Marshal(m["identity"])
		if err != nil || json.Unmarshal(wire, &identity) != nil || identity != target.Identity {
			return false
		}
		fresh, hasFresh := m["targetFresh"].(bool)
		return m["semanticRevision"] == target.SemanticRevision && m["annotationId"] == target.AnnotationID &&
			m["worldFrameRevision"] == worldFrame && m["verificationMode"] == target.VerificationMode &&
			m["allowStaleSearch"] == target.AllowStaleSearch && hasFresh && (fresh || target.AllowStaleSearch)
	}
	state := result.Evidence.RobotState
	route, ok := state["map_route"].(map[string]any)
	if !ok || route["commandId"] != navigationID || route["semanticBindingSha256"] != tokenSHA ||
		capability.Fingerprint(route["goalPose"]) != capability.Fingerprint(command.Parameters["goalPose"]) || !checkExecution(route["semanticExecution"]) {
		return fail("navigation receipt differs from the dispatched target")
	}
	if command.Capability != "verify_arrival" {
		return nil
	}
	verification, ok := state["semantic_verification"].(map[string]any)
	if !ok || verification["commandId"] != command.CommandID || verification["navigationCommandId"] != navigationID ||
		verification["semanticBindingSha256"] != tokenSHA || verification["arrivalVerified"] != true || !checkExecution(verification["semanticExecution"]) {
		return fail("arrival receipt differs from the dispatched target")
	}
	freshCapture := func(raw any) bool {
		stamp, ok := semanticNumber(raw)
		return ok && math.Trunc(stamp) == stamp && stamp >= float64(dispatched.UnixMilli()) &&
			stamp <= float64(result.Evidence.ObservedAt.UnixMilli()+250) && stamp <= float64(time.Now().UnixMilli()+250)
	}
	if !freshCapture(verification["freshCaptureAtUnixMs"]) {
		return fail("arrival capture predates dispatch")
	}
	if target.VerificationMode == "object" {
		observationEvidence, ok := verification["observationEvidence"].(map[string]any)
		if !ok {
			return fail("has no bounded detector evidence")
		}
		count, _ := semanticNumber(observationEvidence["matchCount"])
		observation, _ := verification["observationId"].(string)
		source, _ := verification["sourceId"].(string)
		hash, _ := observationEvidence["evidenceHash"].(string)
		digest, hashErr := hex.DecodeString(hash)
		var observedIdentity manipulation.SemanticIdentity
		identityWire, _ := json.Marshal(observationEvidence["identity"])
		_ = json.Unmarshal(identityWire, &observedIdentity)
		matches, _ := observationEvidence["matches"].([]any)
		if verification["semanticConfirmed"] != true || observationEvidence["semanticConfirmed"] != true ||
			count != 1 || len(matches) != 1 || observationEvidence["matchesTruncated"] != false || observation == "" || source == "" ||
			hashErr != nil || len(digest) != 32 || observedIdentity != target.Identity || observationEvidence["annotationId"] != target.AnnotationID ||
			observationEvidence["semanticRevision"] != target.SemanticRevision || observationEvidence["worldFrameRevision"] != worldFrame ||
			observationEvidence["observationId"] != observation || observationEvidence["sourceId"] != source ||
			!freshCapture(verification["observedAtUnixMs"]) || !freshCapture(observationEvidence["observedAtUnixMs"]) {
			return fail("does not establish one freshly observed object")
		}
	} else if verification["semanticConfirmed"] != false {
		return fail("arrival-only mode cannot claim object recognition")
	}
	return nil
}

func semanticNumber(value any) (float64, bool) {
	var number float64
	switch v := value.(type) {
	case float64:
		number = v
	case int64:
		number = float64(v)
	case int:
		number = float64(v)
	default:
		return 0, false
	}
	return number, !math.IsNaN(number) && !math.IsInf(number, 0)
}
