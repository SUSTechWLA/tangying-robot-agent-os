package fleet_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/agentharness"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type draftThenFinish struct{ calls int }

func (d *draftThenFinish) Decide(_ context.Context, request actionloop.Request) (actionloop.Decision, error) {
	d.calls++
	if request.Role != "system" {
		panic("system harness did not identify its role")
	}
	if d.calls == 1 {
		seen := map[string]bool{}
		for _, tool := range request.Tools {
			seen[tool.Name] = true
		}
		if !seen["fleet.metrics.read"] || seen["robot.command"] || seen["fleet.task.approve"] {
			panic("system harness offered an incomplete or unsafe tool catalog")
		}
		return actionloop.Decision{Tool: "fleet.task_draft.create", Arguments: map[string]any{
			"request": "让1号机器人把红色杯子放进右侧收纳盒", "adapter": "mujoco",
		}}, nil
	}
	if len(request.History) != 1 || request.History[0].ResultDetail["taskId"] == "" {
		panic("model cannot see the draft tool result")
	}
	if request.ContextSnapshot == nil || !strings.Contains(request.ContextSnapshot.Text, "taskId") {
		panic("system decision context omitted the tool result")
	}
	return actionloop.Decision{Done: true, Reason: "draft ready for operator review"}, nil
}

func TestSystemHarnessCreatesOnlyAnUnapprovedDraft(t *testing.T) {
	t.Setenv("TANGYING_AGENT_CONTEXT", "json")
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	model := modelroute.Endpoint{Provider: "deterministic"}
	profile, err := agentharness.New(agentharness.Server, map[string]modelroute.Endpoint{
		modelroute.Intent: model, modelroute.Planning: model, modelroute.System: model,
	})
	if err != nil {
		t.Fatal(err)
	}
	decider := &draftThenFinish{}
	systemAgent, err := fleet.NewSystemAgent(profile, decider)
	if err != nil {
		t.Fatal(err)
	}
	authenticator, err := auth.New(auth.Options{OperatorUser: "op", OperatorPass: "pw",
		DeviceCredentials: map[string]string{"robot-1": "worker-token"},
		AssistCredentials: map[string]string{"robot-1": "assist-token"}})
	if err != nil {
		t.Fatal(err)
	}
	token, _, err := authenticator.Login(context.Background(), "op", "pw")
	if err != nil {
		t.Fatal(err)
	}
	server := fleet.NewServer(service, nil, fleet.WithAuthenticator(authenticator), fleet.WithSystemAgent(systemAgent))
	withoutModel := fleet.NewServer(service, nil, fleet.WithAuthenticator(authenticator))
	unavailableRequest := httptest.NewRequest(http.MethodPost, "/v1/agent/system", strings.NewReader(`{"goal":"inspect fleet"}`))
	unavailableRequest.Header.Set("Authorization", "Bearer "+token)
	unavailableResponse := httptest.NewRecorder()
	withoutModel.Handler().ServeHTTP(unavailableResponse, unavailableRequest)
	if unavailableResponse.Code != http.StatusServiceUnavailable {
		t.Fatalf("system agent without configured model returned %d", unavailableResponse.Code)
	}
	call := func(header, credential, body string) *httptest.ResponseRecorder {
		request := httptest.NewRequest(http.MethodPost, "/v1/agent/system", strings.NewReader(body))
		request.Header.Set(header, credential)
		if header == "X-Device-Token" {
			request.Header.Set("X-Robot-ID", "robot-1")
		}
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		return response
	}
	for _, credential := range []string{"worker-token", "assist-token"} {
		if response := call("X-Device-Token", credential, `{"goal":"create a draft"}`); response.Code != http.StatusForbidden {
			t.Fatalf("device scope %q reached system agent: %d", credential, response.Code)
		}
	}
	if response := call("Authorization", "Bearer "+token, `{"goal":"create a draft","unexpected":true}`); response.Code != http.StatusBadRequest {
		t.Fatalf("unknown request field got %d", response.Code)
	}
	response := call("Authorization", "Bearer "+token, `{"goal":"create a draft for robot 1"}`)
	if response.Code != http.StatusOK || decider.calls != 2 || !strings.Contains(response.Body.String(), `"completed":true`) {
		t.Fatalf("system agent status=%d calls=%d body=%s", response.Code, decider.calls, response.Body.String())
	}
	list, err := service.List(context.Background())
	if err != nil || len(list) != 1 {
		t.Fatalf("task drafts=%d err=%v", len(list), err)
	}
	if len(list[0].Events) != 1 || list[0].Events[0].Type != "TASK_CREATED" {
		t.Fatalf("model approved or dispatched task: %#v", list[0].Events)
	}
	revisions, err := service.ListRevisions(context.Background(), list[0].ID)
	if err != nil || len(revisions) != 1 || !revisions[0].Revision.ApprovalRequired {
		t.Fatalf("draft bypassed approval: %#v err=%v", revisions, err)
	}
}
