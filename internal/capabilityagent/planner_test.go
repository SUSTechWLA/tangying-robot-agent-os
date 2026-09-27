package capabilityagent

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

func TestGoalModelReceivesCompleteSchemasAndCannotClaimPhysicalCompletion(t *testing.T) {
	saw := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		wire, _ := json.Marshal(body["tools"])
		for _, field := range []string{"calibration.get", "mapping.build", "enum", "explore", "operation", "verification"} {
			if !strings.Contains(string(wire), field) {
				t.Errorf("model catalog lost %s", field)
			}
		}
		saw = true
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"id":"plan","type":"function","function":{"name":"propose_capability_plan","arguments":"{\"calls\":[{\"tool\":\"calibration.get\",\"arguments\":{}}]}"}}]}}]}`))
	}))
	defer server.Close()
	planner := &Planner{Provider: &fakeProvider{}, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "configured-goal-model"}}
	bundle, handled, err := planner.PlanGoal(context.Background(), "请帮我检查一下传感器标定")
	if err != nil || !handled || !saw || bundle.Source != orchestration.SourceLLM || bundle.Capabilities.Calls[0].Tool != "calibration.get" {
		t.Fatalf("plan=%#v handled=%v err=%v", bundle, handled, err)
	}
}
func TestOfflineGoalLimitsAreNotDiscarded(t *testing.T) {
	calls, ok := literalCalls("运行标定，探索建图最多行驶12米最多1轮，然后去厨房")
	if !ok || len(calls) != 3 || calls[1].Arguments["maxTravelM"] != float64(12) || calls[1].Arguments["maxLegs"] != 1 {
		t.Fatalf("calls=%#v handled=%v", calls, ok)
	}
	if _, ok := literalCalls("建图，但不要移动机器人"); ok {
		t.Fatal("negative constraint discarded")
	}
}

func TestExactGoalAvoidsModelInferenceWithoutDiscardingBudgets(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("exact request invoked model") }))
	defer server.Close()
	planner := &Planner{Provider: &fakeProvider{}, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "goal"}}
	bundle, handled, err := planner.PlanGoal(context.Background(), "检查标定，建图")
	if err != nil || !handled || bundle.Source != orchestration.SourceDeterministic || bundle.Capabilities.Calls[1].Tool != "mapping.build" {
		t.Fatalf("%+v %v", bundle, err)
	}
}
