package runtime

import (
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
)

func TestProfiledMobilityReadinessDoesNotRequireManipulation(t *testing.T) {
	base := func() Snapshot {
		return Snapshot{RobotProfile: &robotcontract.Profile{Tools: []string{"navigation.navigate", "verify_arrival", "emergency_stop"}},
			Capabilities: []Capability{{Name: "navigation.navigate", SafetyLevel: "physical_motion", Available: true}}}
	}
	for _, tc := range []struct {
		name   string
		change func(*Snapshot)
		ready  bool
	}{
		{"navigation_without_pick_or_place", func(*Snapshot) {}, true},
		{"legacy_without_ready", func(s *Snapshot) { s.RobotProfile = nil }, false},
		{"global_driver_fault", func(s *Snapshot) { s.Blockers = []string{"ROBOT_NOT_ARMED"} }, false},
		{"unavailable", func(s *Snapshot) { s.Capabilities[0].Available = false }, false},
		{"per_tool_fault", func(s *Snapshot) { s.Capabilities[0].Blockers = []string{"LOCALIZATION_LOST"} }, false},
		{"flat_legacy_descriptor", func(s *Snapshot) { s.Capabilities[0].SafetyLevel = "" }, false},
		{"read_only", func(s *Snapshot) { s.Capabilities[0].SafetyLevel = "read_only" }, false},
		{"undeclared_tool", func(s *Snapshot) { s.RobotProfile.Tools = []string{"observe_scene"} }, false},
		{"stop_does_not_enable_motion", func(s *Snapshot) { s.Capabilities[0].Name = "emergency_stop" }, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := base()
			tc.change(&s)
			if s.PhysicalReady() != tc.ready {
				t.Fatalf("readiness = %v, want %v", s.PhysicalReady(), tc.ready)
			}
			if err := s.CanExecute("manipulation.pick"); !errors.Is(err, ErrCapabilityUnknown) {
				t.Fatalf("undeclared pick: %v", err)
			}
		})
	}
}

func TestCapabilityBlockerCannotBeOverriddenByAvailableFlag(t *testing.T) {
	s := Snapshot{Ready: true, Capabilities: []Capability{{Name: "navigation.navigate", Available: true, Blockers: []string{"LOCALIZATION_LOST"}}}}
	if err := s.CanExecute("navigation.navigate"); !errors.Is(err, ErrCapabilityUnavailable) {
		t.Fatalf("conflicting readiness accepted: %v", err)
	}
}
