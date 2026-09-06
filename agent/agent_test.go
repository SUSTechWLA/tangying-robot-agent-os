package agent

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestDefaultParserUsesDeterministicFallback(t *testing.T) {
	parser := NewParser(Config{})
	got, err := parser.Parse("把红色杯子拿过来")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != manipulation.ActionFetch || got.Destination.Category != manipulation.CategoryDeliveryTray {
		t.Fatalf("intent = %+v", got)
	}
}

func TestKnownHandoffCannotBeRewrittenByTheLanguageModel(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"function":{"name":"pick_and_place","arguments":"{\"object\":{\"category\":\"block\",\"color\":\"blue\"},\"destination_relation\":\"left_side\"}"}}]}}]}`))
	}))
	defer server.Close()
	parser := NewParser(Config{Provider: ProviderOpenAI, BaseURL: server.URL, APIKey: "test-key", Model: "test-model"})
	got, err := parser.Parse("让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区")
	if err != nil {
		t.Fatal(err)
	}
	steps := got.Tasks()
	if len(steps) != 2 || steps[0].RobotID != "robot-1" || steps[1].RobotID != "robot-2" || steps[0].Destination.Category != "handoff_zone" || steps[0].Object.Attributes["color"] != "red" {
		t.Fatalf("explicit instructions were rewritten: %+v", steps)
	}
	if calls.Load() != 0 {
		t.Fatal("known instructions should not depend on a remote rewrite")
	}
}

func TestLanguageModelCannotBypassNegationAndConditions(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"function":{"name":"fetch","arguments":"{\"object\":{\"category\":\"cup\",\"color\":\"red\"}}"}}]}}]}`))
	}))
	defer server.Close()
	parser := NewParser(Config{Provider: ProviderOpenAI, BaseURL: server.URL, APIKey: "test-key", Model: "test-model"})
	for _, request := range []string{"Do not bring me the red cup", "Don’t bring me the red cup", "如果有人经过就把红色杯子拿过来"} {
		if _, err := parser.Parse(request); err == nil {
			t.Fatalf("model bypassed request boundary: %s", request)
		}
	}
}

func TestOpenAIParserUsesToolCall(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/chat/completions" {
			t.Fatalf("path = %q", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer test-key" {
			t.Fatalf("authorization = %q", r.Header.Get("Authorization"))
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{
				"message": map[string]any{
					"tool_calls": []map[string]any{{
						"function": map[string]any{
							"name":      "fetch",
							"arguments": `{"object":{"category":"cup","color":"red"}}`,
						},
					}},
				},
			}},
		})
	}))
	defer server.Close()

	parser := NewParser(Config{Provider: ProviderOpenAI, BaseURL: server.URL, APIKey: "test-key", Model: "test-model"})
	got, err := parser.Parse("我想要桌上的红色水杯")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != manipulation.ActionFetch || got.Object.Attributes["color"] != "red" {
		t.Fatalf("intent = %+v", got)
	}
	if got.Destination.Relation != "front_side" {
		t.Fatalf("destination = %+v", got.Destination)
	}
}

func TestOpenAIParserUsesMultipleToolCallsAsSequence(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{
				"message": map[string]any{
					"tool_calls": []map[string]any{
						{
							"function": map[string]any{
								"name":      "pick_and_place",
								"arguments": `{"object":{"category":"cup","color":"red"},"destination_relation":"right_side"}`,
							},
						},
						{
							"function": map[string]any{
								"name":      "fetch",
								"arguments": `{"object":{"category":"bottle","color":"blue"}}`,
							},
						},
					},
				},
			}},
		})
	}))
	defer server.Close()

	parser := NewParser(Config{Provider: ProviderOpenAI, BaseURL: server.URL, APIKey: "test-key", Model: "test-model"})
	got, err := parser.Parse("请收好红色杯子并给我蓝色瓶子")
	if err != nil {
		t.Fatal(err)
	}
	tasks := got.Tasks()
	if len(tasks) != 2 {
		t.Fatalf("tasks = %+v", tasks)
	}
	if tasks[0].Action != manipulation.ActionPickAndPlace || tasks[1].Action != manipulation.ActionFetch {
		t.Fatalf("tasks = %+v", tasks)
	}
	if tasks[1].Object.Category != "bottle" || tasks[1].Object.Attributes["color"] != "blue" {
		t.Fatalf("second task = %+v", tasks[1])
	}
}

func TestOpenAIParserFallsBackWhenModelReturnsInvalidIntent(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{
				"message": map[string]any{
					"tool_calls": []map[string]any{{
						"function": map[string]any{
							"name":      "cook_dinner",
							"arguments": `{}`,
						},
					}},
				},
			}},
		})
	}))
	defer server.Close()

	parser := NewParser(Config{Provider: ProviderOpenAI, BaseURL: server.URL, APIKey: "test-key", Model: "test-model"})
	got, err := parser.Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != manipulation.ActionPickAndPlace || got.Destination.Relation != "right_side" {
		t.Fatalf("intent = %+v", got)
	}
}

func TestOpenAIParserFallsBackOnTransportFailure(t *testing.T) {
	parser := NewParser(Config{Provider: ProviderOpenAI, BaseURL: "http://127.0.0.1:1", APIKey: "test-key", Model: "test-model"})
	got, err := parser.Parse("把蓝色杯子拿给我")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != manipulation.ActionFetch {
		t.Fatalf("intent = %+v", got)
	}
}

func TestLLMFallbackStillRejectsUnknownRequests(t *testing.T) {
	parser := NewParser(Config{})
	if _, err := parser.Parse("帮我做晚饭"); err == nil {
		t.Fatal("expected unsupported intent error")
	}
}
