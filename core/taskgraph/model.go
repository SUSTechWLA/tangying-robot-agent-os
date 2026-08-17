package taskgraph

import (
	"errors"
	"fmt"
)

var (
	ErrDuplicateStepID   = errors.New("duplicate step id")
	ErrInvalidDependency = errors.New("invalid dependency")
	ErrMissingStepID     = errors.New("missing step id")
	ErrMissingSkill      = errors.New("missing skill")
)

type TaskPlan struct {
	ID         string      `json:"id"`
	Goal       string      `json:"goal"`
	Domain     string      `json:"domain"`
	Revision   uint64      `json:"revision"`
	Steps      []SkillStep `json:"steps"`
	Budget     Budget      `json:"budget"`
	StopPolicy StopPolicy  `json:"stopPolicy"`
}

type SkillStep struct {
	ID             string         `json:"id"`
	Skill          string         `json:"skill"`
	Arguments      map[string]any `json:"arguments,omitempty"`
	DependsOn      []string       `json:"dependsOn,omitempty"`
	ExpectedOutput []string       `json:"expectedOutput,omitempty"`
	SafetyLevel    string         `json:"safetyLevel,omitempty"`
	ApprovalID     string         `json:"approvalId,omitempty"`
	DeadlineUnixMS int64          `json:"deadlineUnixMs,omitempty"`
	LeaseMS        uint32         `json:"leaseMs,omitempty"`
	IdempotencyKey string         `json:"idempotencyKey,omitempty"`
}

type Budget struct {
	MaxSteps     int    `json:"maxSteps,omitempty"`
	MaxRetries   int    `json:"maxRetries,omitempty"`
	MaxCostLevel string `json:"maxCostLevel,omitempty"`
}

type StopPolicy struct {
	StopWhenEnough bool `json:"stopWhenEnough,omitempty"`
	StopOnSafety   bool `json:"stopOnSafety"`
}

func (p TaskPlan) ValidateShape() error {
	seen := make(map[string]struct{}, len(p.Steps))
	for _, step := range p.Steps {
		if step.ID == "" {
			return ErrMissingStepID
		}
		if _, ok := seen[step.ID]; ok {
			return fmt.Errorf("%w: %s", ErrDuplicateStepID, step.ID)
		}
		for _, dependency := range step.DependsOn {
			if _, ok := seen[dependency]; !ok {
				return fmt.Errorf("%w: step %s depends on unknown or later step %s", ErrInvalidDependency, step.ID, dependency)
			}
		}
		seen[step.ID] = struct{}{}
	}
	return nil
}
