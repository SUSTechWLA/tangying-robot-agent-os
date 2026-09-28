package capabilityagent

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

type surveySchemaProvider struct{ *fakeProvider }

func (p *surveySchemaProvider) ListServices(ctx context.Context) (*robotv1.ServiceCatalog, error) {
	catalog, err := p.fakeProvider.ListServices(ctx)
	if err != nil {
		return nil, err
	}
	for _, service := range catalog.Services {
		if service.Name == "mapping.build" {
			service.InputSchema = fields(map[string]any{"type": "object", "oneOf": []any{
				map[string]any{"type": "object", "properties": map[string]any{"mode": map[string]any{"type": "string", "enum": []any{"survey"}}}, "required": []any{"mode"}, "additionalProperties": false},
				map[string]any{"type": "object", "properties": map[string]any{"mode": map[string]any{"type": "string", "enum": []any{"explore"}}, "maxLegs": map[string]any{"type": "integer", "minimum": 1, "maximum": 6}}, "additionalProperties": false},
			}})
		}
	}
	return catalog, nil
}

func TestGoalModelReceivesCompleteSchemasAndCannotClaimPhysicalCompletion(t *testing.T) {
	saw := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		wire, _ := json.Marshal(body["tools"])
		for _, field := range []string{"calibration.get", "mapping.build", "enum", "explore", "operation", "verification"} {
			if !strings.Contains(string(wire), field) {
				t.Errorf("model catalog lost %s", field)
			}
		}
		saw = true
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"id":"plan","type":"function","function":{"name":"propose_capability_plan","arguments":"{\"calls\":[{\"tool\":\"calibration.get\",\"arguments\":{}}]}"}}]}}]}`))
	}))
	defer server.Close()
	planner := &Planner{Provider: &fakeProvider{}, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "configured-goal-model"}}
	bundle, handled, err := planner.PlanGoal(context.Background(), "请帮我检查一下传感器标定")
	if err != nil || !handled || !saw || bundle.Source != orchestration.SourceLLM || bundle.Capabilities.Calls[0].Tool != "calibration.get" {
		t.Fatalf("plan=%#v handled=%v err=%v", bundle, handled, err)
	}
}

func TestGoalModelRepairsInvalidSurveyArgumentsBeforeCreatingTask(t *testing.T) {
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempts++
		arguments := `{\"calls\":[{\"tool\":\"mapping.build\",\"arguments\":{\"mode\":\"survey\",\"maxLegs\":2}}]}`
		if attempts == 2 {
			arguments = `{\"calls\":[{\"tool\":\"mapping.build\",\"arguments\":{\"mode\":\"survey\"}}]}`
		}
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"id":"plan","type":"function","function":{"name":"propose_capability_plan","arguments":"` + arguments + `"}}]}}]}`))
	}))
	defer server.Close()
	planner := &Planner{Provider: &surveySchemaProvider{fakeProvider: &fakeProvider{}}, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "goal"}}
	bundle, handled, err := planner.PlanGoal(context.Background(), "请按家庭路线巡检并重新建立地图")
	if err != nil || !handled || attempts != 2 || len(bundle.Capabilities.Calls) != 1 || bundle.Capabilities.Calls[0].Arguments["maxLegs"] != nil {
		t.Fatalf("attempts=%d plan=%#v handled=%v err=%v", attempts, bundle, handled, err)
	}
}
func TestOfflineGoalLimitsAreNotDiscarded(t *testing.T) {
	calls, ok := literalCalls("运行标定，探索建图最多行驶12米最多1轮，然后去厨房")
	if !ok || len(calls) != 3 || calls[1].Arguments["maxTravelM"] != float64(12) || calls[1].Arguments["maxLegs"] != 1 {
		t.Fatalf("calls=%#v handled=%v", calls, ok)
	}
	if _, ok := literalCalls("建图，但不要移动机器人"); ok {
		t.Fatal("negative constraint discarded")
	}
}

func TestOfflineSurveyBudgetRequiresClarification(t *testing.T) {
	planner := &Planner{Provider: &surveySchemaProvider{fakeProvider: &fakeProvider{}}}
	_, handled, err := planner.PlanGoal(context.Background(), "巡检建图最多行驶12米最多1轮")
	if !handled || !errors.Is(err, intent.ErrClarificationRequired) {
		t.Fatalf("handled=%v err=%v", handled, err)
	}
}

func TestCompositeRouteMustRetainExplicitDestination(t *testing.T) {
	planner := &Planner{Provider: &fakeProvider{}, ParseLegacy: func(string) (json.RawMessage, error) {
		return json.Marshal(manipulation.Intent{Action: manipulation.ActionHomeRoute, RouteRooms: []string{"走廊"}})
	}}
	_, handled, err := planner.PlanGoal(context.Background(), "检查地图，然后前往客厅")
	if !handled || !errors.Is(err, intent.ErrClarificationRequired) {
		t.Fatalf("handled=%v err=%v", handled, err)
	}
}

func TestConfiguredGoalModelOwnsEvenExactRequest(t *testing.T) {
	called := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"id":"plan","type":"function","function":{"name":"propose_capability_plan","arguments":"{\"calls\":[{\"tool\":\"calibration.get\",\"arguments\":{}},{\"tool\":\"mapping.build\",\"arguments\":{\"mode\":\"explore\"}}]}"}}]}}]}`))
	}))
	defer server.Close()
	planner := &Planner{Provider: &fakeProvider{}, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "goal"}}
	bundle, handled, err := planner.PlanGoal(context.Background(), "检查标定，建图")
	if err != nil || !handled || !called || bundle.Source != orchestration.SourceLLM || bundle.Capabilities.Calls[1].Tool != "mapping.build" {
		t.Fatalf("%+v %v", bundle, err)
	}
}

