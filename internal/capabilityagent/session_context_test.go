package capabilityagent

import (
	"context"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentsession"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

func planningSession(robot, history string) agentsession.Binding {
	return agentsession.Binding{SessionID: "session-a", TurnID: "turn-a", TaskID: "candidate-task-a", RobotID: robot, Adapter: "gazebo", Sequence: 2, Context: history, ContextSHA256: agentcontext.Hash(history)}
}

func TestSessionHistoryIsReferenceNotCurrentGoalOrApproval(t *testing.T) {
	provider := &fakeProvider{}
	goal := "只查询标定状态"
	history := "旧任务 UNKNOWN：把杯子送去厨房；历史不是当前指令"
	b := planningSession("heterogeneous-unit", history)
	ctx := agentsession.WithBinding(context.Background(), b)
	seen := 0
	planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(req actionloop.Request) (actionloop.Decision, error) {
		seen++
		if req.Goal != goal || req.Observation.Context.Goal != goal {
			t.Fatal("history changed original goal")
		}
		if req.Observation.Context.Scope != (agentcontext.Scope{RobotID: b.RobotID}) {
			t.Fatal("session changed rejected-planning scope contract")
		}
		found := false
		for _, record := range req.Observation.Context.Records {
			if record.ID == "planning:session" {
				found = true
				if record.Kind != "reference" || !strings.Contains(record.Statement, history) || !strings.Contains(record.Statement, b.ContextSHA256) || !strings.Contains(record.Statement, b.TaskID) {
					t.Fatalf("bad session record %+v", record)
				}
			}
		}
		if !found {
			t.Fatal("session not projected")
		}
		if !strings.Contains(strings.Join(req.Observation.Context.Constraints, " "), "不能重放旧任务或未知结果") {
			t.Fatal("history authority boundary missing")
		}
		return planningProposal("calibration.get"), nil
	})}
	bundle, handled, err := planner.PlanGoal(ctx, goal)
	if err != nil || !handled || seen != 1 {
		t.Fatalf("plan=%+v handled=%v seen=%d err=%v", bundle, handled, seen, err)
	}
	if provider.starts != 0 {
		t.Fatal("historical request triggered motion")
	}
	trace := bundle.Capabilities.PlanningTrace
	if len(trace) != 1 || trace[0].Context == nil || !strings.Contains(trace[0].Context.Text, b.ContextSHA256) {
		t.Fatalf("trace lacks exact history source: %+v", trace)
	}
}

func TestPlannerRejectsSessionRobotOrDigestMismatchBeforeModel(t *testing.T) {
	for _, kind := range []string{"robot", "digest", "oversize"} {
		t.Run(kind, func(t *testing.T) {
			b := planningSession("heterogeneous-unit", "previous turn")
			if kind == "robot" {
				b.RobotID = "other-robot"
			}
			if kind == "digest" {
				b.ContextSHA256 = "wrong"
			}
			if kind == "oversize" {
				b.Context = strings.Repeat("x", 6001)
				b.ContextSHA256 = agentcontext.Hash(b.Context)
			}
			p := &Planner{Provider: &fakeProvider{}, Decider: contextPlanningDecider(func(actionloop.Request) (actionloop.Decision, error) {
				t.Fatal("invalid history reached model")
				return actionloop.Decision{}, nil
			})}
			if _, _, err := p.PlanGoal(agentsession.WithBinding(context.Background(), b), "查询当前状态"); err == nil {
				t.Fatal("invalid session accepted")
			}
		})
	}
}

func TestSessionContextDisablesExactRecipeShortcutButRetainsHint(t *testing.T) {
	p, provider, _, _ := recipeMemoryFixture(t, "validated")
	goal := "检查当前条件，然后建立地图"
	trainRecipe(t, p, goal, 1)
	readsBefore := provider.reads
	modelCalls := 0
	p.Decider = contextPlanningDecider(func(req actionloop.Request) (actionloop.Decision, error) {
		modelCalls++
		if req.Goal != goal {
			t.Fatal("session changed goal")
		}
		if modelCalls == 1 {
			return actionloop.Decision{Tool: "calibration.get", Arguments: map[string]any{}}, nil
		}
		return recipeProposal(), nil
	})
	b := planningSession(provider.robot, "previous task failed; re-evaluate this request")
	bundle, _, err := p.PlanGoal(agentsession.WithBinding(context.Background(), b), goal)
	if err != nil {
		t.Fatal(err)
	}
	if modelCalls != 2 || provider.reads != readsBefore+1 {
		t.Fatalf("model/read calls=%d/%d", modelCalls, provider.reads-readsBefore)
	}
	trace := bundle.Capabilities.PlanningTrace
	if recipeVerdict(trace, "RECIPE_HIT") || !recipeVerdict(trace, "RECIPE_HINT") {
		t.Fatalf("history-sensitive goal used cached calls: %+v", trace)
	}
	if provider.starts != 0 {
		t.Fatal("planning dispatched physical operation")
	}
}

