package agent_test

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type groundedRobot struct {
	recordingRobot
	verdict string
	omit    bool
}

func (r *groundedRobot) Invoke(ctx context.Context, c runtime.Command) (runtime.Result, error) {
	r.counts[string(c.Capability)]++
	result := runtime.Result{Success: true, VerificationConfidence: 0.95}
	if r.omit {
		return result, nil
	}
	failure := "NONE"
	if r.verdict != "VERIFIED" {
		failure = "EVIDENCE_INSUFFICIENT"
	}
	data := map[string]any{"schema_version": "state-report.v1", "report_id": "report-" + c.CommandID, "action_id": c.CommandID, "action_name": string(c.Capability), "task_id": c.TaskID, "edge_boot_id": "boot", "edge_monotonic_ts_ns": 123, "logical_clock": 1, "verifier_version": "gvf-1.0.0", "verdict": r.verdict, "failure_type": failure, "tool_return_status": "SUCCESS", "confidence": 0.95, "evidence_completeness": 1, "verified_facts": []string{"test fixture"}, "evidence_refs": []map[string]string{{"uri": "evidence://test/" + strings.Repeat("a", 64), "sha256": strings.Repeat("a", 64), "kind": "pose"}}}
	raw, _ := json.Marshal(data)
	result.StateReportJSON = string(raw)
	return result, nil
}
func TestGroundedGatePersistsAndBlocksUnknownWithoutRetry(t *testing.T) {
	for _, verdict := range []string{"VERIFIED", "UNKNOWN", "FALSIFIED", "missing"} {
		t.Run(verdict, func(t *testing.T) {
			t.Setenv("TANGYING_GVF_ENABLED", "1")
			store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
			task := &tasks.Task{ID: "grounded", Intent: parsed, Approved: true, CurrentRevision: 1}
			robot := &groundedRobot{recordingRobot: recordingRobot{counts: map[string]int{}}, verdict: verdict, omit: verdict == "missing"}
			runner := agent.NewRunner(store, robot, robot)
			reports := 0
			runner.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
				if event.Type == "STATE_REPORT" {
					reports++
				}
				return nil
			}
			_, err = runner.Run(context.Background(), task)
			if verdict == "VERIFIED" {
				if err != nil || reports == 0 {
					t.Fatalf("verified report rejected: %v reports=%d", err, reports)
				}
			} else if !errors.Is(err, agent.ErrPhysicalOutcomeUnknown) {
				t.Fatalf("unknown accepted: %v", err)
			}
			for name, count := range robot.counts {
				if count > 1 {
					t.Fatalf("automatic retry %s %d", name, count)
				}
			}
		})
	}
}
