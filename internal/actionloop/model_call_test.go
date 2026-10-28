package actionloop_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func cacheRequest(t *testing.T) actionloop.Request {
	t.Helper()
	doc := agentcontext.Document{SchemaVersion: agentcontext.Version, Role: "recovery", Scope: agentcontext.Scope{TaskID: "task-A", RobotID: "robot-A", PlanRevision: 2}, Goal: "检查通道"}
	r := actionloop.Request{Goal: doc.Goal, Role: "recovery", Tools: offeredTools(new([]string)), Observation: actionloop.Observation{Summary: "前方有障碍", Context: &doc}}
	snapshot, err := actionloop.SnapshotFor(r)
	if err != nil {
		t.Fatal(err)
	}
	r.ContextSnapshot = &snapshot
	return r
}

func TestCacheHintsKeepRulesStableAndScopeIdentity(t *testing.T) {
	server, seen := modelServer(t, toolCallMessage("observe_scene", `{"source":"head"}`))
	d := newDecider(t, server)
	d.Cache = modelroute.CachePolicy{Mode: "openai", Namespace: "site-A"}
	r := cacheRequest(t)
	if _, err := d.Decide(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	initial := (*seen)[0]
	key := initial["prompt_cache_key"]
	if len(key.(string)) != 64 || initial["cache_salt"] != nil {
		t.Fatal("incorrect backend hint")
	}
	stableSystem := initial["messages"].([]any)[0].(map[string]any)["content"]
	if strings.Contains(stableSystem.(string), r.Goal) {
		t.Fatal("dynamic goal in system prefix")
	}
	// Observation/goal change must change the exact request, not the stable rules.
	second := cacheRequest(t)
	second.Goal = "检查另一通道"
	second.Observation.Context.Goal = second.Goal
	snapshot, err := actionloop.SnapshotFor(second)
	if err != nil {
		t.Fatal(err)
	}
	second.ContextSnapshot = &snapshot
	if _, err := d.Decide(context.Background(), second); err != nil {
		t.Fatal(err)
	}
	if (*seen)[1]["prompt_cache_key"] != key || (*seen)[1]["messages"].([]any)[0].(map[string]any)["content"] != stableSystem {
		t.Fatal("unstable rule cache identity")
	}
	if r.ContextSnapshot.ModelRequestSHA256 == second.ContextSnapshot.ModelRequestSHA256 || !strings.Contains(second.ContextSnapshot.ModelRequestJSON, second.Goal) {
		t.Fatal("new goal lost from request")
	}
	for _, mutate := range []func(*actionloop.Request, *actionloop.LLMDecider){
		func(r *actionloop.Request, d *actionloop.LLMDecider) { r.ContextSnapshot.Scope.RobotID = "robot-B" },
		func(r *actionloop.Request, d *actionloop.LLMDecider) { r.ContextSnapshot.Scope.TaskID = "task-B" },
		func(r *actionloop.Request, d *actionloop.LLMDecider) { r.ContextSnapshot.Scope.PlanRevision++ },
		func(r *actionloop.Request, d *actionloop.LLMDecider) { r.Role = "planning" },
		func(r *actionloop.Request, d *actionloop.LLMDecider) { r.Tools[0].Description += " version two" },
		func(r *actionloop.Request, d *actionloop.LLMDecider) { d.Model = "other-model" },
		func(r *actionloop.Request, d *actionloop.LLMDecider) { d.Cache.Namespace = "other-site" },
	} {
		changed := cacheRequest(t)
		dd := *d
		mutate(&changed, &dd)
		if _, err := dd.Decide(context.Background(), changed); err != nil {
			t.Fatal(err)
		}
		if (*seen)[len(*seen)-1]["prompt_cache_key"] == key {
			t.Fatal("cache identity crossed a scope/tool/model boundary")
		}
	}
	d.Cache.Mode = "vllm"
	if _, err := d.Decide(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	wire := (*seen)[len(*seen)-1]
	if wire["cache_salt"] != key || wire["prompt_cache_key"] != nil {
		t.Fatal("vllm wire format")
	}
	d.Cache = modelroute.CachePolicy{}
	if _, err := d.Decide(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	wire = (*seen)[len(*seen)-1]
	if wire["cache_salt"] != nil || wire["prompt_cache_key"] != nil {
		t.Fatal("default must omit hints")
	}
	d.Cache = modelroute.CachePolicy{Mode: "vllm", Namespace: "site-A"}
	r.ContextSnapshot.Scope = agentcontext.Scope{}
	before := len(*seen)
	if _, err := d.Decide(context.Background(), r); err == nil || len(*seen) != before {
		t.Fatal("unscoped cache call reached server")
	}
}

func TestModelUsageDistinguishesMissingZeroInvalidAndSurvivesSQLite(t *testing.T) {
	cases := []struct{ name, raw, status string }{
		{"missing", "", "missing"}, {"null", "null", "missing"}, {"empty", "{}", "missing"},
		{"zero", `{"prompt_tokens":100,"prompt_tokens_details":{"cached_tokens":0}}`, "valid"},
		{"partial", `{"prompt_tokens_details":{"cached_tokens":32}}`, "valid"},
		{"full", `{"prompt_tokens":100,"completion_tokens":12,"total_tokens":112,"prompt_tokens_details":{"cached_tokens":64,"cache_write_tokens":36}}`, "valid"},
		{"negative", `{"prompt_tokens":-1}`, "invalid"}, {"fraction", `{"prompt_tokens":0.5}`, "invalid"},
		{"overflow", `{"prompt_tokens":9223372036854775808}`, "invalid"}, {"unsafe-json-number", `{"prompt_tokens":9007199254740992}`, "invalid"},
		{"impossible-cache", `{"prompt_tokens":10,"prompt_tokens_details":{"cached_tokens":11}}`, "invalid"},
		{"wrong-shape", `[]`, "invalid"}, {"wrong-details", `{"prompt_tokens_details":"bad"}`, "invalid"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var sent []byte
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				sent, _ = io.ReadAll(r.Body)
				reply := map[string]any{"choices": []any{map[string]any{"message": toolCallMessage("observe_scene", `{"source":"head"}`)}}}
				if tc.raw != "" {
					reply["usage"] = json.RawMessage(tc.raw)
				}
				_ = json.NewEncoder(w).Encode(reply)
			}))
			defer server.Close()
			r := cacheRequest(t)
			if _, err := newDecider(t, server).Decide(context.Background(), r); err != nil {
				t.Fatal(err)
			}
			p := r.ContextSnapshot
			c := p.ModelCall
			if c == nil || c.Outcome != "accepted" || c.HTTPStatus != 200 || c.UsageStatus != tc.status || c.RequestBytes != len(sent) || c.ResponseBytes == 0 || c.ResponseSHA256 == "" || c.DurationMS < 0 {
				t.Fatalf("bad telemetry: %+v", c)
			}
			if tc.status != "valid" && c.Usage != nil {
				t.Fatal("invalid/missing usage became numbers")
			}
			if tc.name == "zero" && (c.Usage.CachedTokens == nil || *c.Usage.CachedTokens != 0) {
				t.Fatal("explicit zero lost")
			}
			if tc.name == "partial" && c.Usage.PromptTokens != nil {
				t.Fatal("unknown denominator became zero")
			}
			actual, err := p.ModelRequestBytes()
			if err != nil || string(actual) != string(sent) {
				t.Fatalf("trace lost exact wire: %v", err)
			}
			path := t.TempDir() + "/trace.db"
			store, err := sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			encoded, _ := json.Marshal(p)
			var data map[string]any
			_ = json.Unmarshal(encoded, &data)
			task := &tasks.Task{ID: "telemetry", AggregateVersion: 1, Events: []tasks.TaskEvent{{Type: "model.call", Payload: data}}}
			if err := store.Create(context.Background(), task); err != nil {
				t.Fatal(err)
			}
			if err := store.Close(); err != nil {
				t.Fatal(err)
			}
			store, err = sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			loaded, err := store.Get(context.Background(), task.ID)
			if err != nil {
				t.Fatal(err)
			}
			encoded, _ = json.Marshal(loaded.Events[0].Payload)
			var restored agentcontext.Projection
			if err := json.Unmarshal(encoded, &restored); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(restored.ModelCall, c) {
				t.Fatal("telemetry changed across durable reopen")
			}
		})
	}
}

