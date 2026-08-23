package recovery

import (
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

func TestClassifySeparatesSafeRetryFromBlockedAndUnknownExecution(t *testing.T) {
	tests := []struct {
		err       error
		class     Class
		retryable bool
	}{
		{policy.ErrObservationStale, ObservationWait, true},
		{policy.ErrObservationUnavailable, ObservationWait, true},
		{policy.ErrProviderTimeout, PolicyRetry, true},
		{policy.ErrProviderUnavailable, PolicyRetry, true},
		{policy.ErrManifestDrift, PolicyBlocked, false},
		{policy.ErrPolicyIncompatible, PolicyBlocked, false},
		{policy.ErrActionUnsafe, PolicyBlocked, false},
		{runtime.ErrSkillStreamClosed, ExecutionReconcile, false},
		{runtime.ErrCapabilityUnavailable, SafeRecovery, false},
		{errors.New("unknown"), SafeRecovery, false},
	}
	for _, test := range tests {
		activity := Classify(test.err)
		if activity.Class != test.class || activity.Retryable != test.retryable ||
			activity.KnownState == "" || activity.AutomaticAction == "" {
			t.Fatalf("error=%v activity=%#v", test.err, activity)
		}
	}
}

func TestRecoveryMessagesNeverContainRawError(t *testing.T) {
	activity := Classify(errors.New("grpc bearerToken=secret-value"))
	if activity.TechnicalCode != "UNCLASSIFIED_EXECUTION_FAILURE" {
		t.Fatalf("activity = %#v", activity)
	}
	for _, text := range []string{activity.KnownState, activity.RobotSafetyState, activity.AutomaticAction} {
		if text == "" || text == "grpc bearerToken=secret-value" {
			t.Fatalf("unsafe public text = %q", text)
		}
	}
}
