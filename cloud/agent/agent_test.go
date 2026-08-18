package agent

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
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
	got, err := parser.Parse("请把红色水杯递给我")
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
	got, err := parser.Parse("把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来")
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
