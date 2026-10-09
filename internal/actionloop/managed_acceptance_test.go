package actionloop_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

type managedAcceptanceDecider func(actionloop.Request) (actionloop.Decision, error)

func (f managedAcceptanceDecider) Decide(_ context.Context, request actionloop.Request) (actionloop.Decision, error) {
	return f(request)
}

func managedAcceptanceLoopDocument() agentcontext.Document {
	return agentcontext.Document{
		SchemaVersion: agentcontext.Version, Role: "recovery", Goal: "取回完整工具来源",
		Scope:       agentcontext.Scope{TaskID: "long-loop", RobotID: "robot-1", PlanRevision: 7},
		Constraints: []string{"未知物理结果不得重做"}, Questions: []string{"早期命令是否已经对账？"},
		Records: []agentcontext.Record{{ID: "initial-guard", Kind: "guard", Statement: "原始禁止事项必须保留"}},
	}
}

func managedAcceptanceLoopJSON(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func managedAcceptanceLoopBudget(t *testing.T, contextBytes, requestBytes int) {
	t.Helper()
	t.Setenv("TANGYING_AGENT_CONTEXT", "legacy")
	t.Setenv("TANGYING_CONTEXT_MAX_BYTES", fmt.Sprint(contextBytes))
	t.Setenv("TANGYING_MODEL_REQUEST_MAX_BYTES", fmt.Sprint(requestBytes))
	t.Setenv("TANGYING_MODEL_OUTPUT_TOKENS", "256")
}

func TestManagedAcceptanceDefaultLoopContextPreservesGuardsWithoutOptIn(t *testing.T) {
	managedAcceptanceLoopBudget(t, 16384, 32768)
	d := managedAcceptanceLoopDocument()
	before := managedAcceptanceLoopJSON(t, d)
	p, err := actionloop.SnapshotFor(actionloop.Request{Goal: d.Goal, Round: 1, Observation: actionloop.Observation{Context: &d, Summary: "待对账"}})
	if err != nil {
		t.Fatal(err)
	}
	if p.Compaction == nil || p.Format != "json" || !json.Valid([]byte(p.Text)) || !strings.Contains(p.Text, "initial-guard") || !strings.Contains(p.Text, d.Questions[0]) {
		t.Fatal("default context omits structured safety state")
	}
	if !bytes.Equal(before, managedAcceptanceLoopJSON(t, d)) {
		t.Fatal("snapshot modified observer-owned document")
	}
}

func TestManagedAcceptanceContextAndHTTPRequestLimitsPreventModelAndMotion(t *testing.T) {
	for _, mode := range []string{"protected-context", "tool-schema", "invalid-json"} {
		t.Run(mode, func(t *testing.T) {
			managedAcceptanceLoopBudget(t, 4096, 8192)
			var serverCalls, physicalCalls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				serverCalls.Add(1)
				_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"function":{"name":"move","arguments":"{}"}}]}}]}`))
			}))
			defer server.Close()
			d := managedAcceptanceLoopDocument()
			tool := actionloop.Tool{Name: "move", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true,
				Call: func(context.Context, map[string]any) (actionloop.Result, error) {
					physicalCalls.Add(1)
					return actionloop.Result{Success: true}, nil
				}}
			switch mode {
			case "protected-context":
				d.Constraints = append(d.Constraints, strings.Repeat("不可压掉的安全约束", 2000))
			case "tool-schema":
				// Schema is outside Document.Tools, so only the full HTTP envelope
				// check catches this request even when managed context fits.
				tool.InputSchema = map[string]any{"type": "object", "description": strings.Repeat("schema", 3000)}
			case "invalid-json":
				d.Attempts = []agentcontext.Attempt{{ID: "corrupt", Arguments: map[string]any{"offset": math.NaN()}}}
			}
			loop := actionloop.Loop{Tools: []actionloop.Tool{tool},
				Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "offline-fixture", Client: server.Client()},
				Scope:   actionloop.ScopeOf("move"), Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
				Observe: func(context.Context) (actionloop.Observation, error) {
					return actionloop.Observation{Context: &d, Summary: "awaiting evidence"}, nil
				},
				MaxRounds: 2, MaxUnproductive: 1,
			}
			outcome, err := loop.Run(context.Background(), d.Goal)
			if err == nil && !outcome.Escalated {
				t.Fatal("invalid context/request did not stop the loop")
			}
			if serverCalls.Load() != 0 || physicalCalls.Load() != 0 || outcome.Calls != 0 || outcome.Completed {
				t.Fatalf("over-limit request reached an execution boundary: HTTP=%d physical=%d outcome=%+v", serverCalls.Load(), physicalCalls.Load(), outcome)
			}
		})
	}
}

func TestManagedAcceptanceRealLoopPreservesLargeToolJSONAndReadsEveryPage(t *testing.T) {
	// Use the production budget for a full multi-page read: every completed
	// read retains its immutable status and arguments. The 8 KiB test below
	// separately verifies one page is immediately usable under a small budget.
	managedAcceptanceLoopBudget(t, 32768, 65536)
	d := managedAcceptanceLoopDocument()
	payload := map[string]any{
		"sequence": uint64(9007199254740993),
		"rows":     []any{map[string]any{"label": "同一个杯子", "history": strings.Repeat("中文抓取/🍵", 3000)}},
	}
	original := managedAcceptanceLoopJSON(t, payload)
	var readCalls, physicalCalls, pages int
	var firstSHA string
	var offset int
	var reconstructed bytes.Buffer
	tools := []actionloop.Tool{
		{Name: "large_read", SafetyLevel: skills.SafetyReadOnly, Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			readCalls++
			return actionloop.Result{Success: true, Message: "完整 JSON 来源", Detail: payload}, nil
		}},
		{Name: "move", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true, Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			physicalCalls++
			return actionloop.Result{Success: true}, nil
		}},
	}
	decider := managedAcceptanceDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		if request.ContextSnapshot == nil || len(request.ContextSnapshot.Text) > 32768 {
			t.Fatal("loop did not provide a bounded managed context")
		}
		if request.Round == 1 {
			return actionloop.Decision{Tool: "large_read", Arguments: map[string]any{"sequence": uint64(9007199254740993)}}, nil
		}
		if !bytes.Equal(managedAcceptanceLoopJSON(t, request.History[0].ResultDetail), original) {
			t.Fatal("large original result was truncated or lost integer precision")
		}
		if request.Round > 2 {
			last := request.History[len(request.History)-1]
			if last.Tool != actionloop.ContextReadTool || last.Verdict != actionloop.VerdictSatisfied {
				t.Fatalf("archive read did not succeed: %+v", last)
			}
			var page agentcontext.ArtifactPage
			if err := json.Unmarshal(managedAcceptanceLoopJSON(t, last.ResultDetail), &page); err != nil {
				t.Fatal(err)
			}
			if page.Offset != offset || page.NextOffset != offset+len(page.Content) || page.SHA256 != firstSHA || page.ItemID != "round:1" {
				t.Fatalf("page binding changed: %+v", page)
			}
			reconstructed.WriteString(page.Content)
			offset = page.NextOffset
			pages++
			if page.Complete {
				var attempt agentcontext.Attempt
				if err := json.Unmarshal(reconstructed.Bytes(), &attempt); err != nil {
					t.Fatal(err)
				}
				_, actualJSON, ok := strings.Cut(attempt.Detail, "工具数据=")
				if !ok || !bytes.Equal([]byte(actualJSON), original) || attempt.Verdict != actionloop.VerdictSatisfied {
					t.Fatal("reconstructed original tool JSON/verdict does not match source")
				}
				return actionloop.Decision{Done: true, Reason: "完整只读来源已取回"}, nil
			}
		}
		if firstSHA == "" {
			for _, archive := range request.ContextSnapshot.Artifacts {
				if archive.Kind == "attempts" {
					firstSHA = archive.SHA256
					break
				}
			}
			if firstSHA == "" {
				t.Fatal("large original result lacks a retrievable archive")
			}
		}
		offered := false
		for _, tool := range request.Tools {
			if tool.Name == actionloop.ContextReadTool {
				offered = tool.SafetyLevel == skills.SafetyReadOnly && !tool.MutatesWorld
			}
		}
		if !offered {
			t.Fatal("archive retrieval was not offered as a read-only tool")
		}
		// Keep the first SHA while later rounds add further archives. Paging
		// must finish against the same immutable source, never a changing array.
		return actionloop.Decision{Tool: actionloop.ContextReadTool, Arguments: map[string]any{"sha256": firstSHA, "item_id": "round:1", "offset": offset, "limit": 3071}}, nil
	})
	loop := actionloop.Loop{Tools: tools, Decider: decider, MaxRounds: 30,
		Observe: func(context.Context) (actionloop.Observation, error) {
			return actionloop.Observation{Context: &d, Summary: "只读审阅来源"}, nil
		},
	}
	outcome, err := loop.Run(context.Background(), d.Goal)
	if err != nil || !outcome.Completed || readCalls != 1 || physicalCalls != 0 || pages < 3 || outcome.Calls != 1 {
		t.Fatalf("retrieval loop incomplete: err=%v completed=%v read=%d physical=%d pages=%d calls=%d reason=%s", err, outcome.Completed, readCalls, physicalCalls, pages, outcome.Calls, outcome.Reason)
	}
	// The durable replay itself must retain the original payload after JSON I/O.
	var replay actionloop.Outcome
	decoder := json.NewDecoder(bytes.NewReader(managedAcceptanceLoopJSON(t, outcome)))
	decoder.UseNumber()
	if err := decoder.Decode(&replay); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(managedAcceptanceLoopJSON(t, replay.Rounds[0].ResultDetail), original) {
		t.Fatal("persisted replay cannot restore original JSON exactly")
	}
}

func TestManagedAcceptanceUnrenderableToolResultStopsBeforeLaterPhysicalCall(t *testing.T) {
	managedAcceptanceLoopBudget(t, 16384, 32768)
	var physicalCalls int
	loop := actionloop.Loop{
		Tools: []actionloop.Tool{
			{Name: "corrupt_read", SafetyLevel: skills.SafetyReadOnly, Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				return actionloop.Result{Success: true, Detail: map[string]any{"bad": make(chan int)}}, nil
			}},
			{Name: "move", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true, Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				physicalCalls++
				return actionloop.Result{Success: true}, nil
			}},
		},
		Scope: actionloop.ScopeOf("move"), Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
		Decider: managedAcceptanceDecider(func(r actionloop.Request) (actionloop.Decision, error) {
			if r.Round == 1 {
				return actionloop.Decision{Tool: "corrupt_read"}, nil
			}
			return actionloop.Decision{Tool: "move"}, nil
		}),
		Observe: func(context.Context) (actionloop.Observation, error) {
			return actionloop.Observation{Summary: "ready"}, nil
		},
	}
	outcome, err := loop.Run(context.Background(), "读取后才可动作")
	if err != nil || !outcome.Escalated || outcome.Completed || physicalCalls != 0 || len(outcome.Rounds) != 1 || outcome.Rounds[0].Code != "UNRENDERABLE_TOOL_RESULT" {
		t.Fatalf("unpersistable result permitted later motion: err=%v outcome=%+v physical=%d", err, outcome, physicalCalls)
	}
}

func TestManagedAcceptanceScopeChangeStopsBeforeAnotherDecisionOrTool(t *testing.T) {
	for _, field := range []string{"task", "robot", "revision"} {
		t.Run(field, func(t *testing.T) {
			managedAcceptanceLoopBudget(t, 16384, 32768)
			d := managedAcceptanceLoopDocument()
			d.Records = append(d.Records, agentcontext.Record{ID: "old-scope-source", Kind: "tool_return", Scope: d.Scope, Statement: strings.Repeat("仅属于原始范围", 4000)})
			originalScope := d.Scope
			var observations, decisions, readCalls, physicalCalls int
			loop := actionloop.Loop{
				Tools: []actionloop.Tool{
					{Name: "read", SafetyLevel: skills.SafetyReadOnly, Call: func(context.Context, map[string]any) (actionloop.Result, error) {
						readCalls++
						return actionloop.Result{Success: true}, nil
					}},
					{Name: "move", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true, Call: func(context.Context, map[string]any) (actionloop.Result, error) {
						physicalCalls++
						return actionloop.Result{Success: true}, nil
					}},
				},
				Decider: managedAcceptanceDecider(func(r actionloop.Request) (actionloop.Decision, error) {
					decisions++
					if r.Round == 1 {
						if len(r.ContextSnapshot.Artifacts) == 0 {
							t.Fatal("fixture has no old-scope archive")
						}
						return actionloop.Decision{Tool: "read"}, nil
					}
					return actionloop.Decision{Tool: "move"}, nil
				}),
				Scope: actionloop.ScopeOf("move"), Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
				Observe: func(context.Context) (actionloop.Observation, error) {
					observations++
					if observations == 2 {
						switch field {
						case "task":
							d.Scope.TaskID = "other-task"
						case "robot":
							d.Scope.RobotID = "other-robot"
						case "revision":
							d.Scope.PlanRevision++
						}
					}
					return actionloop.Observation{Context: &d, Summary: "读取持久状态"}, nil
				},
			}
			outcome, err := loop.Run(context.Background(), d.Goal)
			if err == nil || !outcome.Escalated || outcome.Completed || decisions != 1 || readCalls != 1 || physicalCalls != 0 || len(outcome.Rounds) != 1 {
				t.Fatalf("scope change did not stop before execution: err=%v escalated=%v decisions=%d read=%d physical=%d rounds=%d", err, outcome.Escalated, decisions, readCalls, physicalCalls, len(outcome.Rounds))
			}
			for _, archive := range outcome.Rounds[0].Context.Artifacts {
				if archive.Scope != originalScope {
					t.Fatal("old archive was rebound to new scope")
				}
			}
		})
	}
}

func TestManagedAcceptanceTraceStoresExactHTTPRequestWithoutCredentials(t *testing.T) {
	managedAcceptanceLoopBudget(t, 16384, 32768)
	const fixtureKey = "offline-test-key-never-in-trace"
	requests := make(chan []byte, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		requests <- body
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"function":{"name":"finish","arguments":"{\"reason\":\"只读验收完成\"}"}}]}}]}`))
	}))
	defer server.Close()
	d := managedAcceptanceLoopDocument()
	loop := actionloop.Loop{Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "offline-fixture", APIKey: fixtureKey, Client: server.Client()},
		Observe: func(context.Context) (actionloop.Observation, error) {
			return actionloop.Observation{Context: &d, Summary: "已读取历史"}, nil
		},
	}
	outcome, err := loop.Run(context.Background(), d.Goal)
	if err != nil || !outcome.Completed || len(outcome.Rounds) != 1 || outcome.Rounds[0].Context == nil {
		t.Fatalf("HTTP fixture failed: err=%v outcome=%+v", err, outcome)
	}
	var actual []byte
	select {
	case actual = <-requests:
	default:
		t.Fatal("model server did not receive a request")
	}
	snapshot := outcome.Rounds[0].Context
	if !bytes.Equal(snapshot.ModelRequest, actual) || snapshot.ModelRequestSHA256 != agentcontext.Hash(string(actual)) {
		t.Fatal("durable request bytes/hash differ from actual HTTP model input")
	}
	if bytes.Contains(managedAcceptanceLoopJSON(t, outcome), []byte(fixtureKey)) {
		t.Fatal("API credentials leaked into persisted trace")
	}
	var request struct {
		MaxTokens int `json:"max_tokens"`
		Messages  []struct {
			Role    string `json:"role"`
			Content string `json:"content"`
		} `json:"messages"`
	}
	if err := json.Unmarshal(actual, &request); err != nil {
		t.Fatal(err)
	}
	if request.MaxTokens != 256 || len(request.Messages) != 2 || request.Messages[1].Role != "user" || request.Messages[1].Content != snapshot.Text {
		t.Fatal("actual request lacks reserved output bound or exact managed context")
	}
}

