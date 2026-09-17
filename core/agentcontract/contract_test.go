package agentcontract_test

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// Topic matching decides whether an observer ever hears about an event, so a
// near-miss here is silent: an agent that subscribes to the wrong pattern looks
// healthy and simply never reports anything. These cases pin the boundary.
func TestMatchesHonoursNamespaceBoundaries(t *testing.T) {
	cases := []struct {
		pattern string
		topic   string
		want    bool
		why     string
	}{
		{"ops.anomaly_detected", "ops.anomaly_detected", true, "exact topic"},
		{"ops.*", "ops.anomaly_detected", true, "namespace wildcard"},
		{"ops.*", "ops.root_cause_hypothesis", true, "second topic in the namespace"},
		{"ops.*", "agent.registered", false, "wildcard must not cross namespaces"},
		{"ops.*", "task.started", false, "wildcard must not cross namespaces"},
		{"task.*", "task.started", true, "task namespace"},
		{"agent.*", "agent.health_changed", true, "agent namespace"},
		{"ops.*", "ops", false, "a bare namespace is not a topic"},
		{"ops.*", "opsx.anomaly", false, "a longer name sharing a prefix is not in the namespace"},
		{"ops", "ops.anomaly_detected", false, "a bare namespace matches nothing"},
		{"ops.anomaly_detected", "ops.anomaly", false, "a prefix is not a match"},
		{"", "ops.anomaly_detected", false, "empty pattern"},
		{"ops.*", "", false, "empty topic"},
	}
	for _, testCase := range cases {
		if got := agentcontract.Matches(testCase.pattern, testCase.topic); got != testCase.want {
			t.Errorf("Matches(%q, %q) = %v, want %v (%s)",
				testCase.pattern, testCase.topic, got, testCase.want, testCase.why)
		}
	}
}

func TestMatchesAnySelectsAcrossPatterns(t *testing.T) {
	patterns := []string{agentcontract.TopicOpsAll, agentcontract.TopicTaskStarted}
	if !agentcontract.MatchesAny(patterns, agentcontract.TopicOpsEscalationRequired) {
		t.Fatal("an ops topic was not selected by the ops wildcard")
	}
	if agentcontract.MatchesAny(patterns, agentcontract.TopicActionExecuted) {
		t.Fatal("action.executed matched a subscription that does not name it")
	}
	if agentcontract.MatchesAny(nil, agentcontract.TopicTaskStarted) {
		t.Fatal("no subscription must match nothing")
	}
}

// A misspelled subscription is worse than a missing one: it is silent. The
// vocabulary check turns the typo into a startup error.
func TestKnownPatternRejectsTypos(t *testing.T) {
	for _, pattern := range []string{
		agentcontract.TopicOpsAll, agentcontract.TopicTaskAll, agentcontract.TopicAgentAll,
		agentcontract.TopicTaskStarted, agentcontract.TopicActionExecuted,
		agentcontract.TopicEvidenceCollected, agentcontract.TopicStateTransition,
		agentcontract.TopicOpsAnomalyDetected, agentcontract.TopicOpsRootCauseHypothesis,
		agentcontract.TopicOpsRecoveryProposed, agentcontract.TopicOpsEscalationRequired,
		agentcontract.TopicAgentRegistered, agentcontract.TopicAgentHealthChanged,
		agentcontract.TopicAgentPermissionDenied,
	} {
		if !agentcontract.KnownPattern(pattern) {
			t.Errorf("KnownPattern(%q) = false for a declared pattern", pattern)
		}
	}
	for _, pattern := range []string{"", "ops", "ops.anomaly", "opsx.*", "task", "task.*.done", "OPS.*"} {
		if agentcontract.KnownPattern(pattern) {
			t.Errorf("KnownPattern(%q) = true for an undeclared pattern", pattern)
		}
	}
}