func TestModelMultiroomRouteRemainsOneVerifiedCompositeTask(t *testing.T) {
	request := "先去走廊，再去客厅，并分别确认抵达。"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		arguments, _ := json.Marshal(map[string]any{"calls": []any{map[string]any{"tool": "robot.task", "arguments": map[string]any{"request": request}}}})
		encoded, _ := json.Marshal(string(arguments))
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"id":"plan","type":"function","function":{"name":"propose_capability_plan","arguments":` + string(encoded) + `}}]}}]}`))
	}))
	defer server.Close()
	planner := &Planner{Provider: &fakeProvider{}, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "goal"},
		ParseLegacy: func(request string) (json.RawMessage, error) {
			parsed, err := intent.NewDeterministicParser().Parse(request)
			if err != nil {
				return nil, err
			}
			return json.Marshal(parsed)
		}}
	bundle, handled, err := planner.PlanGoal(context.Background(), request)
	if err != nil || !handled || bundle.Source != orchestration.SourceLLM || len(bundle.Capabilities.Calls) != 1 {
		t.Fatalf("bundle = %+v, handled = %v, err = %v", bundle, handled, err)
	}
	call := bundle.Capabilities.Calls[0]
	var planned manipulation.Intent
	if call.Tool != "robot.task" || call.Arguments["request"] != request || json.Unmarshal(call.LegacyIntent, &planned) != nil ||
		len(planned.RouteRooms) != 2 || planned.RouteRooms[0] != "home_corridor" || planned.RouteRooms[1] != "living_room" {
		t.Fatalf("route call = %+v, intent = %+v", call, planned)
	}
	if _, ok := literalCalls("先去走廊，再去客厅，并分别确认抵达，然后浇花。"); ok {
		t.Fatal("unrecognized physical action became an exact route")
	}
}

type planningProvider struct{ *fakeProvider }

func (p planningProvider) ListServices(ctx context.Context) (*robotv1.ServiceCatalog, error) {
	catalog, err := p.fakeProvider.ListServices(ctx)
	for _, service := range catalog.Services {
		if service.Name == "calibration.get" {
			service.Contract = fields(map[string]any{"version": "1", "effects": []any{"READ"},
				"planningFields": []any{"revision"}})
		}
	}
	return catalog, err
}
func (p planningProvider) CallService(ctx context.Context, req *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if req.Name == "calibration.get" {
		return &robotv1.ServiceResponse{Ok: true, Result: fields(map[string]any{
			"revision": "cal-1", "imageBase64": "secret-image", "imuSamples": []any{1, 2, 3}})}, nil
	}
	return p.fakeProvider.CallService(ctx, req)
}

type planningDecider struct {
	round       int
	sawFiltered bool
}

func (d *planningDecider) Decide(_ context.Context, req actionloop.Request) (actionloop.Decision, error) {
	d.round++
	if d.round == 1 {
		for _, tool := range req.Tools {
			if tool.Name == "calibration.get" {
				return actionloop.Decision{Tool: "calibration.get", Arguments: map[string]any{}}, nil
			}
		}
		return actionloop.Decision{}, errors.New("semantic read not offered")
	}
	if len(req.History) != 1 || req.History[0].ResultDetail["revision"] != "cal-1" || req.History[0].ResultDetail["imageBase64"] != nil || req.History[0].ResultDetail["imuSamples"] != nil {
		return actionloop.Decision{}, errors.New("raw data escaped semantic projection")
	}
	d.sawFiltered = true
	return actionloop.Decision{Tool: "propose_capability_plan", Arguments: map[string]any{"calls": []any{
		map[string]any{"tool": "calibration.get", "arguments": map[string]any{}},
	}}}, nil
}

func TestGoalReadsOnlyProviderDeclaredSemanticProjectionBeforeApproval(t *testing.T) {
	d := &planningDecider{}
	p := &Planner{Provider: planningProvider{fakeProvider: &fakeProvider{}}, Decider: d}
	bundle, handled, err := p.PlanGoal(context.Background(), "检查标定")
	if err != nil || !handled || !d.sawFiltered || len(bundle.Capabilities.PlanningTrace) != 2 ||
		bundle.Capabilities.PlanningTrace[0].Result["imageBase64"] != nil {
		t.Fatalf("bundle=%+v handled=%v err=%v", bundle, handled, err)
	}
}

func TestPlanningViewRejectsRawSensorFieldsEvenWhenProviderDeclaresThem(t *testing.T) {
	for _, value := range []map[string]any{
		{"imageBase64": "encoded"},
		{"nested": map[string]any{"imuSamples": []any{1, 2, 3}}},
		{"cells": []any{1, 2, 3}},
		{"points": []any{[]any{1, 2, 3}}},
	} {
		if err := validatePlanningView(value, 0); err == nil {
			t.Fatalf("raw planning data accepted: %#v", value)
		}
	}
	if err := validatePlanningView(map[string]any{"mapPose": []any{1, 2, 0, 1, 0, 0, 0}, "localizationState": "localized"}, 0); err != nil {
		t.Fatal(err)
	}
}
