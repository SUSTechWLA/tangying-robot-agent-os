package agentcontract

import (
	"strconv"
	"strings"
)

// The identity of a finding.
//
// A finding is reported, stored, planned for and looked up by four different
// parts of the system, and each of them had its own idea of what identifies one.
// The alert store keyed on "code@component", the recovery agent keyed its plan on
// the bare code, and the console looked the plan up by the bare code as well. So
// eleven different component failures of one deployment were all shown the single
// plan made for the first of them, and the plan's own diagnosis named a component
// the alert was not about.
//
// Nothing crashed and no test failed: every part was self-consistent, and only
// the composition of them was wrong. The rule is therefore written down once,
// here, and every part calls it.
//
// Identity is what is wrong and where it is wrong — the code and the component.
// It deliberately excludes how much of it there is: a count that moved from one
// abnormal task to five is the same problem getting worse, and giving it a new
// identity would create a second alert row and a second plan for one condition.

// AnomalyIdentity is the stable identity of a finding on a component.
//
// An empty component is meaningful rather than a mistake: a robot-wide finding
// belongs to no single component, and "CODE@" is a stable identity for it.
func AnomalyIdentity(code, component string) string {
	return code + "@" + component
}

// AnomalyReportID is the identity one report of a finding is deduplicated by.
//
// It is the stable identity plus the observed count, because a standing
// condition must not be re-announced every tick while a worsening one must be.
// Two reports of the same condition share an identity; a report of that condition
// getting worse does not.
//
// A nil count means the finding is not about a number of things. It is a pointer
// rather than a zero value because "no count" and "a count of zero" are different
// reports, and collapsing them would silently stop announcing a count that
// dropped to nothing.
func AnomalyReportID(code, component string, count *int) string {
	identity := AnomalyIdentity(code, component)
	if count == nil {
		return identity
	}
	return identity + "#" + strconv.Itoa(*count)
}

// SameAnomaly reports whether a report identity refers to a stable identity.
//
// It exists so a reader holding "CODE@component#3" can find the plan made for
// "CODE@component" without every caller re-deriving the count suffix rule. The
// comparison is on the "#" boundary, so "CODE@a" is not treated as a prefix of
// "CODE@ab".
func SameAnomaly(reportID, identity string) bool {
	if reportID == identity {
		return true
	}
	if identity == "" || !strings.HasPrefix(reportID, identity) {
		return false
	}
	return reportID[len(identity)] == '#'
}
