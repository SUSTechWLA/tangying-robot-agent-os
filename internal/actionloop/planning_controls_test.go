package actionloop_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

func TestPlanningHTTPExposesOnlyProposalAndBlockedAndRetainsExactInput(t *testing.T) {
	for _, reply := range []string{"propose_capability_plan", actionloop.ControlBlockedTool, actionloop.ControlFinishTool} {
		t.Run(reply, func(t *testing.T) {
			var received string
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				body, err := io.ReadAll(r.Body)
				if err != nil {
					t.Error(err)
				}
				received = string(body)
				_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": toolCallMessage(reply, `{"reason":"依据现有证据作出选择"}`)}}})
			}))
			defer server.Close()
			document := agentcontext.Document{SchemaVersion: agentcontext.Version, Role: "planning", Stage: "planning", Goal: "制定完整计划", Scope: agentcontext.Scope{RobotID: "robot-planning"}}
			request := actionloop.Request{Role: "planning", Goal: document.Goal, Round: 9,
				Observation: actionloop.Observation{Context: &document, Summary: "只可提交计划或说明无法继续"},
				Tools:       []actionloop.Tool{{Name: "propose_capability_plan", InputSchema: map[string]any{"type": "object"}}}}
			snapshot, err := actionloop.SnapshotFor(request)
			if err != nil {
				t.Fatal(err)
			}
			request.ContextSnapshot = &snapshot
			decision, err := newDecider(t, server).Decide(context.Background(), request)
			if reply == actionloop.ControlFinishTool {
				if err == nil || !strings.Contains(err.Error(), "not offered") || decision.Done {
					t.Fatalf("unoffered finish was accepted: %+v %v", decision, err)
				}
			} else if err != nil || (reply == actionloop.ControlBlockedTool && decision.Blocked == "") || (reply == "propose_capability_plan" && decision.Tool != reply) {
				t.Fatalf("allowed planning control was rejected: %+v %v", decision, err)
			}
			if received == "" || snapshot.ModelRequestJSON != received || snapshot.ModelRequestSHA256 != agentcontext.Hash(received) || snapshot.Scope != document.Scope {
				t.Fatal("real HTTP input or scope was not retained")
			}
			var wire struct {
				Tools []struct {
					Function struct{ Name string }
				}
				Messages []struct{ Role, Content string }
			}
			if err := json.Unmarshal([]byte(received), &wire); err != nil {
				t.Fatal(err)
			}
			if len(wire.Tools) != 2 || wire.Tools[0].Function.Name != "propose_capability_plan" || wire.Tools[1].Function.Name != actionloop.ControlBlockedTool {
				t.Fatalf("planning HTTP exposes unrelated controls: %+v", wire.Tools)
			}
			if len(wire.Messages) != 2 || wire.Messages[1].Role != "user" || wire.Messages[1].Content != snapshot.Text {
				t.Fatal("retained snapshot differs from the model input")
			}
		})
	}
}
