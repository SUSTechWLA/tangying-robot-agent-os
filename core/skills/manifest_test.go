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

// TestWorldMutationRequiresSideEffectDeclaration pins the contradiction that must
// fail at load time rather than at run time: a tool that writes the world while
// claiming it has no side effect would tell the caller nothing needs confirming,
// while the closure gate waits for evidence that can never arrive.
func TestWorldMutationRequiresSideEffectDeclaration(t *testing.T) {
	m := skills.SkillManifest{
		Name:                  "manipulation.pick",
		MutatesWorld:          true,
		SideEffect:            false,
		SafetyLevel:           skills.SafetyPhysical,
		DefaultLeaseMS:        15_000,
		AllowedSafetyProfiles: []string{"desktop_standard"},
	}
	if err := m.Validate(); err == nil {
		t.Fatal("world-mutating skill without a side effect must be invalid")
	}
	m.SideEffect = true
	if err := m.Validate(); err != nil {
		t.Fatalf("world-mutating skill with a side effect rejected: %v", err)
	}
}
