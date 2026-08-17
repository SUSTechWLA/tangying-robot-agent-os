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
	Name                  string            `json:"name"`
	Description           string            `json:"description"`
	Capabilities          []string          `json:"capabilities,omitempty"`
	RequiredParameters    []string          `json:"requiredParameters,omitempty"`
	SideEffect            bool              `json:"sideEffect"`
	SafetyLevel           SafetyLevel       `json:"safetyLevel"`
	DefaultLeaseMS        uint32            `json:"defaultLeaseMs,omitempty"`
	AllowedSafetyProfiles []string          `json:"allowedSafetyProfiles,omitempty"`
	ApprovalPolicy        ApprovalPolicy    `json:"approvalPolicy"`
	Metadata              map[string]string `json:"metadata,omitempty"`
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
	return nil
}
