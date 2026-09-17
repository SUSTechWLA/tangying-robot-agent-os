package closedloop_test

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
)

// The tests that remain are the ones about the classification table: which class
// a real runtime failure code gets, and whether that class permits a retry. The
// state-machine tests that used to live here covered a retry budget nothing
// drove, and went with it — the production evidence gate is covered by
// gate_test.go, and the rule that an unknown outcome is never retried is covered
// where it runs: the observer's classifier and the recovery agent's
// reconciliation rule.

func TestClassifyAssignsTheExpectedClassForRealRuntimeCodes(t *testing.T) {
	for code, want := range map[string]closedloop.Class{
		"EXECUTION_OUTCOME_UNKNOWN":   closedloop.UnknownOutcome,
		"RUNTIME_JOURNAL_UNAVAILABLE": closedloop.UnknownOutcome,
		"NAV_VELOCITY_STALE":          closedloop.Transient,
		"NAV_BRIDGE_UNAVAILABLE":      closedloop.Transient,
		"OBJECT_NOT_FOUND":            closedloop.Perception,
		// The runtime's own code for a failed approach. It was absent from the
		// table, so the classifier reported a known failure as an unknown physical
		// outcome and forbade a retry that is safe.
		"GRASP_NOT_REACHED":            closedloop.Perception,
		"DESTINATION_NOT_FOUND":        closedloop.Perception,
		"NAV_LOCALIZATION_UNAVAILABLE": closedloop.Perception,
		"TARGET_UNREACHABLE":           closedloop.Planning,
		"NAV_DEADLINE_EXCEEDED":        closedloop.Planning,
		"FENCING_TOKEN_STALE":          closedloop.Resource,
		"RESOURCE_GRANT_REQUIRED":      closedloop.Resource,
		"ROBOT_BUSY":                   closedloop.Resource,
		"APPROVAL_REQUIRED":            closedloop.Permission,
		"SKILL_NOT_ALLOWED":            closedloop.Permission,
		"EMERGENCY_STOP_LATCHED":       closedloop.Permission,
		"TOOL_PARAMETERS_INVALID":      closedloop.Validation,
		"VERIFIER_REQUIRED":            closedloop.Validation,
		"CANCELLED":                    closedloop.Fatal,
	} {
		if got := closedloop.Classify(code); got != want {
			t.Fatalf("Classify(%q) = %s, want %s", code, got, want)
		}
	}
	if got := closedloop.Classify("  nav_velocity_stale  "); got != closedloop.Transient {
		t.Fatalf("normalisation failed: %s", got)
	}
}
