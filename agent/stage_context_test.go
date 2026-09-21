package agent

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGoalStageFormatsModelInputButRetainsRequest(t *testing.T) {
	for _, mode := range []string{"legacy", "stage", "json"} {
		t.Run(mode, func(t *testing.T) { checkGoalInput(t, mode) })
	}
}

func checkGoalInput(t *testing.T, mode string) {
	t.Setenv("TANGYING_AGENT_CONTEXT", mode)
	raw := "我想要桌上的红色水杯"
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
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"function":{"name":"fetch","arguments":"{\"object\":{\"category\":\"cup\",\"color\":\"red\"}}"}}]}}]}`))
	}))
	defer server.Close()
	planner := newLLMPlanner(Config{BaseURL: server.URL, Model: "test-model", HTTPClient: server.Client()})
	if _, err := planner.Plan(raw); err != nil {
		t.Fatal(err)
	}
	if mode == "legacy" {
		if received != raw {
			t.Fatal("default request changed")
		}
		return
	}
	if received == raw || !strings.Contains(received, raw) || !strings.Contains(received, "goal") {
		t.Fatalf("lost original request or stage: %s", received)
	}
	if mode == "json" && !json.Valid([]byte(received)) {
		t.Fatal("explicit JSON override was not applied")
	}
}
