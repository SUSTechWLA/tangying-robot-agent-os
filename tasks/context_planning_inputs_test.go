package tasks

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

func TestContextKeepsPlanningFactsWithoutRecursingAuditInputs(t *testing.T) {
	oldText := strings.Repeat("previous-model-input-must-not-recur", 10000)
	request := `{"messages":[{"content":"old-request-body-must-not-recur"}]}`
	p := &agentcontext.Projection{Scope: agentcontext.Scope{RobotID: "robot-a"}, Text: oldText,
		SHA256: agentcontext.Hash(oldText), ModelRequestJSON: request, ModelRequestSHA256: agentcontext.Hash(request)}
	bundle := &orchestration.Bundle{Capabilities: &capability.Plan{RobotID: "robot-a", CatalogRevision: "catalog-a",
		Calls: []capability.Call{{Tool: "navigation.status", Arguments: map[string]any{"identity": "approved-call"}}},
		PlanningTrace: []capability.PlanningStep{{Round: 1, Tool: "semantic.locations", Verdict: "SATISFIED",
			Arguments: map[string]any{"view": "original-read-arguments"}, Result: map[string]any{"room": "original-planning-result"}, Context: p}},
	}}
	task := Task{ID: "task-a", Request: "查看当前地图", CurrentRevision: 1, Plan: bundle}
	revision := RevisionRecord{Revision: TaskRevision{TaskID: task.ID, Revision: 1, Request: task.Request, Plan: bundle}}
	before, err := json.Marshal(bundle)
	if err != nil {
		t.Fatal(err)
	}
	d := ContextForRevisions(task, []RevisionRecord{revision}, "recovery", time.Unix(10, 0))
	wire, err := json.Marshal(d)
	if err != nil {
		t.Fatal(err)
	}
	for _, marker := range []string{"previous-model-input-must-not-recur", "old-request-body-must-not-recur", "model_request_json"} {
		if strings.Contains(string(wire), marker) {
			t.Fatalf("previous input recurred in new source: %s", marker)
		}
	}
	for _, required := range []string{"approved-call", "original-read-arguments", "original-planning-result", p.SHA256, p.ModelRequestSHA256} {
		if !strings.Contains(string(wire), required) {
			t.Fatalf("planning fact or audit source lost: %s", required)
		}
	}
	after, err := json.Marshal(bundle)
	if err != nil || string(before) != string(after) || bundle.Capabilities.PlanningTrace[0].Context != p {
		t.Fatal("source projection changed immutable planning inputs")
	}
	if len(wire) > 20000 {
		t.Fatalf("audit payload leaked into next context: %d bytes", len(wire))
	}
}