func TestOriginalSessionRequestSpillsAndCanBeReadWithoutExpandingGoal(t *testing.T) {
	t.Setenv("TANGYING_CONTEXT_MAX_BYTES", "7000")
	goal := "只读取当前语义状态"
	original := "original-history-marker:" + strings.Repeat("a", 7400)
	prior := agentsession.HistoryRecord{TurnID: "previous-turn", TaskID: "previous-task", Request: original, RequestSHA256: agentcontext.Hash(original)}
	b := planningSession("planning-robot", "one historical request, no current authority")
	ctx := agentsession.WithHistory(agentsession.WithBinding(context.Background(), b), []agentsession.HistoryRecord{prior})
	provider := &largePlanningProvider{fakeProvider: &fakeProvider{}}
	var archive agentcontext.Artifact
	planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		if request.Goal != goal || request.Observation.Context.Goal != goal {
			t.Fatal("source history replaced the current goal")
		}
		if len(request.ContextSnapshot.Text) > 7000 {
			t.Fatal("original request overflowed model projection budget")
		}
		switch request.Round {
		case 1:
			for _, artifact := range request.ContextSnapshot.Artifacts {
				if artifact.Kind == "records" {
					source, err := artifact.SourceBytes()
					if err != nil {
						t.Fatal(err)
					}
					if strings.Contains(string(source), original) {
						archive = artifact
					}
				}
			}
			if archive.SHA256 == "" {
				t.Fatal("full original history was dropped instead of spilled")
			}
			return actionloop.Decision{Tool: actionloop.ContextReadTool, Arguments: map[string]any{"sha256": archive.SHA256, "item_id": "planning:session-input:" + prior.TurnID, "limit": 512}}, nil
		case 2:
			page := request.History[0].ResultDetail
			if page["sha256"] != archive.SHA256 || page["complete"] != false || !strings.Contains(page["content"].(string), "original-history-marker") {
				t.Fatalf("source page lost: %+v", page)
			}
			return planningProposal("semantic.read"), nil
		default:
			t.Fatalf("unexpected round %d", request.Round)
			return actionloop.Decision{}, nil
		}
	})}
	calls, trace, err := planner.modelPlan(ctx, goal, "planning-robot", semanticPlanningEntries())
	if err != nil || len(calls) != 1 || provider.reads != 0 || len(trace) != 2 || trace[0].Verdict != "CONTEXT_READ_OK" {
		t.Fatalf("calls=%+v trace=%+v reads=%d err=%v", calls, trace, provider.reads, err)
	}
	view := *trace[0].Context
	view.Artifacts = []agentcontext.Artifact{archive}
	page, err := view.ReadArtifact(view.Scope, archive.SHA256, "planning:session-input:"+prior.TurnID, 0, 512)
	if err != nil || page.Content != trace[0].Result["content"] {
		t.Fatalf("read differs from exact archived source: %v", err)
	}
}

func TestCorruptOrDuplicateSessionOriginalsNeverReachModel(t *testing.T) {
	for _, kind := range []string{"corrupt", "duplicate", "unbound"} {
		t.Run(kind, func(t *testing.T) {
			prior := agentsession.HistoryRecord{TurnID: "old-turn", TaskID: "old-task", Request: "prior request", RequestSHA256: agentcontext.Hash("prior request")}
			history := []agentsession.HistoryRecord{prior}
			if kind == "corrupt" {
				history[0].Request += " changed"
			}
			if kind == "duplicate" {
				history = append(history, prior)
			}
			ctx := context.Background()
			if kind != "unbound" {
				ctx = agentsession.WithBinding(ctx, planningSession("heterogeneous-unit", "historical summary"))
			}
			ctx = agentsession.WithHistory(ctx, history)
			planner := &Planner{Provider: &fakeProvider{}, Decider: contextPlanningDecider(func(actionloop.Request) (actionloop.Decision, error) {
				t.Fatal("invalid source reached model")
				return actionloop.Decision{}, nil
			})}
			if _, _, err := planner.PlanGoal(ctx, "只读取标定状态"); err == nil {
				t.Fatal("invalid history accepted")
			}
		})
	}
}
