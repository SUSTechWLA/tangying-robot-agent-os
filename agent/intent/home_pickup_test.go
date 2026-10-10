package intent_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/types/known/structpb"
)

func TestHomePickupComplementPreservesCompleteIntent(t *testing.T) {
	parser := intent.NewDeterministicParser()
	for _, verb := range []string{"拿", "取", "抓", "拾"} {
		for _, phrase := range []string{
			"从厨房出发，VERB杯子放进收纳盘，然后返回客厅",
			"从客厅出发，去厨房VERB红色杯子，放进右侧蓝色收纳盒，然后回到客厅",
			"从客厅去厨房，VERB杯子和蓝色杯子都放进右侧托盘内，然后回到客厅",
			"从客厅去厨房，VERB红色杯子放进托盘，再VERB绿色瓶子放进左侧收纳盒，然后回到客厅",
		} {
			request := strings.ReplaceAll(phrase, "VERB", verb+"起")
			t.Run(request, func(t *testing.T) {
				want, err := parser.Parse(strings.ReplaceAll(phrase, "VERB", verb))
				if err != nil {
					t.Fatalf("existing complete grammar failed: %v", err)
				}
				got, err := parser.Parse(request)
				if err != nil || !reflect.DeepEqual(got, want) {
					t.Fatalf("pickup complement changed route/object/container/constraints: got=%+v want=%+v err=%v", got, want, err)
				}
			})
		}
	}
	got, err := parser.Parse("从厨房出发，拿起杯子放进收纳盘，然后返回客厅")
	if err != nil || got.Action != manipulation.ActionHomeManipulation || got.Object.Category != "cup" ||
		got.Destination.Category != manipulation.CategoryStorageBin || got.ManipulationRouteIndex == nil ||
		*got.ManipulationRouteIndex != 0 || !reflect.DeepEqual(got.RouteRooms, []string{"kitchen", "living_room"}) ||
		len(got.Object.Attributes) != 0 || len(got.Destination.Attributes) != 0 || got.ReturnToStart ||
		!got.Constraints.KeepUpright || !got.Constraints.AvoidHumans {
		t.Fatalf("exact failed focused clause lost semantics: %+v %v", got, err)
	}
}

func TestHomePickupComplementDoesNotAuthorizePartialOrAmbiguousRequests(t *testing.T) {
	for _, request := range []string{
		"从厨房出发，拿起来杯子放进收纳盘，然后返回客厅",
		"从厨房出发，拿起起杯子放进收纳盘，然后返回客厅",
		"从厨房出发，把起杯子放进收纳盘，然后返回客厅",
		"从厨房出发，将起杯子放进收纳盘，然后返回客厅",
		"从厨房出发，拿起杯子和起瓶子放进收纳盘，然后返回客厅",
		"从厨房出发，拿起杯子，然后返回客厅",
		"从厨房出发，拿起杯子放进水槽，然后返回客厅",
		"从厨房出发，拿起洗洁精放进收纳盘，然后返回客厅",
		"从厨房出发，拿起透明杯子放进收纳盘，然后返回客厅",
		"从厨房出发，拿起杯子和洗洁精放进收纳盘，然后返回客厅",
		"从厨房出发，拿起杯子或瓶子放进收纳盘，然后返回客厅",
		"从厨房出发，拿起杯子放在收纳盘下面，然后返回客厅",
		"从厨房出发，拿起杯子放进收纳盘和水槽，然后返回客厅",
		"从厨房出发，拿起杯子，检查烤箱，放进收纳盘，然后返回客厅",
		"从厨房出发，拿起杯子放进收纳盘，然后打开烤箱，然后返回客厅",
		"从厨房出发，拿起杯子放进收纳盘并打开烤箱，然后返回客厅",
		"从厨房出发，拿起杯子，然后返回客厅，放进收纳盘",
		"从厨房出发，拿起杯子放进收纳盘，然后去书房",
		"从厨房出发，拿起杯子放进收纳盘，然后去卧室拿起瓶子放进盒子，最后返回客厅",
		"从厨房出发，不要拿起杯子放进收纳盘，然后返回客厅",
		"从厨房出发，如果杯子是空的再拿起杯子放进收纳盘，然后返回客厅",
	} {
		t.Run(request, func(t *testing.T) {
			got, err := intent.NewDeterministicParser().Parse(request)
			if !errors.Is(err, intent.ErrClarificationRequired) || got.Action != "" || len(got.Sequence) != 0 || len(got.RouteRooms) != 0 {
				t.Fatalf("unconsumed or ambiguous clause became executable: %+v %v", got, err)
			}
		})
	}
}