func TestManagedAcceptanceArchivedFailureKeepsExactUnknownOutcomeInView(t *testing.T) {
	managedAcceptanceLoopBudget(t, 8192, 16384)
	d := managedAcceptanceLoopDocument()
	var calls int
	loop := actionloop.Loop{
		Tools: []actionloop.Tool{{Name: "pick", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true,
			Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				calls++
				return actionloop.Result{Code: "UNVERIFIED_WORLD_MUTATION", Message: strings.Repeat("无法确认杯子是否已经移动；", 3000)}, nil
			}}},
		Decider: managedAcceptanceDecider(func(actionloop.Request) (actionloop.Decision, error) { return actionloop.Decision{Tool: "pick"}, nil }),
		Scope:   actionloop.ScopeOf("pick"), Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
		Observe: func(context.Context) (actionloop.Observation, error) {
			return actionloop.Observation{Context: &d, Summary: "原始抓取结果待确认"}, nil
		},
	}
	outcome, err := loop.Run(context.Background(), d.Goal)
	if err != nil || !outcome.Escalated || outcome.Completed || calls != 1 || len(outcome.Rounds) != 1 {
		t.Fatalf("unknown physical outcome did not stop: err=%v calls=%d outcome=%+v", err, calls, outcome)
	}
	failed := outcome.Rounds[0]
	if failed.Verdict != actionloop.VerdictUnsatisfied || failed.Code != "UNVERIFIED_WORLD_MUTATION" || failed.Class != "UNKNOWN_OUTCOME" || len(failed.Detail) < 8192 {
		t.Fatal("fixture did not produce a large actual unknown-outcome failure")
	}
	// Recovery can inspect the stopped run; this does not resume its physical
	// loop or treat a model's reading of history as execution authority.
	snapshot, err := actionloop.SnapshotFor(actionloop.Request{Goal: d.Goal, Round: 2, History: outcome.Rounds,
		Observation: actionloop.Observation{Context: &d, Summary: "仅审查已停止任务的原始错误"}})
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Compaction.ArchivedAttempts != 1 || len(snapshot.Text) > 8192 || strings.Contains(snapshot.Text, failed.Detail) {
		t.Fatal("large failure detail was not actually externalized within the budget")
	}
	var view agentcontext.Document
	if err := json.Unmarshal([]byte(snapshot.Text), &view); err != nil {
		t.Fatal(err)
	}
	if len(view.Attempts) != 1 || view.Attempts[0].Verdict != failed.Verdict {
		t.Fatal("archiving changed the original failure verdict")
	}
	for _, record := range view.Records {
		if record.ID != "actionloop:attempt-state:1" {
			continue
		}
		var state struct{ Verdict, Code, Class string }
		if err := json.Unmarshal([]byte(record.Statement[strings.Index(record.Statement, "{"):]), &state); err != nil {
			t.Fatal(err)
		}
		if record.Kind != "guard" || state.Verdict != failed.Verdict || state.Code != failed.Code || state.Class != failed.Class {
			t.Fatalf("protected failure state changed: kind=%s state=%+v", record.Kind, state)
		}
		return
	}
	t.Fatal("archived failure lost its non-archivable exact-state guard")
}

