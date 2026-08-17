package manipulation_test

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestPlanRequiresApprovalForPhysicalSteps(t *testing.T) {
	plan := manipulation.Plan(manipulation.GroundedTask{
		TaskID:      "task-1",
		Object:      manipulation.SceneRef{ID: "cup-1", Confidence: 0.95},
		Destination: manipulation.SceneRef{ID: "bin-1", Confidence: 0.96},
	}, time.Now().Add(time.Minute))
	for _, step := range plan.Steps {
		if step.Skill == "manipulation.pick" || step.Skill == "manipulation.place" {
			if step.ApprovalID == "" || step.LeaseMS == 0 || step.IdempotencyKey == "" {
				t.Fatalf("physical step missing controls: %+v", step)
			}
		}
	}
}
