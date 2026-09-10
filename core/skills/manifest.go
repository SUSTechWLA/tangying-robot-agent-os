package skills

import (
	"errors"
	"fmt"
)

type SafetyLevel string

const (
	SafetyReadOnly SafetyLevel = "read_only"
	SafetyLocal    SafetyLevel = "local_side_effect"
	SafetyPhysical SafetyLevel = "physical_motion"
)

type ApprovalPolicy struct {
	Required bool `json:"required"`
}

type SkillManifest struct {
	Name                  string         `json:"name"`
	Description           string         `json:"description"`
	Capabilities          []string       `json:"capabilities,omitempty"`
	RequiredParameters    []string       `json:"requiredParameters,omitempty"`
	SideEffect            bool           `json:"sideEffect"`
	SafetyLevel           SafetyLevel    `json:"safetyLevel"`
	DefaultLeaseMS        uint32         `json:"defaultLeaseMs,omitempty"`
	AllowedSafetyProfiles []string       `json:"allowedSafetyProfiles,omitempty"`
	ApprovalPolicy        ApprovalPolicy `json:"approvalPolicy"`
	// MutatesWorld marks a tool that leaves the physical world in a new state
	// which must be confirmed from a fresh post-command observation. A
	// successful return code is a trigger to observe, never completion proof.
	// The Agent refuses to record such a step as done without fresh evidence.
	MutatesWorld bool              `json:"mutatesWorld,omitempty"`
	Metadata     map[string]string `json:"metadata,omitempty"`
}

type CapabilityManifest struct {
	DeviceID        string   `json:"deviceId"`
	Adapter         string   `json:"adapter"`
	Skills          []string `json:"skills"`
	SoftwareVersion string   `json:"softwareVersion"`
	Ready           bool     `json:"ready"`
	Blockers        []string `json:"blockers,omitempty"`
}

func (m SkillManifest) Validate() error {
	if m.Name == "" {
		return errors.New("skill name is required")
	}
	if m.SafetyLevel == SafetyPhysical {
		if !m.SideEffect {
			return errors.New("physical skill must be marked as side effect")
		}
		if m.DefaultLeaseMS == 0 {
			return errors.New("physical skill requires a default lease")
		}
		if len(m.AllowedSafetyProfiles) == 0 {
			return errors.New("physical skill requires an allowed safety profile")
		}
	}
	if m.SideEffect && m.SafetyLevel == SafetyReadOnly {
		return fmt.Errorf("side-effect skill %s cannot be read-only", m.Name)
	}
	// A world mutation without a side effect is a contradiction: the caller
	// would be told nothing needs confirming while the closure gate waits for
	// evidence that can never arrive.
	if m.MutatesWorld && !m.SideEffect {
		return fmt.Errorf("world-mutating skill %s must be marked as side effect", m.Name)
	}
	return nil
}