func TestKnownTopicCoversEveryDeclaredTopic(t *testing.T) {
	topics := []string{
		agentcontract.TopicTaskStarted, agentcontract.TopicTaskCompleted, agentcontract.TopicTaskFailed,
		agentcontract.TopicActionExecuted, agentcontract.TopicEvidenceCollected, agentcontract.TopicStateTransition,
		agentcontract.TopicOpsAnomalyDetected, agentcontract.TopicOpsRootCauseHypothesis,
		agentcontract.TopicOpsRecoveryProposed, agentcontract.TopicOpsEscalationRequired,
		agentcontract.TopicOpsRecoveryDeferred,
		agentcontract.TopicAgentRegistered, agentcontract.TopicAgentHealthChanged,
		agentcontract.TopicAgentPermissionDenied,
	}
	if len(topics) != 14 {
		t.Fatalf("the topic list has %d entries; the vocabulary and this test must move together", len(topics))
	}
	for _, topic := range topics {
		if !agentcontract.KnownTopic(topic) {
			t.Errorf("KnownTopic(%q) = false", topic)
		}
		// Every topic must sit in a namespace, otherwise an observer written
		// against that namespace's wildcard would silently never hear it.
		namespace := agentcontract.NamespaceOf(topic)
		if namespace == "" {
			t.Errorf("topic %q belongs to no namespace", topic)
			continue
		}
		if !agentcontract.Matches(namespace+"*", topic) {
			t.Errorf("topic %q is not matched by its own namespace wildcard %q*", topic, namespace)
		}
	}
}

func TestKnownPatternAcceptsEveryNamespaceWildcard(t *testing.T) {
	if len(agentcontract.NamespacePrefixes) == 0 {
		t.Fatal("no namespaces are declared")
	}
	for _, namespace := range agentcontract.NamespacePrefixes {
		if !agentcontract.KnownPattern(namespace + "*") {
			t.Errorf("KnownPattern(%q*) = false for a declared namespace", namespace)
		}
	}
}

func TestPermissionSeparatesDeclarationFromEnforcement(t *testing.T) {
	readOnly := agentcontract.ReadOnlyPermission()
	if !readOnly.ReadOnly {
		t.Fatal("the read-only permission must be read-only")
	}
	if readOnly.MayMutateWorld() || readOnly.MayMutateTaskState() {
		t.Fatalf("read-only permission %#v permits a mutation", readOnly)
	}
	// A declaration of world mutation without task state mutation is not a
	// meaningful authority: an agent that cannot change a task cannot drive it.
	observer := agentcontract.Permission{ReadOnly: false, MutatesWorld: true}
	if !observer.MayMutateWorld() {
		t.Fatal("a declared mutation must be reported as permitted")
	}
	zero := agentcontract.Permission{}
	if zero.MayMutateWorld() || zero.MayMutateTaskState() {
		t.Fatal("the zero permission must be the safest reading of an unknown agent")
	}
}

func TestAnomalyPayloadKeepsRepeatsIdentifiable(t *testing.T) {
	payload := agentcontract.AnomalyPayload{
		AnomalyID: "fault:estop:EMERGENCY_STOP_LATCHED", Severity: "critical",
		Component: "estop", Code: "EMERGENCY_STOP_LATCHED", Message: "latched",
		Evidence: []string{"obs-1", "obs-2"}, DetectedAt: time.Unix(100, 0).UTC(),
		Facts: map[string]any{"ageMs": int64(8124)},
	}.Encode()

	if payload["anomalyId"] != "fault:estop:EMERGENCY_STOP_LATCHED" {
		t.Fatalf("anomaly id lost: %#v", payload)
	}
	// The identity is what lets a repeat report be recognised as the same
	// problem instead of a new one, which is how an observer avoids paging on
	// every telemetry tick.
	if payload["severity"] != "critical" || payload["component"] != "estop" {
		t.Fatalf("severity or component lost: %#v", payload)
	}
	if facts, ok := payload["facts"].(map[string]any); !ok || facts["ageMs"] != int64(8124) {
		t.Fatalf("facts lost: %#v", payload["facts"])
	}
}

func TestHypothesisPayloadAlwaysCarriesConfidenceAndRetryAdvice(t *testing.T) {
	payload := agentcontract.HypothesisPayload{
		HypothesisID: "h1", Category: "UNKNOWN_OUTCOME", Confidence: 0,
		EvidenceChain:           []string{"step remained STARTED"},
		AutomaticRetryForbidden: true, ProposedAt: time.Unix(1, 0).UTC(),
	}.Encode()

	// Confidence zero must be published rather than omitted: a consumer must be
	// able to treat low confidence as "ask a human", which it cannot do if the
	// field is simply absent.
	if _, present := payload["confidence"]; !present {
		t.Fatalf("confidence was omitted at zero: %#v", payload)
	}
	// The single most important piece of advice must never be softened by an
	// omission downstream.
	if payload["automaticRetryForbidden"] != true {
		t.Fatalf("automaticRetryForbidden = %#v, want true", payload["automaticRetryForbidden"])
	}
	if chain, ok := payload["evidenceChain"].([]string); !ok || len(chain) == 0 {
		t.Fatalf("evidence chain lost: %#v", payload["evidenceChain"])
	}
}

