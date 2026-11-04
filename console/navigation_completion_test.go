package console_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestNavigationExperienceNeedsSuccessfulTaskAndEachCurrentArrival(t *testing.T) {
	for _, tc := range []struct {
		name, request string
		state         taskgraph.TaskState
		events        []tasks.TaskEvent
		satisfied     bool
	}{
		{"single verified", "去厨房", taskgraph.StateSucceeded, []tasks.TaskEvent{localProgressEvent("verify_arrival_00", "verify_arrival", "CONFIRMED", 1, "capture")}, true},
		{"not terminal", "去厨房", taskgraph.StateExecuting, []tasks.TaskEvent{localProgressEvent("verify_arrival_00", "verify_arrival", "CONFIRMED", 1, "capture")}, false},
		{"missing capture", "去厨房", taskgraph.StateSucceeded, []tasks.TaskEvent{localProgressEvent("verify_arrival_00", "verify_arrival", "CONFIRMED", 1)}, false},
		{"wrong tool", "去厨房", taskgraph.StateSucceeded, []tasks.TaskEvent{localProgressEvent("verify_arrival_00", "observe_scene", "CONFIRMED", 1, "capture")}, false},
		{"old revision", "去厨房", taskgraph.StateSucceeded, []tasks.TaskEvent{localProgressEvent("verify_arrival_00", "verify_arrival", "CONFIRMED", 2, "capture")}, false},
		{"missing destination", "先去厨房，再去卧室", taskgraph.StateSucceeded, []tasks.TaskEvent{localProgressEvent("verify_arrival_00", "verify_arrival", "CONFIRMED", 1, "capture")}, false},
		{"later failed recheck", "去厨房", taskgraph.StateSucceeded, []tasks.TaskEvent{localProgressEvent("verify_arrival_00", "verify_arrival", "CONFIRMED", 1, "capture"), localProgressEvent("verify_arrival_00/resume-read/new", "verify_arrival", "FAILED", 1)}, false},
		{"all destinations", "先去厨房，再去卧室", taskgraph.StateSucceeded, []tasks.TaskEvent{localProgressEvent("verify_arrival_00", "verify_arrival", "CONFIRMED", 1, "first"), localProgressEvent("verify_arrival_01", "verify_arrival", "CONFIRMED", 1, "second")}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			s := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
			task, err := s.Create(ctx, tc.request, "gazebo")
			if err != nil {
				t.Fatal(err)
			}
			for _, state := range []taskgraph.TaskState{taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting, taskgraph.StateVerifying, taskgraph.StateSucceeded} {
				if err := s.Transition(ctx, task.ID, state, "test execution"); err != nil {
					t.Fatal(err)
				}
				if state == tc.state {
					break
				}
			}
			for _, event := range tc.events {
				if _, err := s.AppendEvent(ctx, task.ID, event); err != nil {
					t.Fatal(err)
				}
			}
			w := httptest.NewRecorder()
			console.NewServer(s, &executorSpy{}).Handler().ServeHTTP(w, httptest.NewRequest("GET", "/v1/tasks/"+task.ID+"/experience", nil))
			var view tasks.TaskExperience
			if err := json.Unmarshal(w.Body.Bytes(), &view); err != nil {
				t.Fatal(err)
			}
			if w.Code != 200 || len(view.Steps) != 1 || (view.Steps[0].Status == tasks.StepSatisfied) != tc.satisfied {
				t.Fatalf("HTTP %d view=%+v", w.Code, view)
			}
		})
	}
}

type reviewableCapabilityPlan struct{}

func (reviewableCapabilityPlan) PlanGoal(context.Context, string) (orchestration.Bundle, bool, error) {
	return orchestration.Bundle{Source: orchestration.SourceLLM, Capabilities: &capability.Plan{RobotID: "ground-a", CatalogRevision: "catalog",
		Calls: []capability.Call{{Tool: "robot.task", Arguments: map[string]any{"request": "巡检卧室和卫生间，最后回到客厅"}, LegacyIntent: json.RawMessage(`{"action":"home_route","routeRooms":["bedroom","bathroom","living_room"]}`)}, {Tool: "navigation.status", Arguments: map[string]any{}}}}}, true, nil
}
func TestCapabilityExperienceShowsFrozenRouteAndNamesReadOnlyStatus(t *testing.T) {
	s := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	s.SetGoalPlanner(reviewableCapabilityPlan{})
	task, err := s.Create(context.Background(), "巡检卧室和卫生间，最后回到客厅", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	w := httptest.NewRecorder()
	console.NewServer(s, &executorSpy{}).Handler().ServeHTTP(w, httptest.NewRequest("GET", "/v1/tasks/"+task.ID+"/experience", nil))
	var view tasks.TaskExperience
	if err := json.Unmarshal(w.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if len(view.Steps) != 2 || !strings.Contains(view.Steps[0].Explanation, "bedroom → bathroom → living_room") || !strings.Contains(view.Steps[0].Explanation, "巡检卧室和卫生间") || !strings.Contains(view.Steps[1].Explanation, "只读") || strings.Contains(view.Steps[1].Explanation, "contract") {
		t.Fatalf("unreviewable plan: %+v", view)
	}
}
