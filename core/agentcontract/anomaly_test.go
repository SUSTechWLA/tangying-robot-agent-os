package agentcontract_test

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// The identity of a finding is what four parts of the system agree on, so its
// edges are pinned rather than left to whoever calls it.
//
// The defect behind this file: the alert store identified a finding by code and
// component, the recovery agent identified it by code, and the console looked the
// plan up by code. Each was self-consistent and the composition showed one
// component's recovery plan against eleven different component failures.

func TestIdentityNamesWhatIsWrongAndWhere(t *testing.T) {
	if got := agentcontract.AnomalyIdentity("NAV_MAP_NOT_READY", "chassis"); got != "NAV_MAP_NOT_READY@chassis" {
		t.Fatalf("identity = %q", got)
	}
	// A robot-wide finding belongs to no component, and that is a real identity
	// rather than a missing one: two robot-wide findings with different codes
	// must still be two findings.
	if got := agentcontract.AnomalyIdentity("ANOMALY_TELEMETRY_STALE", ""); got != "ANOMALY_TELEMETRY_STALE@" {
		t.Fatalf("robot-wide identity = %q", got)
	}
	if agentcontract.AnomalyIdentity("A", "") == agentcontract.AnomalyIdentity("B", "") {
		t.Fatal("two different robot-wide findings share an identity")
	}
}

func TestReportIdentityCarriesTheCountOnlyWhenThereIsOne(t *testing.T) {
	if got := agentcontract.AnomalyReportID("CODE", "c", nil); got != "CODE@c" {
		t.Fatalf("identity without a count = %q", got)
	}
	three := 3
	if got := agentcontract.AnomalyReportID("CODE", "c", &three); got != "CODE@c#3" {
		t.Fatalf("identity with a count = %q", got)
	}
	// A count of zero is a report, not the absence of one. Collapsing the two
	// would stop announcing a condition that dropped to nothing.
	zero := 0
	if got := agentcontract.AnomalyReportID("CODE", "c", &zero); got != "CODE@c#0" {
		t.Fatalf("identity with a zero count = %q", got)
	}
}

func TestAReportIsMatchedToItsIdentityAcrossTheCountSuffix(t *testing.T) {
	identity := agentcontract.AnomalyIdentity("CODE", "c")
	three := 3
	report := agentcontract.AnomalyReportID("CODE", "c", &three)

	if !agentcontract.SameAnomaly(report, identity) {
		t.Fatal("a report carrying a count did not match its own identity")
	}
	if !agentcontract.SameAnomaly(identity, identity) {
		t.Fatal("an identity did not match itself")
	}
	if agentcontract.SameAnomaly(identity, report) {
		t.Fatal("a plain identity was treated as a report of the counted one")
	}
}

func TestIdentityMatchingDoesNotConfuseSimilarComponents(t *testing.T) {
	// The separator matters. A prefix match that ignored it would answer the
	// question "is this the same finding" with "is this component's name a prefix
	// of that one's", and quietly attach one component's plan to another.
	if agentcontract.SameAnomaly("CODE@ab", "CODE@a") {
		t.Fatal("CODE@ab was matched to CODE@a")
	}
	if agentcontract.SameAnomaly("CODE@a#2", "CODE@a") != true {
		t.Fatal("a counted report of CODE@a did not match CODE@a")
	}
	if agentcontract.SameAnomaly("CODE@ab#2", "CODE@a") {
		t.Fatal("CODE@ab#2 was matched to CODE@a")
	}
	if agentcontract.SameAnomaly("CODE@a", "") {
		t.Fatal("every report was matched to the empty identity")
	}
}

// Distinct components must never share an identity, because that is exactly the
// collision that made one plan stand in for eleven failures.
func TestDifferentComponentsNeverShareAnIdentity(t *testing.T) {
	seen := map[string]string{}
	for _, component := range []string{"navigation.navigate", "observe_scene", "resolve_targets", ""} {
		identity := agentcontract.AnomalyIdentity("ANOMALY_ACTION_FAILED", component)
		if previous, exists := seen[identity]; exists {
			t.Fatalf("components %q and %q share identity %q", previous, component, identity)
		}
		seen[identity] = component
	}
}
