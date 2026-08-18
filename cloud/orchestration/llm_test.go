package orchestration

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func validPlansJSON() string {
	return `{"plans":[{"id":"task-1","goal":"place object","steps":[
		{"id":"observe","skill":"observe_scene","arguments":{}},
		{"id":"resolve","skill":"resolve_targets","arguments":{"objectId":"@object","destinationId":"@destination"},"dependsOn":["observe"]},
		{"id":"plan_grasp","skill":"plan_grasp","arguments":{"objectId":"@object"},"dependsOn":["resolve"]},
		{"id":"pick","skill":"manipulation.pick","arguments":{"targetRef":"@object"},"dependsOn":["plan_grasp"]},
		{"id":"verify_grasp","skill":"verify_grasp","arguments":{"objectId":"@object"},"dependsOn":["pick"]},
		{"id":"place","skill":"manipulation.place","arguments":{"targetRef":"@destination"},"dependsOn":["verify_grasp"]},
		{"id":"verify_place","skill":"verify_placement","arguments":{"objectId":"@object","destinationId":"@destination"},"dependsOn":["place"]}
	]}]}`
}

func plannerConfig(serverURL string, samples int) Config {
	return Config{
		Provider: "openai",
		BaseURL:  serverURL,
		APIKey:   "test-key",
		Model:    "test-model",
		Samples:  samples,
	}
}

func TestLLMPlannerAcceptsCatalogGeneratedPlan(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]any{"content": validPlansJSON()}}},
		})
	}))
	defer server.Close()

	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	planner := New(manipulation.Catalog(), plannerConfig(server.URL, 1))
	bundle, err := planner.Plan("把红色杯子放进右侧收纳盒", parsed)
	if err != nil {
		t.Fatal(err)
	}
	if !bundle.LLMGenerated() || bundle.Source != SourceLLM {
		t.Fatalf("bundle = %+v", bundle)
	}
	if len(bundle.Plans) != 1 || len(bundle.Plans[0].Steps) != 7 {
		t.Fatalf("plans = %+v", bundle.Plans)
	}
}

func TestLLMPlannerFallsBackDeterministicWhenPlanInvalid(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]any{"content": `{"plans":[{"id":"bad","steps":[{"id":"shell","skill":"shell.execute","arguments":{}}]}]}`}}},
		})
	}))
	defer server.Close()

	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	planner := New(manipulation.Catalog(), plannerConfig(server.URL, 1))
	bundle, err := planner.Plan("把红色杯子放进右侧收纳盒", parsed)
	if err != nil {
		t.Fatal(err)
	}
	if bundle.Source != SourceDeterministic || bundle.Attempts != 1 || len(bundle.Rejections) == 0 {
		t.Fatalf("bundle = %+v", bundle)
	}
}

func TestLLMPlannerUsesSelfConsistencyMajority(t *testing.T) {
	var call atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		value := call.Add(1)
		content := validPlansJSON()
		if value == 2 {
			content = `{"plans":[{"id":"task-1","goal":"place object","steps":[
				{"id":"observe","skill":"observe_scene","arguments":{}},
				{"id":"resolve","skill":"resolve_targets","arguments":{"objectId":"@object","destinationId":"@destination"},"dependsOn":["observe"]},
				{"id":"plan_grasp","skill":"plan_grasp","arguments":{"objectId":"@object"},"dependsOn":["resolve"]},
				{"id":"pick","skill":"manipulation.pick","arguments":{"targetRef":"@object"},"dependsOn":["plan_grasp"]},
				{"id":"verify_grasp","skill":"verify_grasp","arguments":{"objectId":"@object"},"dependsOn":["pick"]},
				{"id":"place","skill":"manipulation.place","arguments":{"targetRef":"@destination"},"dependsOn":["verify_grasp"]},
				{"id":"verify_place","skill":"verify_placement","arguments":{"objectId":"@object","destinationId":"@destination"},"dependsOn":["place"]},
				{"id":"extra_check","skill":"observe_scene","arguments":{},"dependsOn":["verify_place"]}
			]}]}`
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": map[string]any{"content": content}}},
		})
	}))
	defer server.Close()

	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	planner := New(manipulation.Catalog(), plannerConfig(server.URL, 3))
	bundle, err := planner.Plan("把红色杯子放进右侧收纳盒", parsed)
	if err != nil {
		t.Fatal(err)
	}
	if bundle.Source != SourceConsensus || bundle.AcceptedCandidates != 3 {
		t.Fatalf("bundle = %+v", bundle)
	}
	if len(bundle.Plans[0].Steps) != 7 {
		t.Fatalf("steps = %d, want majority plan with 7 steps", len(bundle.Plans[0].Steps))
	}
}

func TestMetricsRewardAcceptedLLMPlansAndExecutionSuccess(t *testing.T) {
	metrics := CalculateMetrics([]TaskRecord{
		{Source: SourceDeterministic, State: "SUCCEEDED"},
		{Source: SourceLLM, State: "SUCCEEDED", Attempts: 1, AcceptedCandidates: 1},
		{Source: SourceConsensus, State: "FAILED", Attempts: 3, AcceptedCandidates: 2, Sequence: true},
	})
	if metrics.TotalTasks != 3 || metrics.SequenceTasks != 1 {
		t.Fatalf("metrics = %+v", metrics)
	}
	if metrics.LLMPlanRate != 2.0/3.0 || metrics.EndToEndSuccessRate != 2.0/3.0 {
		t.Fatalf("metrics = %+v", metrics)
	}
	if metrics.OrchestrationScore <= 0 {
		t.Fatalf("score = %f", metrics.OrchestrationScore)
	}
}
