package guard_test

import (
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/guard"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestGuardRejectsPhysicalSkillWithoutLease(t *testing.T) {
	plan := manipulation.Plan(validGroundedTask(), time.Now().Add(time.Minute))
	for i := range plan.Steps {
		if plan.Steps[i].Skill == "manipulation.pick" {
			plan.Steps[i].LeaseMS = 0
		}
	}
	err := guard.New(manipulation.Catalog()).Validate(plan)
	if !errors.Is(err, guard.ErrPhysicalLeaseRequired) {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestGuardRejectsLowGroundingConfidence(t *testing.T) {
	grounded := validGroundedTask()
	grounded.Object.Confidence = 0.49
	plan := manipulation.Plan(grounded, time.Now().Add(time.Minute))
	err := guard.New(manipulation.Catalog()).Validate(plan)
	if !errors.Is(err, guard.ErrGroundingConfidence) {
		t.Fatalf("Validate() error = %v", err)
	}
}

func validGroundedTask() manipulation.GroundedTask {
	return manipulation.GroundedTask{
		TaskID:      "task-1",
		Object:      manipulation.SceneRef{ID: "cup-1", Confidence: 0.95},
		Destination: manipulation.SceneRef{ID: "bin-1", Confidence: 0.96},
		KeepUpright: true,
	}
}
