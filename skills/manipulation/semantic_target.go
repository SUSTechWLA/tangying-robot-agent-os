package manipulation

import (
	"encoding/hex"
	"fmt"
	"math"
	"strings"
)

func (t *SemanticTarget) Validate(robotID string) error {
	if t == nil || t.Identity.RobotID != robotID || robotID == "" || t.Identity.FrameID != "map" {
		return fmt.Errorf("semantic target robot/frame identity mismatch")
	}
	for _, value := range []string{t.Identity.MapID, t.AnnotationID, t.Name} {
		if strings.TrimSpace(value) == "" || len(value) > 512 {
			return fmt.Errorf("invalid semantic target name or id")
		}
	}
	for _, value := range []string{t.Identity.MapRevision, t.Identity.CalibrationRevision, t.Identity.CloudSHA256, t.SemanticRevision} {
		if decoded, err := hex.DecodeString(value); err != nil || len(decoded) != 32 {
			return fmt.Errorf("invalid semantic content revision")
		}
	}
	if math.IsNaN(t.MaxDistanceM) || math.IsInf(t.MaxDistanceM, 0) || t.MaxDistanceM < .5 || t.MaxDistanceM > 5 {
		return fmt.Errorf("semantic approach distance outside bounds")
	}
	if t.VerificationMode != "arrival" && t.VerificationMode != "object" {
		return fmt.Errorf("unsupported semantic verification mode")
	}
	return nil
}
