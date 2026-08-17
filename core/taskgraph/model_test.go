package taskgraph_test

import (
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

func TestTaskPlanRejectsDuplicateStepIDs(t *testing.T) {
	plan := taskgraph.TaskPlan{Steps: []taskgraph.SkillStep{{ID: "observe"}, {ID: "observe"}}}
	if err := plan.ValidateShape(); !errors.Is(err, taskgraph.ErrDuplicateStepID) {
		t.Fatalf("ValidateShape() error = %v", err)
	}
}

func TestTaskPlanRejectsLaterDependency(t *testing.T) {
	plan := taskgraph.TaskPlan{Steps: []taskgraph.SkillStep{
		{ID: "pick", DependsOn: []string{"observe"}},
		{ID: "observe"},
	}}
	if err := plan.ValidateShape(); !errors.Is(err, taskgraph.ErrInvalidDependency) {
		t.Fatalf("ValidateShape() error = %v", err)
	}
}