func TestManagedAcceptanceEightKiBBudgetKeepsRetrievedPageVisibleNextRound(t *testing.T) {
	for _, mode := range []string{"default-limit", "requested-4096", "invalid-sha"} {
		t.Run(mode, func(t *testing.T) {
			managedAcceptanceLoopBudget(t, 8192, 16384)
			d := managedAcceptanceLoopDocument()
			d.Records = append(d.Records, agentcontext.Record{ID: "source-to-read", Kind: "tool_return", Scope: d.Scope,
				Statement: strings.Repeat("待阅读的原始中文证据🍵。", 2000)})
			var decisions int
			loop := actionloop.Loop{
				Decider: managedAcceptanceDecider(func(request actionloop.Request) (actionloop.Decision, error) {
					decisions++
					if request.Round == 1 {
						var sha string
						for _, a := range request.ContextSnapshot.Artifacts {
							if a.Kind == "records" {
								sha = a.SHA256
							}
						}
						if sha == "" {
							t.Fatal("source was not archived")
						}
						args := map[string]any{"sha256": sha, "item_id": "source-to-read"}
						if mode == "requested-4096" {
							args["limit"] = 4096
						} else if mode == "invalid-sha" {
							args["sha256"] = "not-in-this-context"
						}
						return actionloop.Decision{Tool: actionloop.ContextReadTool, Arguments: args}, nil
					}
					if len(request.History) != 1 || request.History[0].Verdict != actionloop.VerdictSatisfied {
						t.Fatal("read did not produce a successful prior round")
					}
					var actual agentcontext.ArtifactPage
					if err := json.Unmarshal(managedAcceptanceLoopJSON(t, request.History[0].ResultDetail), &actual); err != nil {
						t.Fatal(err)
					}
					if len(actual.Content) == 0 || len(actual.Content) > 1024 || actual.Complete {
						t.Fatalf("8 KiB context requires a bounded nonempty page: contentBytes=%d complete=%v", len(actual.Content), actual.Complete)
					}
					var view agentcontext.Document
					if err := json.Unmarshal([]byte(request.ContextSnapshot.Text), &view); err != nil {
						t.Fatal(err)
					}
					if len(request.ContextSnapshot.Text) > 8192 || len(view.Attempts) != 1 {
						t.Fatal("next context is oversized or lacks the retrieval attempt")
					}
					_, pageJSON, ok := strings.Cut(view.Attempts[0].Detail, "工具数据=")
					if !ok {
						t.Fatal("freshly retrieved page was immediately archived again")
					}
					var visible agentcontext.ArtifactPage
					if err := json.Unmarshal([]byte(pageJSON), &visible); err != nil {
						t.Fatal(err)
					}
					if visible != actual {
						t.Fatal("page visible to next decision differs from retrieval result")
					}
					return actionloop.Decision{Done: true, Reason: "本页已经在决策上下文可读"}, nil
				}),
				Observe: func(context.Context) (actionloop.Observation, error) {
					return actionloop.Observation{Context: &d, Summary: "只读查看来源"}, nil
				},
				MaxRounds: 2,
			}
			outcome, err := loop.Run(context.Background(), d.Goal)
			if err != nil || outcome.Calls != 0 {
				t.Fatalf("archive-only loop failed: err=%v calls=%d", err, outcome.Calls)
			}
			if mode == "invalid-sha" {
				if !outcome.Escalated || outcome.Completed || decisions != 1 || len(outcome.Rounds) != 1 || outcome.Rounds[0].Code != "INVALID_ARGUMENT" || outcome.Rounds[0].Class != "VALIDATION" {
					t.Fatalf("invalid read was misclassified as physical uncertainty: %+v", outcome)
				}
			} else if !outcome.Completed || decisions != 2 {
				t.Fatalf("page did not reach next decision: completed=%v decisions=%d reason=%s", outcome.Completed, decisions, outcome.Reason)
			}
		})
	}
}