type pickupPlanningProvider struct{ calls atomic.Int32 }

func (*pickupPlanningProvider) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	schema, _ := structpb.NewStruct(map[string]any{"type": "object", "additionalProperties": false})
	contract, _ := structpb.NewStruct(map[string]any{"version": "1", "effects": []any{"READ"}, "planningFields": []any{"state"}})
	return &robotv1.ServiceCatalog{RobotId: "focused-robot", Services: []*robotv1.ServiceDefinition{{
		Name: "navigation.status", Available: true, InputSchema: schema, Contract: contract,
	}}}, nil
}

func (p *pickupPlanningProvider) CallService(context.Context, *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.calls.Add(1)
	return nil, errors.New("planning fixture must not execute a provider call")
}

func TestExactFocusedPickupGoalStillUsesRealModelEntryAndFreezesUnapprovedPlan(t *testing.T) {
	const request = "读取导航定位状态；从厨房出发，拿起杯子放进收纳盘，然后返回客厅；最后读取导航定位状态。抓取和放置分别验证，每个地点确认抵达。"
	const composite = "从厨房出发，拿起杯子放进收纳盘，然后返回客厅"
	var requests atomic.Int32
	bodies := make(chan []byte, 1)
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		if requests.Add(1) == 1 {
			bodies <- body
		}
		arguments, _ := json.Marshal(map[string]any{"calls": []any{
			map[string]any{"tool": "navigation.status", "arguments": map[string]any{}},
			map[string]any{"tool": "robot.task", "arguments": map[string]any{"request": composite}},
			map[string]any{"tool": "navigation.status", "arguments": map[string]any{}},
		}})
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{
			"tool_calls": []any{map[string]any{"function": map[string]any{"name": "propose_capability_plan", "arguments": string(arguments)}}},
		}}}})
	}))
	defer model.Close()
	provider := &pickupPlanningProvider{}
	parser := intent.NewDeterministicParser()
	planner := &capabilityagent.Planner{Provider: provider, Decider: &actionloop.LLMDecider{BaseURL: model.URL, Model: "pickup-goal-fixture"},
		ParseLegacy: func(subrequest string) (json.RawMessage, error) {
			parsed, err := parser.Parse(subrequest)
			if err != nil {
				return nil, err
			}
			return json.Marshal(parsed)
		}}
	service := tasks.NewService(tasks.NewMemoryStore(), parser)
	service.SetGoalPlanner(planner)
	task, err := service.Create(context.Background(), request, "auto")
	if err != nil {
		t.Fatal(err)
	}
	if requests.Load() != 1 || provider.calls.Load() != 0 || task.Approved || task.Plan.Source != orchestration.SourceLLM ||
		task.Plan.Capabilities == nil || len(task.Plan.Capabilities.Calls) != 3 {
		t.Fatalf("GOAL model/approval boundary changed: requests=%d providerCalls=%d task=%+v", requests.Load(), provider.calls.Load(), task)
	}
	calls := task.Plan.Capabilities.Calls
	var legacy manipulation.Intent
	if calls[0].Tool != "navigation.status" || calls[1].Tool != "robot.task" || calls[2].Tool != "navigation.status" ||
		calls[1].Arguments["request"] != composite || json.Unmarshal(calls[1].LegacyIntent, &legacy) != nil ||
		legacy.Action != manipulation.ActionHomeManipulation || legacy.Object.Category != "cup" ||
		legacy.Destination.Category != manipulation.CategoryStorageBin || legacy.ManipulationRouteIndex == nil ||
		*legacy.ManipulationRouteIndex != 0 || !reflect.DeepEqual(legacy.RouteRooms, []string{"kitchen", "living_room"}) {
		t.Fatalf("frozen focused plan lost scope or became navigation-only: %+v %+v", calls, legacy)
	}
	trace := task.Plan.Capabilities.PlanningTrace
	if len(trace) != 1 || trace[0].Context == nil || trace[0].Verdict != "FROZEN_FOR_APPROVAL" ||
		trace[0].Context.ModelRequestJSON != string(<-bodies) || !strings.Contains(trace[0].Context.Text, request) {
		t.Fatal("exact full goal or actual model input was not retained")
	}
}