func TestPayloadBoundsUnboundedInput(t *testing.T) {
	long := strings.Repeat("x", agentcontract.MaxPayloadString*2)
	payload := agentcontract.AnomalyPayload{
		AnomalyID: "a", Severity: "info", Component: "c", Code: "CODE",
		Message: long, DetectedAt: time.Unix(1, 0).UTC(),
		// A nested structure is dropped rather than flattened: an event payload
		// that can hold arbitrary depth is one nobody can validate.
		Facts: map[string]any{"ok": "yes", "nested": map[string]any{"a": 1}, "list": []string{"a"}},
	}.Encode()

	message, _ := payload["message"].(string)
	if len(message) > agentcontract.MaxPayloadString {
		t.Fatalf("message length = %d, want <= %d", len(message), agentcontract.MaxPayloadString)
	}
	facts, _ := payload["facts"].(map[string]any)
	if facts["ok"] != "yes" {
		t.Fatalf("a bounded scalar was dropped: %#v", facts)
	}
	if _, present := facts["nested"]; present {
		t.Fatalf("a nested value was published: %#v", facts)
	}
	if _, present := facts["list"]; present {
		t.Fatalf("a list value was published: %#v", facts)
	}
}

func TestRecoveryProposalKeepsApprovalVisible(t *testing.T) {
	payload := agentcontract.RecoveryProposalPayload{
		ProposalID: "p1", Action: "re-observe before retrying",
		AutomationLevel: "advisory", RequiresApproval: true, HypothesisID: "h1",
	}.Encode()
	if payload["requiresApproval"] != true {
		t.Fatalf("requiresApproval = %#v, want true", payload["requiresApproval"])
	}
	if payload["automationLevel"] != "advisory" {
		t.Fatalf("automationLevel = %#v, want advisory", payload["automationLevel"])
	}
}

func TestPermissionDenialIsRecordedWithWhatWasAsked(t *testing.T) {
	payload := agentcontract.PermissionDeniedPayload{
		Capability: "manipulation.pick", ReasonCode: "AGENT_READ_ONLY",
		MutatesWorld: true, Detail: "observer may not drive hardware",
	}.Encode()
	if payload["mutatesWorld"] != true || payload["reasonCode"] != "AGENT_READ_ONLY" {
		t.Fatalf("a refusal that loses what was asked cannot be reviewed: %#v", payload)
	}
}

func TestDescribeReadsTheStaticHalfOfAnAgent(t *testing.T) {
	if descriptor := agentcontract.Describe(nil); descriptor.Name != "" {
		t.Fatalf("describing no agent = %#v", descriptor)
	}
	descriptor := agentcontract.Describe(stubAgent{name: "task", version: "1"})
	if descriptor.Name != "task" || descriptor.Version != "1" {
		t.Fatalf("descriptor = %#v", descriptor)
	}
	if !descriptor.Permissions.MayMutateWorld() {
		t.Fatalf("descriptor lost the permissions: %#v", descriptor.Permissions)
	}
}

type stubAgent struct {
	name    string
	version string
}

func (a stubAgent) Name() string                             { return a.name }
func (a stubAgent) Version() string                          { return a.version }
func (a stubAgent) Capabilities() []agentcontract.Capability { return nil }
func (a stubAgent) Subscriptions() []string                  { return nil }
func (a stubAgent) Health(context.Context) agentcontract.Health {
	return agentcontract.Health{Status: agentcontract.HealthHealthy}
}
func (a stubAgent) Permissions() agentcontract.Permission {
	return agentcontract.Permission{MutatesWorld: true, MutatesTaskState: true}
}
func (a stubAgent) OnEvent(context.Context, agentcontract.Event) error { return nil }
func (a stubAgent) Execute(context.Context, agentcontract.ExecuteRequest) (agentcontract.ExecutionOutcome, error) {
	return agentcontract.ExecutionOutcome{}, nil
}
func (a stubAgent) Shutdown(context.Context) error { return nil }
