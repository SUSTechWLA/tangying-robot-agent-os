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

func TestExactGoalAvoidsModelInferenceWithoutDiscardingBudgets(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("exact request invoked model") }))
	defer server.Close()
	planner := &Planner{Provider: &fakeProvider{}, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "goal"}}
	bundle, handled, err := planner.PlanGoal(context.Background(), "检查标定，建图")
	if err != nil || !handled || bundle.Source != orchestration.SourceDeterministic || bundle.Capabilities.Calls[1].Tool != "mapping.build" {
		t.Fatalf("%+v %v", bundle, err)
	}
}