func TestManagedAcceptanceHTTPModelArgumentsRejectUnsafeIntegersWithoutBreakingStringsOrDecimals(t *testing.T) {
	for _, tc := range []struct {
		name, arguments, key string
		want                 any
		reject               bool
	}{
		{name: "unsafe-integer", arguments: `{"id":9007199254740993}`, reject: true},
		{name: "exact-string-id", arguments: `{"id":"9007199254740993"}`, key: "id", want: "9007199254740993"},
		{name: "ordinary-decimal", arguments: `{"offset":0.017}`, key: "offset", want: 0.017},
	} {
		t.Run(tc.name, func(t *testing.T) {
			managedAcceptanceLoopBudget(t, 16384, 32768)
			var requests atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				requests.Add(1)
				// Arguments stay raw JSON inside the actual provider tool-call
				// string; no test-side float conversion can hide input corruption.
				response := map[string]any{"choices": []any{map[string]any{"message": map[string]any{"tool_calls": []any{
					map[string]any{"function": map[string]any{"name": "move", "arguments": tc.arguments}},
				}}}}}
				if err := json.NewEncoder(w).Encode(response); err != nil {
					t.Error(err)
				}
			}))
			defer server.Close()
			var physicalCalls int
			var actualArguments map[string]any
			tool := actionloop.Tool{Name: "move", SafetyLevel: skills.SafetyPhysical, MutatesWorld: true,
				InputSchema: map[string]any{"type": "object", "properties": map[string]any{
					"id": map[string]any{"type": []any{"string", "integer"}}, "offset": map[string]any{"type": "number"},
				}},
				Call: func(_ context.Context, args map[string]any) (actionloop.Result, error) {
					physicalCalls++
					actualArguments = args
					// This is an offline invocation boundary, not a robot. Stop
					// without claiming any physical evidence or task success.
					return actionloop.Result{Code: "INVALID_ARGUMENT", Message: "offline fixture stop"}, nil
				},
			}
			decider := &actionloop.LLMDecider{BaseURL: server.URL, Model: "offline-fixture", Client: server.Client()}
			decision, err := decider.Decide(context.Background(), actionloop.Request{Goal: "验证工具参数入口", Round: 1, Tools: []actionloop.Tool{tool}, Observation: actionloop.Observation{Summary: "只做离线验收"}})
			if tc.reject {
				if err == nil || decision.Tool != "" || physicalCalls != 0 {
					t.Fatal("unsafe numeric model argument was not rejected before dispatch")
				}
			} else if err != nil || decision.Tool != "move" || decision.Arguments[tc.key] != tc.want {
				t.Fatalf("portable model argument changed at response entry: err=%v decision=%+v", err, decision)
			}
			loop := actionloop.Loop{Tools: []actionloop.Tool{tool}, Decider: decider, MaxRounds: 1, MaxUnproductive: 1,
				Scope: actionloop.ScopeOf("move"), Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
				Observe: func(context.Context) (actionloop.Observation, error) {
					return actionloop.Observation{Summary: "只做离线验收"}, nil
				},
			}
			outcome, err := loop.Run(context.Background(), "验证工具参数入口")
			if err != nil || requests.Load() != 2 || !outcome.Escalated || outcome.Completed {
				t.Fatalf("real HTTP decider/loop fixture failed: err=%v requests=%d outcome=%+v", err, requests.Load(), outcome)
			}
			if tc.reject {
				if physicalCalls != 0 || outcome.Calls != 0 || outcome.Rounds[0].Verdict != actionloop.VerdictRefused {
					t.Fatal("unsafe integer reached physical invocation")
				}
			} else if physicalCalls != 1 || outcome.Calls != 1 || actualArguments[tc.key] != tc.want {
				t.Fatalf("portable argument not passed exactly to tool: calls=%d arguments=%+v", physicalCalls, actualArguments)
			}
		})
	}
}
