package actionloop_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

func TestModelReceivesAndTraceStoresExactContext(t *testing.T) {
	t.Setenv("TANGYING_AGENT_CONTEXT", "json")
	server, seen := modelServer(t, toolCallMessage("observe_scene", `{"source":"head","reason":"复核证据"}`))
	original := agentcontext.Document{SchemaVersion: agentcontext.Version, Role: "recovery", Goal: "原目标", Scope: agentcontext.Scope{TaskID: "t1", PlanRevision: 4}, Records: []agentcontext.Record{{ID: "proof-9", Kind: "verification", Statement: "UNKNOWN"}}}
	var calls []string
	loop := actionloop.Loop{Tools: offeredTools(&calls), Decider: newDecider(t, server), MaxRounds: 1, Observe: func(context.Context) (actionloop.Observation, error) {
		return actionloop.Observation{Summary: "需要观察", Context: &original, EvidenceIDs: []string{"proof-9"}}, nil
	}}
	result, err := loop.Run(context.Background(), "恢复当前任务")
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Rounds) == 0 || result.Rounds[0].Context == nil {
		t.Fatal("context not persisted")
	}
	snapshot := result.Rounds[0].Context
	messages := (*seen)[0]["messages"].([]any)
	actual := messages[len(messages)-1].(map[string]any)["content"].(string)
	if snapshot.Text != actual || snapshot.SHA256 != agentcontext.Hash(actual) {
		t.Fatal("trace and model input differ")
	}
	var doc agentcontext.Document
	if err := json.Unmarshal([]byte(actual), &doc); err != nil {
		t.Fatal(err)
	}
	if len(doc.Records) != 2 || doc.Scope.TaskID != "t1" || len(original.Records) != 1 {
		t.Fatal("lost provenance or mutated observer")
	}
}

func TestHistoryRetainsArgumentsForFailureReflection(t *testing.T) {
	t.Setenv("TANGYING_AGENT_CONTEXT", "json")
	snapshot, err := actionloop.SnapshotFor(actionloop.Request{Goal: "recovery", Round: 2, Observation: observation(), History: []actionloop.Round{{Round: 1, Tool: "pick", Arguments: map[string]any{"offset": 0.017}, Verdict: "UNSATISFIED", Code: "NO_OBJECT"}}})
	if err != nil {
		t.Fatal(err)
	}
	var doc agentcontext.Document
	_ = json.Unmarshal([]byte(snapshot.Text), &doc)
	if doc.Attempts[0].Arguments["offset"] != 0.017 {
		t.Fatal("lost exact failed parameters")
	}
}
