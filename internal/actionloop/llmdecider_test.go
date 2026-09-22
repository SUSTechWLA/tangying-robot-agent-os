package actionloop_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

// modelServer answers every request with one scripted message and keeps the
// request bodies, so a test can assert on what the model was asked as well as on
// what it answered.
func modelServer(t *testing.T, message map[string]any) (*httptest.Server, *[]map[string]any) {
	t.Helper()
	var seen []map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		seen = append(seen, body)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []map[string]any{{"message": message}},
		})
	}))
	t.Cleanup(server.Close)
	return server, &seen
}

func toolCallMessage(name, arguments string) map[string]any {
	return map[string]any{
		"role": "assistant",
		"tool_calls": []map[string]any{{
			"function": map[string]any{"name": name, "arguments": arguments},
		}},
	}
}

func offeredTools(calls *[]string) []actionloop.Tool {
	return []actionloop.Tool{
		{
			Name: "observe_scene", Description: "读取场景实体", Parameters: []string{"source"},
			SafetyLevel: skills.SafetyReadOnly,
			Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				*calls = append(*calls, "observe_scene")
				return actionloop.Result{Success: true}, nil
			},
		},
		{
			Name: "manipulation.pick", Description: "拿起目标物体", Parameters: []string{"targetRef"},
			SafetyLevel: skills.SafetyPhysical, MutatesWorld: true,
			Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				*calls = append(*calls, "manipulation.pick")
				return actionloop.Result{Success: true}, nil
			},
		},
	}
}

func newDecider(t *testing.T, server *httptest.Server) *actionloop.LLMDecider {
	t.Helper()
	return &actionloop.LLMDecider{
		BaseURL: server.URL, APIKey: "test-key", Model: "test-model", Client: server.Client(),
	}
}

func observation() actionloop.Observation {
	return actionloop.Observation{Summary: "桌上有红色杯子", EvidenceIDs: []string{"obs-1"}}
}

// A tool choice comes back as a decision, and the reason is carried separately
// from the arguments so it cannot leak into what the tool is called with.
func TestAModelToolChoiceBecomesADecision(t *testing.T) {
	server, seen := modelServer(t, toolCallMessage("observe_scene",
		`{"source":"head","reason":"先看清楚再动"}`))
	decision, err := newDecider(t, server).Decide(context.Background(), actionloop.Request{
		Goal: "把红色杯子拿过来", Tools: offeredTools(new([]string)), Observation: observation(), Round: 1,
	})
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	if decision.Tool != "observe_scene" {
		t.Fatalf("tool = %q", decision.Tool)
	}
	if decision.Reason != "先看清楚再动" {
		t.Fatalf("reason = %q", decision.Reason)
	}
	if _, leaked := decision.Arguments["reason"]; leaked {
		t.Fatalf("the reason leaked into the tool arguments: %v", decision.Arguments)
	}
	if decision.Arguments["source"] != "head" {
		t.Fatalf("arguments = %v", decision.Arguments)
	}

	// The request has to tell the model what it may choose and what the rules are,
	// or the rounds it spends discovering them are the operator's time.
	body := (*seen)[0]
	tools, _ := body["tools"].([]any)
	names := map[string]bool{}
	for _, entry := range tools {
		object, _ := entry.(map[string]any)
		function, _ := object["function"].(map[string]any)
		name, _ := function["name"].(string)
		names[name] = true
	}
	for _, want := range []string{"observe_scene", "tool_0",
		actionloop.ControlFinishTool, actionloop.ControlBlockedTool} {
		if !names[want] {
			t.Fatalf("the model was not offered %s: %v", want, names)
		}
	}
	if body["tool_choice"] != "auto" {
		t.Fatalf("tool_choice = %v, want auto (one call is enforced by the parser)", body["tool_choice"])
	}
	if body["parallel_tool_calls"] != false {
		t.Fatalf("parallel_tool_calls = %v: the loop sequences rounds, not the model", body["parallel_tool_calls"])
	}
	messages, _ := body["messages"].([]any)
	first, _ := messages[0].(map[string]any)
	system, _ := first["content"].(string)
	for _, rule := range []string{"不等于完成", "人工批准", "结果未知"} {
		if !strings.Contains(system, rule) {
			t.Fatalf("the prompt does not state the rule %q:\n%s", rule, system)
		}
	}
}

