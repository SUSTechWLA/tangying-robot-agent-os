package worker

import (
	"context"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// Semantic grounding is local, but the identity/order/search/verification
// constraints are part of the existing cloud execution digest and fence.
func TestCloudSemanticTargetChangesInvalidateTheExistingHandoff(t *testing.T) {
	for _, change := range []string{"map", "cloud", "semantic", "target", "search", "verification"} {
		t.Run(change, func(t *testing.T) {
			_, _, cloud := policyTask(t)
			r := strings.Repeat("a", 64)
			cloud.task.Intent.SemanticTarget = &manipulation.SemanticTarget{
				Identity:         manipulation.SemanticIdentity{RobotID: "robot-1", MapID: "map", MapRevision: r, CalibrationRevision: r, CloudSHA256: r, FrameID: "map"},
				SemanticRevision: r, AnnotationID: "target-a", Name: "检测区", MaxDistanceM: .65, VerificationMode: "arrival"}
			store := contextFixture(t, cloud)
			target := cloud.task.Intent.SemanticTarget
			switch change {
			case "map":
				target.Identity.MapRevision = strings.Repeat("b", 64)
			case "cloud":
				target.Identity.CloudSHA256 = strings.Repeat("b", 64)
			case "semantic":
				target.SemanticRevision = strings.Repeat("b", 64)
			case "target":
				target.AnnotationID = "target-b"
			case "search":
				target.AllowStaleSearch = true
			case "verification":
				target.VerificationMode = "object"
			}
			robot := &learnedRuntime{}
			worker := New(Config{RobotID: "robot-1", Cloud: cloud, Runtime: robot, ExecutionStore: store})
			if _, err := worker.acceptContext(context.Background(), cloud.task, cloud.node); err == nil {
				t.Fatal("modified semantic target accepted under an old cloud claim")
			}
			if len(robot.commands) != 0 {
				t.Fatal("modified context dispatched hardware")
			}
		})
	}
}
