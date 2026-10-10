package capabilityagent

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func writePlanningDecision(w http.ResponseWriter, tool string, arguments map[string]any) {
	encoded, _ := json.Marshal(arguments)
	_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{
		"tool_calls": []any{map[string]any{"function": map[string]any{"name": tool, "arguments": string(encoded)}}},
	}}}})
}

func planningProposal(tool string) actionloop.Decision {
	return actionloop.Decision{Tool: "propose_capability_plan", Arguments: map[string]any{"calls": []any{
		map[string]any{"tool": tool, "arguments": map[string]any{}},
	}}}
}

func TestGoalModelInputAndRejectedRoundSurviveTaskAndRevisionReopen(t *testing.T) {
	ctx := context.Background()
	var bodies []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		bodies = append(bodies, string(body))
		if len(bodies) == 1 {
			_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"no tool selected"}}]}`))
			return
		}
		decision := planningProposal("calibration.get")
		writePlanningDecision(w, decision.Tool, decision.Arguments)
	}))
	defer server.Close()
	provider := &fakeProvider{}
	planner := &Planner{Provider: provider, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "goal-context-test"}}
	path := filepath.Join(t.TempDir(), "planning.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	service.SetGoalPlanner(planner)
	task, err := service.Create(ctx, "只读取整机标定状态", "auto")
	if err != nil {
		_ = store.Close()
		t.Fatal(err)
	}
	if task.Approved || provider.starts != 0 || len(bodies) != 2 {
		t.Fatalf("planning performed or approved motion: task=%+v requests=%d", task, len(bodies))
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	loaded, err := store.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	revision, err := store.Revision(ctx, task.ID, 1)
	if err != nil {
		t.Fatal(err)
	}
	for name, trace := range map[string][]capability.PlanningStep{
		"task":     loaded.Plan.Capabilities.PlanningTrace,
		"revision": revision.Revision.Plan.Capabilities.PlanningTrace,
	} {
		if len(trace) != 2 || trace[0].Verdict != "MODEL_DECISION_REJECTED" || trace[1].Verdict != "FROZEN_FOR_APPROVAL" {
			t.Fatalf("%s trace=%+v", name, trace)
		}
		for i, step := range trace {
			p := step.Context
			if p == nil || p.Scope != (agentcontext.Scope{RobotID: "heterogeneous-unit"}) || p.Stage != "planning" || agentcontext.Hash(p.Text) != p.SHA256 {
				t.Fatalf("%s round %d lost exact planning scope: %+v", name, i+1, p)
			}
			body, err := p.ModelRequestBytes()
			if err != nil || string(body) != bodies[i] || p.ModelRequestJSON != bodies[i] {
				t.Fatalf("%s round %d input differs from HTTP body: %v", name, i+1, err)
			}
			var request struct {
				Messages []struct{ Role, Content string }
			}
			if err := json.Unmarshal(body, &request); err != nil {
				t.Fatal(err)
			}
			found := false
			for _, message := range request.Messages {
				if message.Role == "user" && message.Content == p.Text {
					found = true
				}
			}
			if !found {
				t.Fatalf("%s round %d retained context was not sent to model", name, i+1)
			}
			if strings.Contains(p.Text, "model_request_json") {
				t.Fatal("prior model requests recursively entered the next input")
			}
		}
	}
}

func TestGoalModelExhaustionRetainsNonExecutableRejectedInputs(t *testing.T) {
	var bodies []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		bodies = append(bodies, string(body))
		http.Error(w, "model temporarily unavailable", http.StatusServiceUnavailable)
	}))
	defer server.Close()
	planner := &Planner{Provider: &fakeProvider{}, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "goal-context-test"}}
	bundle, handled, err := planner.PlanGoal(context.Background(), "只读取整机标定状态")
	if err == nil || !handled || bundle.Capabilities == nil || len(bundle.Capabilities.Calls) != 0 || len(bodies) != 3 {
		t.Fatalf("failed plan became executable or lost trace: bundle=%+v err=%v", bundle, err)
	}
	trace := bundle.Capabilities.PlanningTrace
	if len(trace) != 3 {
		t.Fatalf("trace length=%d", len(trace))
	}
	for i, step := range trace {
		if step.Context == nil || step.Verdict != "MODEL_DECISION_REJECTED" || step.Context.ModelRequestJSON != bodies[i] {
			t.Fatalf("error round %d lost actual input", i+1)
		}
	}
}

type contextPlanningDecider func(actionloop.Request) (actionloop.Decision, error)

func (d contextPlanningDecider) Decide(_ context.Context, request actionloop.Request) (actionloop.Decision, error) {
	return d(request)
}

type largePlanningProvider struct {
	*fakeProvider
	reads int
}

func (p *largePlanningProvider) CallService(_ context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if request.Name != "semantic.read" || request.RobotId != "planning-robot" {
		return nil, errors.New("unexpected provider call")
	}
	p.reads++
	return &robotv1.ServiceResponse{Ok: true, Result: fields(map[string]any{"report": map[string]any{
		"calibration": strings.Repeat("a", 2000), "navigation": strings.Repeat("b", 2000), "mapping": strings.Repeat("c", 2000),
	}})}, nil
}

func semanticPlanningEntries() map[string]capability.Manifest {
	return map[string]capability.Manifest{"semantic.read": {
		Name:        "semantic.read",
		InputSchema: map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false},
		Contract:    capability.Contract{PlanningFields: []string{"report"}},
	}}
}

func TestGoalPlanningArchivePagesKeepTheirOriginalScopeAndSHA(t *testing.T) {
	t.Setenv("TANGYING_CONTEXT_MAX_BYTES", "7000")
	provider := &largePlanningProvider{fakeProvider: &fakeProvider{}}
	var first agentcontext.Artifact
	var secondOffset float64
	planner := &Planner{Provider: provider}
	planner.Decider = contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		if request.ContextSnapshot == nil || request.ContextSnapshot.Scope != (agentcontext.Scope{RobotID: "planning-robot"}) {
			t.Fatal("missing or invented planning identity")
		}
		archiveOffered := false
		for _, tool := range request.Tools {
			if tool.Name == actionloop.ContextReadTool {
				archiveOffered = true
				if tool.MutatesWorld {
					t.Fatal("archive read became a robot action")
				}
			}
		}
		switch request.Round {
		case 1, 3:
			if request.Round == 1 && archiveOffered {
				t.Fatal("archive reader offered without source")
			}
			if request.Round == 3 {
				page := request.History[1].ResultDetail
				secondOffset, _ = page["next_offset"].(float64)
				if page["complete"] != false || secondOffset <= 0 {
					t.Fatalf("first page missing pagination: %#v", page)
				}
			}
			return actionloop.Decision{Tool: "semantic.read", Arguments: map[string]any{}}, nil
		case 2, 4:
			if !archiveOffered {
				t.Fatal("compacted history cannot be read")
			}
			if request.Round == 2 {
				for _, artifact := range request.ContextSnapshot.Artifacts {
					if artifact.Kind == "attempts" {
						first = artifact
					}
				}
				if first.SHA256 == "" {
					t.Fatal("large semantic read was not archived")
				}
			} else {
				for _, artifact := range request.ContextSnapshot.Artifacts {
					if artifact.SHA256 == first.SHA256 {
						t.Fatal("fixture did not replace current archive SHA")
					}
				}
			}
			return actionloop.Decision{Tool: actionloop.ContextReadTool, Arguments: map[string]any{
				"sha256": first.SHA256, "item_id": "round:1", "offset": secondOffset, "limit": 256,
			}}, nil
		case 5:
			return planningProposal("semantic.read"), nil
		default:
			t.Fatalf("unexpected round %d", request.Round)
			return actionloop.Decision{}, nil
		}
	})
	calls, trace, err := planner.modelPlan(context.Background(), "读取语义状态并制定只读计划", "planning-robot", semanticPlanningEntries())
	if err != nil || len(calls) != 1 || calls[0].Tool != "semantic.read" || provider.reads != 2 || len(trace) != 5 {
		t.Fatalf("calls=%+v trace=%+v provider reads=%d err=%v", calls, trace, provider.reads, err)
	}
	for _, index := range []int{1, 3} {
		step := trace[index]
		if step.Verdict != "CONTEXT_READ_OK" || step.Result["sha256"] != first.SHA256 {
			t.Fatalf("archive read failed or used new source: %+v", step)
		}
		offset, _ := step.Arguments["offset"].(float64)
		view := *step.Context
		view.Artifacts = []agentcontext.Artifact{first}
		page, err := view.ReadArtifact(view.Scope, first.SHA256, "round:1", int(offset), 256)
		if err != nil || page.Content != step.Result["content"] {
			t.Fatalf("page differs from persisted original source: %v", err)
		}
	}
}

func TestGoalPlanningArchiveCannotBecomeAnApprovedProviderCall(t *testing.T) {
	provider := &largePlanningProvider{fakeProvider: &fakeProvider{}}
	planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(actionloop.Request) (actionloop.Decision, error) {
		return planningProposal(actionloop.ContextReadTool), nil
	})}
	calls, trace, err := planner.modelPlan(context.Background(), "只读计划", "planning-robot", semanticPlanningEntries())
	if err == nil || len(calls) != 0 || len(trace) != 3 || provider.reads != 0 {
		t.Fatalf("archive was approved or called provider: calls=%+v trace=%+v err=%v", calls, trace, err)
	}
}

func TestGoalPlanningKeepsReadAndRoundLimitsAndFailsClosedOnBudget(t *testing.T) {
	for _, tc := range []struct {
		name, tool            string
		budget                string
		wantRounds, wantReads int
	}{
		{"six provider reads", "semantic.read", "32768", 10, 6},
		{"ten total decisions", actionloop.ContextReadTool, "32768", 10, 0},
		{"essential context cannot fit", "semantic.read", "500", 0, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("TANGYING_CONTEXT_MAX_BYTES", tc.budget)
			provider := &largePlanningProvider{fakeProvider: &fakeProvider{}}
			rounds := 0
			planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(actionloop.Request) (actionloop.Decision, error) {
				rounds++
				return actionloop.Decision{Tool: tc.tool, Arguments: map[string]any{}}, nil
			})}
			calls, trace, err := planner.modelPlan(context.Background(), "只读计划", "planning-robot", semanticPlanningEntries())
			if err == nil || len(calls) != 0 || rounds != tc.wantRounds || provider.reads != tc.wantReads {
				t.Fatalf("rounds=%d reads=%d calls=%+v err=%v", rounds, provider.reads, calls, err)
			}
			if rounds == 0 && (len(trace) != 1 || trace[0].Verdict != "CONTEXT_REJECTED" || trace[0].Context != nil) {
				t.Fatalf("budget failure fabricated a model input: %+v", trace)
			}
		})
	}
}
