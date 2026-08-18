package guard

import (
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

var (
	ErrUnknownSkill          = errors.New("unknown skill")
	ErrPhysicalLeaseRequired = errors.New("physical skill requires lease")
	ErrPhysicalDeadline      = errors.New("physical skill deadline invalid")
	ErrApprovalRequired      = errors.New("physical skill requires approval")
	ErrMissingParameter      = errors.New("skill is missing a required parameter")
	ErrGroundingConfidence   = errors.New("grounding confidence below threshold")
	ErrBudgetExceeded        = errors.New("plan budget exceeded")
)

type Guard struct {
	catalog       map[string]skills.SkillManifest
	minConfidence float64
	now           func() time.Time
}

func New(catalog []skills.SkillManifest) *Guard {
	indexed := make(map[string]skills.SkillManifest, len(catalog))
	for _, manifest := range catalog {
		indexed[manifest.Name] = manifest
	}
	return &Guard{catalog: indexed, minConfidence: 0.7, now: time.Now}
}

func (g *Guard) Validate(plan taskgraph.TaskPlan) error {
	if err := plan.ValidateShape(); err != nil {
		return err
	}
	if plan.Budget.MaxSteps > 0 && len(plan.Steps) > plan.Budget.MaxSteps {
		return fmt.Errorf("%w: %d > %d", ErrBudgetExceeded, len(plan.Steps), plan.Budget.MaxSteps)
	}
	for _, step := range plan.Steps {
		manifest, ok := g.catalog[step.Skill]
		if !ok {
			return fmt.Errorf("%w: %s", ErrUnknownSkill, step.Skill)
		}
		for _, required := range manifest.RequiredParameters {
			if _, ok := step.Arguments[required]; !ok {
				return fmt.Errorf("%w: %s.%s", ErrMissingParameter, step.Skill, required)
			}
		}
		if manifest.SafetyLevel == skills.SafetyPhysical {
			if step.LeaseMS == 0 {
				return fmt.Errorf("%w: %s", ErrPhysicalLeaseRequired, step.ID)
			}
			if step.DeadlineUnixMS <= g.now().UnixMilli() {
				return fmt.Errorf("%w: %s", ErrPhysicalDeadline, step.ID)
			}
			if manifest.ApprovalPolicy.Required && step.ApprovalID == "" {
				return fmt.Errorf("%w: %s", ErrApprovalRequired, step.ID)
			}
			if step.IdempotencyKey == "" {
				return fmt.Errorf("physical step %s requires idempotency key", step.ID)
			}
		}
		if step.Skill == "resolve_targets" {
			if confidence(step.Arguments["objectConfidence"]) < g.minConfidence ||
				confidence(step.Arguments["destinationConfidence"]) < g.minConfidence {
				return ErrGroundingConfidence
			}
		}
	}
	return nil
}

func confidence(value any) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case float32:
		return float64(typed)
	default:
		return 0
	}
}
