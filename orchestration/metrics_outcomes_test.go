package orchestration

import "testing"

func TestCurrentOutcomeMetricsSeparateCancellationFromFailure(t *testing.T) {
	m := CalculateMetrics([]TaskRecord{
		{State: "READY"}, {State: "EXECUTING"}, {State: "SUCCEEDED"},
		{State: "FAILED"}, {State: "RECOVERABLE_FAILURE"}, {State: "SAFETY_STOPPED"},
		{State: "CANCELLED"}, {State: "cancelled"},
	})
	if m.TotalTasks != 8 || m.SucceededTasks != 1 || m.FailedTasks != 2 || m.CancelledTasks != 2 || m.SafetyStoppedTasks != 1 {
		t.Fatalf("current state counts are conflated: %+v", m)
	}
	// This compatibility rate is the share of all persisted tasks, including
	// unexecuted drafts. Historical execution failures need the event ledger.
	if m.EndToEndSuccessRate != 0.125 {
		t.Fatalf("changed the documented all-created denominator: %+v", m)
	}
}
