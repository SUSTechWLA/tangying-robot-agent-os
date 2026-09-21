package agentcontext

import (
	"encoding/json"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
)

// GroundedRecord preserves the original validated report, including fields not
// projected by the control plane. Edge monotonic time is NOT UTC observation time.
func GroundedRecord(raw, taskID, robotID string) (Record, error) {
	var header closedloop.GroundedReport
	if err := json.Unmarshal([]byte(raw), &header); err != nil {
		return Record{}, err
	}
	report, err := closedloop.ParseGroundedReport(raw, header.ActionID, header.ActionName, taskID)
	if err != nil {
		return Record{}, err
	}
	refs := make([]string, 0, len(report.EvidenceRefs))
	for _, ref := range report.EvidenceRefs {
		refs = append(refs, ref.URI)
	}
	return Record{ID: report.ReportID, Kind: "verification", Scope: Scope{TaskID: report.TaskID, RobotID: robotID},
		Statement: fmt.Sprintf("动作 %s（%s）的边缘验证结论为 %s；失败类型=%s；工具回执=%s。计划版本及 UTC 有效期未声明，不能推定当前仍有效。原报告：%s", report.ActionID, report.ActionName, report.Verdict, report.FailureType, report.ToolReturnStatus, raw), EvidenceIDs: refs}, nil
}
