package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

func TestRobotRecoveryUsesBoundRuntimeScopeForCacheHints(t *testing.T) {
	configuration := config{robotID: "robot-local"}
	if err := configuration.bindRuntimeIdentity("measured-runtime-robot"); err != nil {
		t.Fatal(err)
	}
	observer := recoveryObserver{robotID: configuration.robotID}
	observation, err := observer.Observe(context.Background(), "")
	if err != nil || observation.Context == nil || observation.Context.Scope != (agentcontext.Scope{RobotID: configuration.robotID}) {
		t.Fatalf("robot recovery lost the bound identity or invented a task: observation=%+v error=%v", observation, err)
	}
	var received []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		received, _ = io.ReadAll(request.Body)
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{
			"tool_calls": []any{map[string]any{"function": map[string]any{"name": "cannot_proceed", "arguments": `{"reason":"fixture refusal"}`}}},
		}}}})
	}))
	defer server.Close()
	request := actionloop.Request{Role: "recovery", Goal: "检查机器人当前状态", Observation: observation}
	snapshot, err := actionloop.SnapshotFor(request)
	if err != nil {
		t.Fatal(err)
	}
	request.ContextSnapshot = &snapshot
	decider := &actionloop.LLMDecider{BaseURL: server.URL, Model: "fixture-model", Client: server.Client(),
		Cache: modelroute.CachePolicy{Mode: "vllm", Namespace: "site-A"}}
	decision, err := decider.Decide(context.Background(), request)
	if err != nil || decision.Blocked != "fixture refusal" {
		t.Fatalf("valid robot-level recovery could not reach the model: decision=%+v error=%v", decision, err)
	}
	var body map[string]any
	if err := json.Unmarshal(received, &body); err != nil {
		t.Fatal(err)
	}
	if salt, _ := body["cache_salt"].(string); len(salt) != 64 || snapshot.ModelCall == nil || snapshot.ModelCall.CacheMode != "vllm" {
		t.Fatalf("robot scope did not reach cache wire/trace: body=%v trace=%+v", body, snapshot.ModelCall)
	}
	if snapshot.Scope != (agentcontext.Scope{RobotID: configuration.robotID}) {
		t.Fatalf("request invented a task identity: %+v", snapshot.Scope)
	}
	if exact, err := snapshot.ModelRequestBytes(); err != nil || string(exact) != string(received) {
		t.Fatalf("scoped model request lost exact transport bytes: %v", err)
	}
}

func TestLegacyRobotRecoveryWithoutIdentityKeepsUnscopedObservation(t *testing.T) {
	observation, err := (recoveryObserver{}).Observe(context.Background(), "")
	if err != nil || observation.Context != nil || observation.Summary == "" {
		t.Fatalf("legacy robot observation behavior changed: %+v error=%v", observation, err)
	}
	snapshot, err := actionloop.SnapshotFor(actionloop.Request{Goal: "诊断机器人", Observation: observation})
	if err != nil || snapshot.Scope != (agentcontext.Scope{}) {
		t.Fatalf("legacy observation fabricated scope: %+v error=%v", snapshot.Scope, err)
	}
}
