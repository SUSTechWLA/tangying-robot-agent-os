package tasks

import (
	"encoding/json"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"testing"
	"time"
)

func TestDecisionViewPreservesSourceBindingsAndMissingMetadata(t *testing.T) {
	t.Setenv("TANGYING_AGENT_CONTEXT", "factorial")
	task := Task{ID: "t", Request: "恢复当前任务", CurrentRevision: 5, AggregateVersion: 900, Events: []TaskEvent{{Sequence: 7, Type: "tool_started", StepID: "s", OccurredAt: time.Unix(12, 0), Payload: map[string]any{"attempt_id": "attempt-2", "agent_context": map[string]any{"recursive": "omit"}}}}}
	p := DecisionContextFor(task, "recovery", time.Unix(100, 0))
	if p.Snapshot.LedgerSequence == nil || *p.Snapshot.LedgerSequence != 7 {
		t.Fatal("aggregate version is not ledger sequence")
	}
	r := p.Records[len(p.Records)-1]
	if r.StepID == nil || *r.StepID != "s" || r.AttemptID == nil || *r.AttemptID != "attempt-2" {
		t.Fatal("lost bindings")
	}
	if r.ObservedMS != nil || r.Scope.Revision != nil || r.Scope.RobotID != nil {
		t.Fatal("fabricated sensor time or scope")
	}
	data, _ := json.Marshal(r.Payload)
	if string(data) == "" {
		t.Fatal("lost original event")
	}
	if _, ok := r.Payload["data"].(map[string]any)["agent_context"]; ok {
		t.Fatal("recursive context retained")
	}
	first, _ := json.Marshal(p)
	for i := 0; i < 20; i++ {
		again, _ := json.Marshal(DecisionContextFor(task, "recovery", time.Unix(100, 0)))
		if string(again) != string(first) {
			t.Fatal("nondeterministic view")
		}
	}
	d := ContextFor(task, "recovery", time.Unix(100, 0))
	view, err := agentcontext.Project(d, "recovery")
	if err != nil {
		t.Fatal(err)
	}
	if view.Factors == nil || view.SchemaVersion != agentcontext.DecisionVersion || view.SemanticSHA256 == "" {
		t.Fatal("missing exact projection trace")
	}
}
