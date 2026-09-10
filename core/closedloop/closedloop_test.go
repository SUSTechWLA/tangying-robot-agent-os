package closedloop_test

import (
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
)

// dispatch sits exactly on a millisecond boundary, so NormalizeDispatchTime
// leaves it unchanged and the deterministic backoff schedule stays assertable.
var dispatch = time.Date(2026, 9, 10, 12, 0, 0, 0, time.UTC).Add(time.Millisecond)

func newTrack(t *testing.T, policy closedloop.Policy) *closedloop.Track {
	t.Helper()
	track, err := closedloop.NewTrack("task01-pick", "manipulation.pick", dispatch, policy)
	if err != nil {
		t.Fatal(err)
	}
	if err := track.Dispatch(dispatch); err != nil {
		t.Fatal(err)
	}
	return track
}

func freshEvidence() *closedloop.Evidence {
	return &closedloop.Evidence{
		ObservationID: "obs-1", ObservedAt: dispatch.Add(300 * time.Millisecond),
		Freshness: "FRESH", SourceID: "robot-1/head-rgbd", Confidence: 0.91,
	}
}

func TestSuccessWithoutEvidenceCannotComplete(t *testing.T) {
	track := newTrack(t, closedloop.DefaultPolicy())
	state, err := track.Record(closedloop.Succeeded, "", nil, dispatch.Add(time.Second))
	if !errors.Is(err, closedloop.ErrEvidenceRequired) {
		t.Fatalf("err = %v, want ErrEvidenceRequired", err)
	}
	if state != closedloop.AwaitingEvidence {
		t.Fatalf("state = %s, want AWAITING_EVIDENCE", state)
	}
	if track.Complete() {
		t.Fatal("a successful return code alone must never count as completion")
	}
}

func TestEvidenceOlderThanTheDispatchCannotVerify(t *testing.T) {
	track := newTrack(t, closedloop.DefaultPolicy())
	stale := freshEvidence()
	stale.ObservedAt = dispatch.Add(-time.Millisecond)
	state, err := track.Record(closedloop.Succeeded, "", stale, dispatch.Add(time.Second))
	if !errors.Is(err, closedloop.ErrEvidenceStale) {
		t.Fatalf("err = %v, want ErrEvidenceStale", err)
	}
	if state != closedloop.AwaitingEvidence || track.Complete() {
		t.Fatalf("state = %s complete = %v", state, track.Complete())
	}
}

func TestStaleSourceVerdictCannotBeLaunderedIntoCompletion(t *testing.T) {
	for _, verdict := range []string{"STALE", "stale", "UNKNOWN"} {
		track := newTrack(t, closedloop.DefaultPolicy())
		evidence := freshEvidence()
		evidence.Freshness = verdict
		if _, err := track.Record(closedloop.Succeeded, "", evidence, dispatch.Add(time.Second)); !errors.Is(err, closedloop.ErrEvidenceStale) {
			t.Fatalf("freshness %q: err = %v, want ErrEvidenceStale", verdict, err)
		}
		if track.Complete() {
			t.Fatalf("freshness %q produced a completion", verdict)
		}
	}
}

