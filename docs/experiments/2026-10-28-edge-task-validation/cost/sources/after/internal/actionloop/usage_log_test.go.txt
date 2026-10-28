package actionloop_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

func TestLoopAccountingFailureStopsAfterOneRecordedHTTPAttempt(t *testing.T) {
	path := filepath.Join(t.TempDir(), "usage.jsonl")
	var requests atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		_, _ = io.Copy(io.Discard, r.Body)
		if err := os.Chmod(path, 0o644); err != nil {
			t.Error(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": toolCallMessage("observe_scene", `{"source":"head"}`)}}, "usage": map[string]any{"prompt_tokens": 100, "completion_tokens": 20}})
	}))
	defer server.Close()
	d := newDecider(t, server)
	d.UsageFile = path
	var toolsCalled []string
	observation := cacheRequest(t).Observation
	loop := actionloop.Loop{Decider: d, Tools: offeredTools(&toolsCalled), ContextRole: "recovery",
		Observe: func(context.Context) (actionloop.Observation, error) { return observation, nil }}
	outcome, err := loop.Run(context.Background(), "核验当前场景")
	t.Logf("LOOP_USAGE_FAULT httpRequests=%d rounds=%d toolCalls=%d ledgerError=%v", requests.Load(), len(outcome.Rounds), len(toolsCalled), errors.Is(err, actionloop.ErrModelUsageLog))
	if !errors.Is(err, actionloop.ErrModelUsageLog) || !outcome.Escalated || outcome.Completed || requests.Load() != 1 || len(outcome.Rounds) != 1 || len(toolsCalled) != 0 {
		t.Fatalf("fatal accounting failure retried or consumed candidate: outcome=%+v err=%v", outcome, err)
	}
	call := outcome.Rounds[0].Context.ModelCall
	if call == nil || call.HTTPStatus != 200 || call.Outcome != "accepted" || call.UsageLogStatus != "write_error" || call.Usage == nil || *call.Usage.PromptTokens != 100 {
		t.Fatalf("original HTTP accounting lost: %+v", call)
	}
}

type recordedUsage struct {
	SchemaVersion string                 `json:"schema_version"`
	Event         string                 `json:"event"`
	Call          agentcontext.ModelCall `json:"call"`
	Scope         agentcontext.Scope     `json:"scope"`
}

func readUsage(t *testing.T, path string) []recordedUsage {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var result []recordedUsage
	for _, line := range strings.Split(strings.TrimSuffix(string(body), "\n"), "\n") {
		var event recordedUsage
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			t.Fatal(err)
		}
		if event.SchemaVersion != "agent.model-usage.v1" {
			t.Fatalf("unexpected accounting schema: %s", event.SchemaVersion)
		}
		result = append(result, event)
	}
	return result
}

func TestOutputCapAndUsageLedgerRecordExactCallWithoutSecrets(t *testing.T) {
	server, seen := modelServer(t, toolCallMessage("observe_scene", `{"source":"head"}`))
	d := newDecider(t, server)
	d.OutputTokens, d.Stage = 768, modelroute.Recovery
	d.UsageFile = filepath.Join(t.TempDir(), "usage.jsonl")
	d.APIKey = "never-record-header-secret"
	r := cacheRequest(t)
	text := r.ContextSnapshot.Text
	if _, err := d.Decide(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	if (*seen)[0]["max_tokens"] != float64(768) || r.ContextSnapshot.Compaction.Budget.OutputTokens != 768 || r.ContextSnapshot.Text != text {
		t.Fatal("effective output reserve must match wire and metadata without rewriting projection text")
	}
	events := readUsage(t, d.UsageFile)
	call := r.ContextSnapshot.ModelCall
	if len(events) != 2 || events[0].Event != "start" || events[1].Event != "result" || events[0].Call.CallID != call.CallID || events[1].Call.CallID != call.CallID || len(call.EndpointSHA256) != 64 || call.UsageLogStatus != "recorded" || call.Stage != modelroute.Recovery {
		t.Fatalf("incomplete accounting: %+v", events)
	}
	if events[1].Call.UsageStatus != "missing" || events[1].Call.Usage != nil || events[1].Scope.TaskID != "task-A" {
		t.Fatal("missing usage converted to zero or scope lost")
	}
	body, _ := os.ReadFile(d.UsageFile)
	for _, secret := range []string{d.APIKey, d.BaseURL, r.Goal, "observe_scene", "model_request_json"} {
		if strings.Contains(string(body), secret) {
			t.Fatalf("ledger leaked content: %s", secret)
		}
	}
	info, _ := os.Stat(d.UsageFile)
	if info.Mode().Perm() != 0o600 {
		t.Fatal("ledger permissions")
	}
}

func TestOutputLimitRecordsKnownUsageAndRejectsEvenParseableDecision(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": toolCallMessage("observe_scene", `{"source":"head"}`), "finish_reason": "length"}}, "usage": map[string]any{"prompt_tokens": 100, "completion_tokens": 128}})
	}))
	defer server.Close()
	d := newDecider(t, server)
	d.OutputTokens, d.UsageFile = 128, filepath.Join(t.TempDir(), "usage.jsonl")
	r := cacheRequest(t)
	decision, err := d.Decide(context.Background(), r)
	if err == nil || decision.Tool != "" {
		t.Fatal("truncated decision executed")
	}
	call := readUsage(t, d.UsageFile)[1].Call
	if call.Outcome != "output_limit" || !call.ResponseTruncated || call.Usage == nil || *call.Usage.CompletionTokens != 128 || call.Usage.TotalTokens != nil {
		t.Fatalf("truncation accounting lost: %+v", call)
	}
}

