package closedloop

import (
	"encoding/json"
	"errors"
	"math"
	"regexp"
)

// GroundedReport is the control-plane projection of the edge-owned GCL schema.
// Raw evidence stays on the edge. Nanosecond identity travels as JSON text,
// never through protobuf Struct's floating-point number representation.
type GroundedReport struct {
	ReportID             string   `json:"report_id"`
	SchemaVersion        string   `json:"schema_version"`
	ActionID             string   `json:"action_id"`
	ActionName           string   `json:"action_name"`
	TaskID               string   `json:"task_id"`
	Verdict              string   `json:"verdict"`
	FailureType          string   `json:"failure_type"`
	Confidence           float64  `json:"confidence"`
	EvidenceCompleteness float64  `json:"evidence_completeness"`
	EdgeBootID           string   `json:"edge_boot_id"`
	MonotonicNS          uint64   `json:"edge_monotonic_ts_ns"`
	VerifierVersion      string   `json:"verifier_version"`
	ToolReturnStatus     string   `json:"tool_return_status"`
	LogicalClock         uint64   `json:"logical_clock"`
	VerifiedFacts        []string `json:"verified_facts"`
	EvidenceRefs         []struct {
		URI    string `json:"uri"`
		SHA256 string `json:"sha256"`
		Kind   string `json:"kind"`
	} `json:"evidence_refs"`
}

var evidenceURI = regexp.MustCompile(`^evidence://[a-zA-Z0-9_.-]+/([a-f0-9]{64})$`)

func ParseGroundedReport(raw, actionID, actionName, taskID string) (GroundedReport, error) {
	var report GroundedReport
	if len(raw) > 1024*1024 {
		return report, errors.New("grounded report exceeds size limit")
	}
	err := json.Unmarshal([]byte(raw), &report)
	if err != nil || len(raw) > 1024*1024 || report.SchemaVersion != "state-report.v1" ||
		report.ActionID != actionID || report.ActionName != actionName || report.TaskID != taskID ||
		report.ReportID == "" || report.EdgeBootID == "" || report.VerifierVersion == "" || report.MonotonicNS == 0 || report.LogicalClock == 0 ||
		math.IsNaN(report.Confidence) || report.Confidence < 0 || report.Confidence > 1 ||
		report.EvidenceCompleteness < 0 || report.EvidenceCompleteness > 1 {
		return report, errors.New("invalid or mismatched grounded state report")
	}
	if report.Verdict != "VERIFIED" && report.Verdict != "FALSIFIED" && report.Verdict != "UNKNOWN" {
		return report, errors.New("invalid grounded verdict")
	}
	for _, ref := range report.EvidenceRefs {
		match := evidenceURI.FindStringSubmatch(ref.URI)
		if len(match) != 2 || match[1] != ref.SHA256 {
			return report, errors.New("grounded evidence must be a content-addressed reference")
		}
	}
	if report.Verdict == "VERIFIED" && (report.Confidence == 0 || report.EvidenceCompleteness != 1 ||
		len(report.EvidenceRefs) == 0 || len(report.VerifiedFacts) == 0 || report.FailureType != "NONE" || report.ToolReturnStatus == "NOT_DISPATCHED") {
		return report, errors.New("verified report lacks complete physical evidence")
	}
	return report, nil
}