func TestSuccessWithFreshEvidenceCompletes(t *testing.T) {
	track := newTrack(t, closedloop.DefaultPolicy())
	state, err := track.Record(closedloop.Succeeded, "", freshEvidence(), dispatch.Add(time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if state != closedloop.Verified || !track.Complete() {
		t.Fatalf("state = %s complete = %v", state, track.Complete())
	}
	if track.RequiresReconciliation() {
		t.Fatal("a verified write must not require reconciliation")
	}
}

func TestUnknownOutcomeNeverRetries(t *testing.T) {
	track := newTrack(t, closedloop.Policy{MaxAttempts: 5, BackoffBase: time.Millisecond, BackoffMax: time.Second})
	state, err := track.Record(closedloop.Unknown, "", nil, dispatch.Add(time.Second))
	if !errors.Is(err, closedloop.ErrUnknownRetry) {
		t.Fatalf("err = %v, want ErrUnknownRetry", err)
	}
	if state != closedloop.Escalated {
		t.Fatalf("state = %s, want ESCALATED", state)
	}
	if !track.RequiresReconciliation() {
		t.Fatal("an unknown physical outcome must require reconciliation")
	}
	if err := track.Dispatch(dispatch.Add(2 * time.Second)); !errors.Is(err, closedloop.ErrTerminalState) {
		t.Fatalf("escalated track accepted a dispatch: %v", err)
	}
}

// A runtime that reports a failure code meaning "the outcome is unknown" is the
// same hazard as a transport timeout, so both must escalate.
func TestFailureCodesWithUnknownOutcomeAlsoEscalateWithoutRetry(t *testing.T) {
	for _, code := range []string{
		"EXECUTION_OUTCOME_UNKNOWN", "PHYSICAL_OUTCOME_UNKNOWN", "RUNTIME_JOURNAL_UNAVAILABLE",
		"BACKEND_STOP_FAILED", "SERVICE_SHUTDOWN",
	} {
		track := newTrack(t, closedloop.Policy{MaxAttempts: 4, BackoffBase: time.Millisecond, BackoffMax: time.Second})
		state, err := track.Record(closedloop.Failed, code, nil, dispatch.Add(time.Second))
		if state != closedloop.Escalated {
			t.Fatalf("%s: state = %s, want ESCALATED", code, state)
		}
		if !errors.Is(err, closedloop.ErrUnknownRetry) {
			t.Fatalf("%s: err = %v, want ErrUnknownRetry", code, err)
		}
	}
}

func TestTransientFailureRetriesWithinBudgetThenEscalates(t *testing.T) {
	track := newTrack(t, closedloop.Policy{MaxAttempts: 2, BackoffBase: 100 * time.Millisecond, BackoffMax: time.Second})
	state, err := track.Record(closedloop.Failed, "NAV_VELOCITY_STALE", nil, dispatch.Add(time.Second))
	if err != nil || state != closedloop.Retrying {
		t.Fatalf("state = %s err = %v, want RETRYING", state, err)
	}
	if at := track.NextAttemptAt(); at != dispatch.Add(100*time.Millisecond) {
		t.Fatalf("next attempt at %s, want deterministic backoff %s", at, dispatch.Add(100*time.Millisecond))
	}
	if err := track.Dispatch(dispatch.Add(100 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	state, err = track.Record(closedloop.Failed, "NAV_VELOCITY_STALE", nil, dispatch.Add(300*time.Millisecond))
	if !errors.Is(err, closedloop.ErrRetriesExhausted) {
		t.Fatalf("err = %v, want ErrRetriesExhausted", err)
	}
	if state != closedloop.Escalated {
		t.Fatalf("state = %s, want ESCALATED", state)
	}
	if track.RequiresReconciliation() {
		t.Fatal("a classified transient failure is a known non-effect, not an unknown outcome")
	}
}

func TestPerceptionFailureIsRetryableButPlanningAndValidationAreNot(t *testing.T) {
	for _, tc := range []struct {
		code      string
		retryable bool
	}{
		{"OBJECT_NOT_FOUND", true},
		{"NAV_MAP_NOT_READY", true},
		{"NAV_OBSERVATION_LOST", true},
		{"TARGET_UNREACHABLE", false},
		{"NAV_WORKSPACE_LIMIT", false},
		{"TOOL_PARAMETERS_INVALID", false},
		{"APPROVAL_REQUIRED", false},
		{"FENCING_TOKEN_STALE", false},
		{"IDEMPOTENCY_CONFLICT", false},
		{"SOMETHING_NEW_FROM_A_FUTURE_ADAPTER", false},
		{"", false},
	} {
		if got := closedloop.Classify(tc.code).Retryable(); got != tc.retryable {
			t.Fatalf("Classify(%q).Retryable() = %v, want %v (class %s)",
				tc.code, got, tc.retryable, closedloop.Classify(tc.code))
		}
	}
}

func TestClassifyAssignsTheExpectedClassForRealRuntimeCodes(t *testing.T) {
	for code, want := range map[string]closedloop.Class{
		"EXECUTION_OUTCOME_UNKNOWN":    closedloop.UnknownOutcome,
		"RUNTIME_JOURNAL_UNAVAILABLE":  closedloop.UnknownOutcome,
		"NAV_VELOCITY_STALE":           closedloop.Transient,
		"NAV_BRIDGE_UNAVAILABLE":       closedloop.Transient,
		"OBJECT_NOT_FOUND":             closedloop.Perception,
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

func TestClassifiedFailuresEscalateImmediatelyWhenNotRetryable(t *testing.T) {
	track := newTrack(t, closedloop.DefaultPolicy())
	state, err := track.Record(closedloop.Failed, "TARGET_UNREACHABLE", nil, dispatch.Add(time.Second))
	if err != nil {
		t.Fatalf("a known non-retryable failure is not an error, got %v", err)
	}
	if state != closedloop.Escalated {
		t.Fatalf("state = %s, want ESCALATED", state)
	}
	if track.RequiresReconciliation() {
		t.Fatal("a planner-class failure does not leave the hardware state unknown")
	}
}

func TestBackoffGrowsThenSaturates(t *testing.T) {
	base, maximum := 100*time.Millisecond, time.Second
	// Backoff(n) is the delay owed before attempt number n (1-based): the first
	// attempt waits for nothing, the second waits one base, then it doubles.
	want := []time.Duration{0, 0, base, 2 * base, 4 * base, 8 * base, maximum, maximum}
	for attempt, expected := range want {
		if got := closedloop.Backoff(attempt, base, maximum); got != expected {
			t.Fatalf("Backoff(%d) = %s, want %s", attempt, got, expected)
		}
	}
	if got := closedloop.Backoff(9, 0, maximum); got != 0 {
		t.Fatalf("zero base must disable backoff, got %s", got)
	}
}

func TestPolicyAndTrackRejectInvalidConfiguration(t *testing.T) {
	if err := (closedloop.Policy{MaxAttempts: 0}).Validate(); err == nil {
		t.Fatal("a policy without attempts must be rejected")
	}
	if _, err := closedloop.NewTrack("", "manipulation.pick", dispatch, closedloop.DefaultPolicy()); err == nil {
		t.Fatal("a track without a step id must be rejected")
	}
	if _, err := closedloop.NewTrack("step", "", dispatch, closedloop.DefaultPolicy()); err == nil {
		t.Fatal("a track without a capability must be rejected")
	}
	track, err := closedloop.NewTrack("step", "manipulation.pick", dispatch, closedloop.Policy{})
	if err != nil {
		t.Fatal(err)
	}
	if track.Attempts() != 0 || track.State() != closedloop.Pending {
		t.Fatalf("fresh track = %s/%d", track.State(), track.Attempts())
	}
	// Recording before dispatch is a programming error, not a verdict.
	if _, err := track.Record(closedloop.Succeeded, "", freshEvidence(), dispatch); err == nil {
		t.Fatal("a verdict before dispatch must be rejected")
	}
}

func TestRetryBudgetIsEnforcedOnDispatchToo(t *testing.T) {
	track := newTrack(t, closedloop.Policy{MaxAttempts: 1, BackoffBase: time.Millisecond, BackoffMax: time.Second})
	state, err := track.Record(closedloop.Failed, "NAV_VELOCITY_STALE", nil, dispatch.Add(time.Second))
	if !errors.Is(err, closedloop.ErrRetriesExhausted) {
		t.Fatalf("err = %v, want ErrRetriesExhausted", err)
	}
	if state != closedloop.Escalated {
		t.Fatalf("state = %s, want ESCALATED", state)
	}
}

func TestVerifiedTrackIsTerminal(t *testing.T) {
	track := newTrack(t, closedloop.DefaultPolicy())
	if _, err := track.Record(closedloop.Succeeded, "", freshEvidence(), dispatch.Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	if _, err := track.Record(closedloop.Failed, "NAV_VELOCITY_STALE", nil, dispatch.Add(2*time.Second)); !errors.Is(err, closedloop.ErrTerminalState) {
		t.Fatalf("verified track accepted another verdict: %v", err)
	}
	if err := track.Dispatch(dispatch.Add(2 * time.Second)); !errors.Is(err, closedloop.ErrTerminalState) {
		t.Fatalf("verified track accepted another dispatch: %v", err)
	}
	if at := track.NextAttemptAt(); !at.IsZero() {
		t.Fatalf("verified track scheduled a retry at %s", at)
	}
}
