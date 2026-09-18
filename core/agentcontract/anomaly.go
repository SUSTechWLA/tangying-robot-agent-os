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

// Stage prefixes a finding's later events put in front of the shared report id.
// They exist so a reader can tell the anomaly from the hypothesis made about it;
// they are not part of the condition's identity.
var anomalyStagePrefixes = []string{"hyp-", "prop-", "esc-"}

// AnomalyIdentityOf reduces any stage of a finding to the condition it is about.
//
// A finding produces up to four events — the anomaly, the hypothesis, the
// proposal and the escalation — and each names itself by prefixing the shared
// report id with its stage. Anything asking "which condition is this?", as the
// ledger's re-observation filter does, has to undo that prefix, and the count
// suffix with it: the count is how a worsening condition announces itself, not
// what the condition is. That rule lives here beside AnomalyReportID, because the
// alternative — every caller re-deriving it — is exactly how the three disagreeing
// identities this file was written to fix came about.
func AnomalyIdentityOf(findingID string) string {
	for _, prefix := range anomalyStagePrefixes {
		if strings.HasPrefix(findingID, prefix) {
			findingID = findingID[len(prefix):]
			break
		}
	}
	index := strings.LastIndex(findingID, "#")
	if index <= 0 {
		return findingID
	}
	if _, err := strconv.Atoi(findingID[index+1:]); err != nil {
		return findingID
	}
	return findingID[:index]
}

// findingIDKeys are the payload fields the four finding events name themselves
// by, in the order they are tried.
var findingIDKeys = []string{"anomalyId", "hypothesisId", "proposalId", "escalationId"}

// ConditionIdentity reads the condition a finding event is about.
//
// It reports false for an event that is not a finding, and for one whose id is
// missing: a caller that keyed on an empty identity would treat every such event
// as the same condition and suppress all but the first, which is the failure mode
// this is meant to prevent rather than cause.
func ConditionIdentity(topic string, payload map[string]any) (string, bool) {
	switch topic {
	case TopicOpsAnomalyDetected, TopicOpsRootCauseHypothesis,
		TopicOpsRecoveryProposed, TopicOpsEscalationRequired, TopicOpsAnomalyCleared:
	default:
		return "", false
	}
	for _, key := range findingIDKeys {
		raw, ok := payload[key].(string)
		if !ok {
			continue
		}
		if identity := AnomalyIdentityOf(strings.TrimSpace(raw)); identity != "" {
			return identity, true
		}
	}
	return "", false
}