func TestFailedModelCallsAreTracedAndDoNotRetainPriorUsage(t *testing.T) {
	for _, tc := range []struct{ name, outcome string }{{"http", "http_error"}, {"bad-json", "response_error"}, {"unoffered", "invalid_decision"}, {"timeout", "timeout"}, {"cancel", "canceled"}} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = io.Copy(io.Discard, r.Body)
				switch tc.name {
				case "http":
					http.Error(w, "unavailable", 503)
				case "bad-json":
					_, _ = w.Write([]byte(`{"bad`))
				case "unoffered":
					_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": toolCallMessage("unoffered", `{}`)}}})
				default:
					<-r.Context().Done()
				}
			}))
			defer server.Close()
			r := cacheRequest(t)
			r.ContextSnapshot.ModelCall = &agentcontext.ModelCall{UsageStatus: "valid", Outcome: "accepted"}
			d := newDecider(t, server)
			d.Timeout = 40 * time.Millisecond
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if tc.name == "cancel" {
				cancel()
			}
			if _, err := d.Decide(ctx, r); err == nil {
				t.Fatal("failure accepted")
			}
			c := r.ContextSnapshot.ModelCall
			if c == nil || c.Outcome != tc.outcome || c.Usage != nil {
				t.Fatalf("wrong failure telemetry: %+v", c)
			}
		})
	}
}

func TestConfigurationFailureClearsReusedCallTrace(t *testing.T) {
	r := cacheRequest(t)
	r.ContextSnapshot.ModelCall = &agentcontext.ModelCall{Outcome: "accepted"}
	r.ContextSnapshot.ModelRequestJSON = "prior"
	r.ContextSnapshot.ModelRequestSHA256 = "prior"
	if _, err := (&actionloop.LLMDecider{}).Decide(context.Background(), r); err == nil {
		t.Fatal("empty configuration accepted")
	}
	if r.ContextSnapshot.ModelCall != nil || r.ContextSnapshot.ModelRequestJSON != "" || r.ContextSnapshot.ModelRequestSHA256 != "" {
		t.Fatal("failure retained prior successful call")
	}
}
