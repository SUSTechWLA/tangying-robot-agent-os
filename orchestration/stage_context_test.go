package orchestration

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestPlanningStageFormatsInputAndKeepsValidation(t *testing.T) {
	for _, mode := range []string{"legacy", "stage", "json"} {
		t.Run(mode, func(t *testing.T) { checkPlanningInput(t, mode) })
	}
}

func checkPlanningInput(t *testing.T, mode string) {
	t.Setenv("TANGYING_AGENT_CONTEXT", mode)
	var received string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Messages []struct {
				Role    string
				Content string
			}
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		received = body.Messages[1].Content
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{"content": `{"plans":[]}`}}}})
	}))
	defer server.Close()
	planner := LLMPlanner{baseURL: server.URL, model: "test-model", client: server.Client(), samples: 1}
	result, err := planner.Plan("把杯子放到托盘", manipulation.Intent{}, World{})
	if err != nil {
		t.Fatal(err)
	}
	if mode == "legacy" && received != "把杯子放到托盘" {
		t.Fatal("default request changed")
	}
	if mode != "legacy" && (!strings.Contains(received, "planning") || !strings.Contains(received, "把杯子放到托盘")) {
		t.Fatal(received)
	}
	if mode == "json" && !json.Valid([]byte(received)) {
		t.Fatal("explicit JSON override was not applied")
	}
	if result.Source != SourceDeterministic {
		t.Fatal("stage formatting bypassed plan validation")
	}
}