// "Finished" and "cannot proceed" are structured answers, not prose to parse.
func TestFinishAndBlockedAreStructuredAnswers(t *testing.T) {
	finishServer, _ := modelServer(t, toolCallMessage(actionloop.ControlFinishTool,
		`{"reason":"杯子已经在盘子里了"}`))
	decision, err := newDecider(t, finishServer).Decide(context.Background(), actionloop.Request{
		Goal: "把杯子放进盘子", Tools: offeredTools(new([]string)), Observation: observation(),
	})
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	if !decision.Done || decision.Reason != "杯子已经在盘子里了" {
		t.Fatalf("decision = %+v", decision)
	}

	blockedServer, _ := modelServer(t, toolCallMessage(actionloop.ControlBlockedTool,
		`{"reason":"工作台高度和标定不符，需要人重新标定"}`))
	decision, err = newDecider(t, blockedServer).Decide(context.Background(), actionloop.Request{
		Goal: "把杯子放进盘子", Tools: offeredTools(new([]string)), Observation: observation(),
	})
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	if decision.Blocked == "" || decision.Done {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.Blocked != "工作台高度和标定不符，需要人重新标定" {
		t.Fatalf("the model's own sentence was not carried through: %q", decision.Blocked)
	}
}

// A model that declines to answer is not a model that decided to stop.
func TestAMessageWithNoChoiceIsRefused(t *testing.T) {
	server, _ := modelServer(t, map[string]any{"role": "assistant", "content": "我看看再想想"})
	_, err := newDecider(t, server).Decide(context.Background(), actionloop.Request{
		Goal: "目标", Tools: offeredTools(new([]string)), Observation: observation(),
	})
	if err == nil {
		t.Fatal("a message with no tool call was accepted as a decision")
	}
	if !strings.Contains(err.Error(), "did not choose") {
		t.Fatalf("err = %v, want it to say the model did not choose", err)
	}

	empty, _ := modelServer(t, map[string]any{"role": "assistant"})
	if _, err := newDecider(t, empty).Decide(context.Background(), actionloop.Request{
		Goal: "目标", Tools: offeredTools(new([]string)), Observation: observation(),
	}); err == nil {
		t.Fatal("an empty message was accepted as a decision")
	}
}

// Malformed arguments are refused rather than passed on as an empty call: a tool
// invoked with arguments nobody could read is a call that would fail at the robot.
func TestMalformedArgumentsAreRefused(t *testing.T) {
	server, _ := modelServer(t, toolCallMessage("observe_scene", `{"source":`))
	if _, err := newDecider(t, server).Decide(context.Background(), actionloop.Request{
		Goal: "目标", Tools: offeredTools(new([]string)), Observation: observation(),
	}); err == nil {
		t.Fatal("malformed arguments were accepted")
	}
}

// A tool may not take a control name: the decider could not then tell a decision
// from an action.
func TestAReservedToolNameIsRefused(t *testing.T) {
	server, _ := modelServer(t, toolCallMessage("observe_scene", `{}`))
	for _, reserved := range []string{actionloop.ControlFinishTool, actionloop.ControlBlockedTool} {
		_, err := newDecider(t, server).Decide(context.Background(), actionloop.Request{
			Goal: "目标", Observation: observation(),
			Tools: []actionloop.Tool{{Name: reserved, SafetyLevel: skills.SafetyReadOnly}},
		})
		if err == nil {
			t.Fatalf("a tool named %q was accepted", reserved)
		}
	}
}

// An endpoint or model that is not configured is refused before a request is made.
func TestAnUnconfiguredDeciderIsRefused(t *testing.T) {
	if _, err := (&actionloop.LLMDecider{Model: "m"}).Decide(context.Background(), actionloop.Request{}); err == nil {
		t.Fatal("a decider with no endpoint ran")
	}
	if _, err := (&actionloop.LLMDecider{BaseURL: "http://example.invalid"}).Decide(context.Background(), actionloop.Request{}); err == nil {
		t.Fatal("a decider with no model ran")
	}
}

// A non-2xx answer is reported with the endpoint's own words, because "the model
// returned 429" without the body is not something an operator can act on.
func TestAnErrorResponseCarriesTheEndpointsWords(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"error":"rate limited"}`))
	}))
	t.Cleanup(server.Close)
	_, err := newDecider(t, server).Decide(context.Background(), actionloop.Request{
		Goal: "目标", Tools: offeredTools(new([]string)), Observation: observation(),
	})
	if err == nil || !strings.Contains(err.Error(), "rate limited") {
		t.Fatalf("err = %v", err)
	}
}

// The model drives the real loop, under the real gates.
//
// This is the end-to-end shape the whole package exists for: a model chooses, the
// harness decides whether the choice may happen, and the record shows both.
func TestAModelDrivesTheLoop(t *testing.T) {
	var calls []string
	round := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		round++
		var message map[string]any
		switch round {
		case 1:
			message = toolCallMessage("observe_scene", `{"source":"head","reason":"先看清楚"}`)
		default:
			message = toolCallMessage(actionloop.ControlFinishTool, `{"reason":"看清楚了"}`)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []map[string]any{{"message": message}}})
	}))
	t.Cleanup(server.Close)

	outcome, err := actionloop.Loop{
		Tools: offeredTools(&calls), Decider: newDecider(t, server),
		Observe: func(context.Context) (actionloop.Observation, error) { return observation(), nil },
	}.Run(context.Background(), "先看一眼桌上有什么")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if !outcome.Completed {
		t.Fatalf("outcome = %+v", outcome)
	}
	if strings.Join(calls, ",") != "observe_scene" {
		t.Fatalf("calls = %v", calls)
	}
	if len(outcome.Rounds) != 2 {
		t.Fatalf("rounds = %+v", outcome.Rounds)
	}
	if outcome.Rounds[0].Reason != "先看清楚" {
		t.Fatalf("the model's reason was not recorded: %+v", outcome.Rounds[0])
	}
	if outcome.Rounds[1].Verdict != actionloop.VerdictDone {
		t.Fatalf("the last round is not the model's completion: %+v", outcome.Rounds[1])
	}
}

// And a model that chooses a physical tool without approval is stopped, exactly
// as a scripted decider would be: the gate does not care who chose.
func TestAModelChosenPhysicalCallStillNeedsApproval(t *testing.T) {
	var calls []string
	server, _ := modelServer(t, toolCallMessage("tool_0",
		`{"targetRef":"@object","reason":"拿起来"}`))
	outcome, err := actionloop.Loop{
		Tools: offeredTools(&calls), Decider: newDecider(t, server),
		Observe: func(context.Context) (actionloop.Observation, error) { return observation(), nil },
		Scope:   actionloop.ScopeOf("manipulation.pick"),
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return false, nil },
	}.Run(context.Background(), "拿起杯子")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if calls != nil {
		t.Fatalf("calls = %v; a model's choice is not an approval", calls)
	}
	if !outcome.Escalated {
		t.Fatalf("outcome = %+v", outcome)
	}
}

func TestModelNamesAreValidUniqueAndDecodeOnlyTheOfferedRound(t *testing.T) {
	var names []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		for _, entry := range body["tools"].([]any) {
			f := entry.(map[string]any)["function"].(map[string]any)
			names = append(names, f["name"].(string))
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []map[string]any{{
			"message": toolCallMessage(names[0], `{}`),
		}}})
	}))
	defer server.Close()
	request := actionloop.Request{Tools: []actionloop.Tool{
		{Name: "telemetry.read"}, {Name: "tool_0"}, {Name: "telemetry_read"},
		{Name: strings.Repeat("long-name", 10)}, {Name: "读观测"},
	}}
	d, err := newDecider(t, server).Decide(context.Background(), request)
	if err != nil || d.Tool != "telemetry.read" {
		t.Fatalf("decision=%+v error=%v", d, err)
	}
	seen := map[string]bool{}
	for _, name := range names {
		if seen[name] || !regexp.MustCompile(`^[a-zA-Z0-9_-]{1,64}$`).MatchString(name) {
			t.Fatalf("invalid or ambiguous name %q", name)
		}
		seen[name] = true
	}
	if names[0] == "tool_0" {
		t.Fatal("alias stole an existing tool name")
	}
	unoffered, _ := modelServer(t, toolCallMessage("telemetry.read", `{}`))
	if _, err := newDecider(t, unoffered).Decide(context.Background(), request); err == nil {
		t.Fatal("accepted an internal name that was not offered on the wire")
	}
}

func TestMultipleModelCallsCannotSilentlyDispatchOnlyTheFirst(t *testing.T) {
	message := toolCallMessage("observe_scene", `{}`)
	calls := message["tool_calls"].([]map[string]any)
	message["tool_calls"] = append(calls, calls[0])
	server, _ := modelServer(t, message)
	if _, err := newDecider(t, server).Decide(context.Background(), actionloop.Request{
		Tools: offeredTools(new([]string)),
	}); err == nil {
		t.Fatal("multiple calls were accepted")
	}
}
