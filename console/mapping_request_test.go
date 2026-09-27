package console_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type mappingGoalPlanner struct{ requests []string }

func (p *mappingGoalPlanner) PlanGoal(_ context.Context, request string) (orchestration.Bundle, bool, error) {
	p.requests = append(p.requests, request)
	// The adapter has no interpretation layer: errors and the complete request
	// come from the shared planner. Real strict grammar is covered in its tests.
	if strings.Contains(request, "不要") {
		return orchestration.Bundle{}, true, intent.ErrClarificationRequired
	}
	return orchestration.Bundle{Source: "deterministic", Capabilities: &capability.Plan{RobotID: "unit", CatalogRevision: "registered", Calls: []capability.Call{{Tool: "mapping.build", Arguments: map[string]any{"mode": "explore"}}}}}, true, nil
}
func mappingTaskRequest(t *testing.T, body string) (*httptest.ResponseRecorder, *tasks.Service, *mappingGoalPlanner) {
	t.Helper()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	planner := &mappingGoalPlanner{}
	service.SetGoalPlanner(planner)
	server := console.NewServer(service, nil, console.WithSessionToken(testSessionToken)).Handler()
	request := httptest.NewRequest(http.MethodPost, "http://localhost/v1/mapping/request", strings.NewReader(body))
	request.Header.Set(console.SessionHeaderName, testSessionToken)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	return response, service, planner
}
func TestLegacyMappingEntryCreatesUnapprovedUnifiedTask(t *testing.T) {
	response, service, planner := mappingTaskRequest(t, `{"request":"探索建图最多行驶12米最多1轮","adapter":"gazebo"}`)
	if response.Code != 201 {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	var task tasks.Task
	if err := json.NewDecoder(response.Body).Decode(&task); err != nil {
		t.Fatal(err)
	}
	stored, _ := service.Get(context.Background(), task.ID)
	if stored == nil || stored.Approved || stored.State != "READY" || stored.Plan.Capabilities == nil || len(planner.requests) != 1 || planner.requests[0] != task.Request {
		t.Fatalf("unexpected draft: %#v", stored)
	}
	if response.Header().Get("Deprecation") != "true" {
		t.Fatal("legacy entry lacks migration marker")
	}
}
func TestMappingCompatibilityDoesNotDropNegativeOrBudgetConstraints(t *testing.T) {
	response, service, _ := mappingTaskRequest(t, `{"request":"不要建图"}`)
	list, _ := service.List(context.Background())
	if response.Code != 422 || len(list) != 0 {
		t.Fatalf("negation executed: %d %#v", response.Code, list)
	}
	for _, body := range []string{`{"request":"建图","maxTravelM":12}`, `{"request":"建图","environment":"未知地点"}`, `{"request":"建图","extra":true}`, `{"request":"建图"} {}`} {
		response, service, _ := mappingTaskRequest(t, body)
		list, _ := service.List(context.Background())
		if response.Code != 400 || len(list) != 0 {
			t.Fatalf("constraints ignored: %d body=%s", response.Code, body)
		}
	}
}
func TestMappingCompatibilityRequiresTaskAuthority(t *testing.T) {
	server := console.NewServer(nil, nil, console.WithSessionToken(testSessionToken)).Handler()
	request := httptest.NewRequest(http.MethodPost, "http://localhost/v1/mapping/request", strings.NewReader(`{"request":"建图"}`))
	request.Header.Set(console.SessionHeaderName, testSessionToken)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != 503 {
		t.Fatalf("status=%d", response.Code)
	}
}