func TestUnsafeUsageFileRefusesHTTPWithoutChangingExistingData(t *testing.T) {
	for _, kind := range []string{"symlink", "hardlink", "permissions", "partial", "directory", "relative"} {
		t.Run(kind, func(t *testing.T) {
			var called atomic.Int64
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called.Add(1) }))
			defer server.Close()
			dir := t.TempDir()
			path, original := filepath.Join(dir, "usage.jsonl"), filepath.Join(dir, "original")
			if err := os.WriteFile(original, []byte("preserve-me\n"), 0o600); err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "symlink":
				if err := os.Symlink(original, path); err != nil {
					t.Fatal(err)
				}
			case "hardlink":
				if err := os.Link(original, path); err != nil {
					t.Fatal(err)
				}
			case "permissions":
				if err := os.WriteFile(path, []byte("{}\n"), 0o644); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(path, 0o644); err != nil {
					t.Fatal(err)
				}
			case "partial":
				if err := os.WriteFile(path, []byte(`{"event":`), 0o600); err != nil {
					t.Fatal(err)
				}
			case "directory":
				if err := os.Mkdir(path, 0o700); err != nil {
					t.Fatal(err)
				}
			case "relative":
				path = "relative-usage.jsonl"
			}
			d := newDecider(t, server)
			d.UsageFile = path
			r := cacheRequest(t)
			if _, err := d.Decide(context.Background(), r); err == nil || called.Load() != 0 || r.ContextSnapshot.ModelCall.UsageLogStatus != "write_error" {
				t.Fatal("unsafe accounting path reached HTTP")
			}
			body, _ := os.ReadFile(original)
			if string(body) != "preserve-me\n" {
				t.Fatal("existing data was mutated")
			}
		})
	}
}

func TestResultLogFailurePreservesHTTPUsageAndLeavesUnfinishedStart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "usage.jsonl")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		if err := os.Chmod(path, 0o644); err != nil {
			t.Error(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": toolCallMessage("observe_scene", `{"source":"head"}`)}}, "usage": map[string]any{"prompt_tokens": 100, "completion_tokens": 20}})
	}))
	defer server.Close()
	d := newDecider(t, server)
	d.UsageFile = path
	r := cacheRequest(t)
	decision, err := d.Decide(context.Background(), r)
	call := r.ContextSnapshot.ModelCall
	if err == nil || decision.Tool != "" || call.HTTPStatus != 200 || call.Outcome != "accepted" || call.UsageLogStatus != "write_error" || call.Usage == nil || *call.Usage.PromptTokens != 100 {
		t.Fatalf("write failure hid real HTTP/usage or permitted execution: %+v %v", call, err)
	}
	if events := readUsage(t, path); len(events) != 1 || events[0].Event != "start" {
		t.Fatal("failed append should leave accounting explicitly unfinished")
	}
}

func TestConcurrentUsageAppendsStayWholeAndUniquelyIdentified(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": toolCallMessage("observe_scene", `{"source":"head"}`)}}})
	}))
	defer server.Close()
	path := filepath.Join(t.TempDir(), "usage.jsonl")
	const calls = 12
	var wg sync.WaitGroup
	errs := make(chan error, calls)
	for i := 0; i < calls; i++ {
		request := cacheRequest(t)
		wg.Add(1)
		go func(r actionloop.Request) {
			defer wg.Done()
			d := &actionloop.LLMDecider{BaseURL: server.URL, Model: "fixture", Client: server.Client(), UsageFile: path}
			_, err := d.Decide(context.Background(), r)
			if err != nil {
				errs <- err
			}
		}(request)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}
	pairs := map[string]string{}
	for _, event := range readUsage(t, path) {
		pairs[event.Call.CallID] += event.Event + ","
	}
	if len(pairs) != calls {
		t.Fatalf("call identities: %d != %d", len(pairs), calls)
	}
	for id, events := range pairs {
		if events != "start,result," {
			t.Error(fmt.Sprintf("%s: %s", id, events))
		}
	}
}
