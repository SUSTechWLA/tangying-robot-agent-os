package skills_test

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
)

func TestPhysicalSkillRequiresLeaseAndSafetyProfile(t *testing.T) {
	m := skills.SkillManifest{Name: "manipulation.pick", SideEffect: true, SafetyLevel: skills.SafetyPhysical}
	if err := m.Validate(); err == nil {
		t.Fatal("physical skill without lease and safety profiles must be invalid")
	}
	m.DefaultLeaseMS = 15_000
	m.AllowedSafetyProfiles = []string{"desktop_standard"}
	if err := m.Validate(); err != nil {
		t.Fatalf("valid physical manifest rejected: %v", err)
	}
}
