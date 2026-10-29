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

type readonlyExperiencePlan struct{}

func (readonlyExperiencePlan) PlanGoal(context.Context, string) (orchestration.Bundle, bool, error) {
	return orchestration.Bundle{Source: orchestration.SourceLLM, Capabilities: &capability.Plan{
		RobotID: "gazebo-home_furnished", CatalogRevision: "catalog",
		Calls: []capability.Call{{Tool: "navigation.status"}, {Tool: "mapping.inventory"}, {Tool: "mapping.status"}},
	}}, true, nil
}

func TestReadOnlyCapabilityExperienceNamesReadsWithoutInventingTransportOrProgress(t *testing.T) {
	ctx := context.Background()
	s := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	s.SetGoalPlanner(readonlyExperiencePlan{})
	task, err := s.Create(ctx, "只读检查导航、保存地图和建图状态，不要移动", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	history, err := s.ListRevisions(ctx, task.ID)
	if err != nil || len(history) != 1 {
		t.Fatalf("history=%+v error=%v", history, err)
	}
	for _, state := range []taskgraph.TaskState{taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting, taskgraph.StateVerifying, taskgraph.StateSucceeded} {
		if err := s.Transition(ctx, task.ID, state, "fixture read-only execution"); err != nil {
			t.Fatal(err)
		}
	}
	for index, call := range task.Plan.Capabilities.Calls {
		for _, status := range []string{"RUNNING", "AWAITING_EVIDENCE", "CONFIRMED"} {
			if _, err := s.AppendEvent(ctx, task.ID, localProgressEvent(history[0].Revision.Steps[index].StepID, call.Tool, status, 1)); err != nil {
				t.Fatal(err)
			}
		}
	}
	w := httptest.NewRecorder()
	console.NewServer(s, &executorSpy{}).Handler().ServeHTTP(w, httptest.NewRequest("GET", "/v1/tasks/"+task.ID+"/experience", nil))
	var view tasks.TaskExperience
	if err := json.Unmarshal(w.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if w.Code != 200 || len(view.Steps) != 3 || len(view.Activities) != 9 {
		t.Fatalf("HTTP %d view=%+v", w.Code, view)
	}
	for _, step := range view.Steps {
		if step.Status != tasks.StepSatisfied || !strings.Contains(step.Explanation, "只读") || strings.Contains(step.Explanation, "contract") || step.CapabilityLabel == "机器人能力" {
			t.Errorf("read-only step is not reviewable: %+v", step)
		}
		if !strings.Contains(step.EvidenceText, "只读结果已返回") || strings.Contains(step.EvidenceText, "等待环境") {
			t.Errorf("completed read still asks for physical effect evidence: %+v", step)
		}
	}
	for _, activity := range view.Activities {
		if activity.DisplayName == "机器人能力" || strings.Contains(activity.Purpose, "正在执行") || activity.Purpose == "" {
			t.Errorf("purpose invents a current running state: %+v", activity)
		}
	}
}
