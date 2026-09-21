package closedloop

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestGroundedReportIdentityAndReferences(t *testing.T) {
	base := map[string]any{"schema_version": "state-report.v1", "report_id": "report", "action_id": "action", "action_name": "manipulation.pick", "task_id": "task", "edge_boot_id": "boot", "edge_monotonic_ts_ns": uint64(9007199254740993), "logical_clock": 1, "verifier_version": "gvf-1.0.0", "verdict": "VERIFIED", "failure_type": "NONE", "tool_return_status": "SUCCESS", "confidence": 0.95, "evidence_completeness": 1.0, "verified_facts": []string{"Holding(right,cup)"}, "evidence_refs": []map[string]string{{"uri": "evidence://edge/" + strings.Repeat("a", 64), "sha256": strings.Repeat("a", 64), "kind": "force"}}}
	raw, _ := json.Marshal(base)
	result, err := ParseGroundedReport(string(raw), "action", "manipulation.pick", "task")
	if err != nil || result.MonotonicNS != 9007199254740993 {
		t.Fatalf("nanosecond identity lost: %+v %v", result, err)
	}
	for key, value := range map[string]any{"action_id": "wrong", "action_name": "wrong", "task_id": "wrong", "edge_boot_id": "", "logical_clock": 0, "verdict": "SUCCESS", "confidence": 1.1, "evidence_completeness": 0.5, "evidence_refs": []string{}, "verified_facts": []string{}, "tool_return_status": "NOT_DISPATCHED"} {
		t.Run(key, func(t *testing.T) {
			copy := map[string]any{}
			for k, v := range base {
				copy[k] = v
			}
			copy[key] = value
			encoded, _ := json.Marshal(copy)
			if _, err := ParseGroundedReport(string(encoded), "action", "manipulation.pick", "task"); err == nil {
				t.Fatal("invalid report accepted")
			}
		})
	}
}
