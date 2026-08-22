package tasks_test

import (
	"bytes"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestExperienceUsesHumanToolLanguageAndFiltersSecrets(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-1", CurrentRevision: 2, AggregateVersion: 7},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-1", Revision: 2, Request: "把方块放到右侧", Understanding: "2号机器人把方块放到右侧目标区",
		}},
		Activities: []tasks.ToolActivityInput{{
			ToolName: "manipulation.pick", Status: "AWAITING_EVIDENCE", RobotID: "robot-2", StepID: "receiver",
			Arguments: map[string]any{"targetRef": "red-block", "bearerToken": "secret-bearer", "password": "secret-password"},
			Display:   tasks.ToolDisplay{DisplayName: "拿稳物品", Purpose: "安全拿起红色方块", SafeArguments: []string{"targetRef"}},
			CommandID: "cmd-2", CatalogRevision: "catalog-2", FencingToken: 3,
		}},
	})
	if view.SchemaVersion != "task.experience.v1" || view.Activities[0].DisplayName != "拿稳物品" ||
		view.Activities[0].StatusText != "正在确认动作结果" {
		t.Fatalf("view=%#v", view)
	}
	if view.Activities[0].SafeArguments["targetRef"] != "red-block" || len(view.Activities[0].SafeArguments) != 1 {
		t.Fatalf("safe arguments=%#v", view.Activities[0].SafeArguments)
	}
	wire, _ := json.Marshal(view)
	for _, forbidden := range [][]byte{[]byte("secret-bearer"), []byte("secret-password"), []byte("bearerToken"), []byte("password")} {
		if bytes.Contains(wire, forbidden) {
			t.Fatalf("experience leaked secret field/value %q: %s", forbidden, wire)
		}
	}
	if view.Professional.Activities[0].ToolName != "manipulation.pick" || view.Professional.Activities[0].CommandID != "cmd-2" {
		t.Fatalf("professional details=%#v", view.Professional)
	}
}

func TestExperienceProjectionMatchesPersistedEventReplay(t *testing.T) {
	events := []tasks.TaskEvent{
		{Sequence: 3, Type: "TOOL_ACTIVITY", Payload: map[string]any{"toolName": "manipulation.place", "activityStatus": "RUNNING", "robotId": "robot-2", "stepId": "receiver", "taskRevision": float64(2), "aggregateVersion": float64(8)}},
		{Sequence: 4, Type: "TOOL_ACTIVITY", Payload: map[string]any{"toolName": "manipulation.place", "activityStatus": "CONFIRMED", "robotId": "robot-2", "stepId": "receiver", "taskRevision": float64(2), "aggregateVersion": float64(9), "evidenceIds": []any{"observation-9"}}},
	}
	wire, err := json.Marshal(events)
	if err != nil {
		t.Fatal(err)
	}
	var replayed []tasks.TaskEvent
	if err := json.Unmarshal(wire, &replayed); err != nil {
		t.Fatal(err)
	}
	project := func(stream []tasks.TaskEvent) tasks.TaskExperience {
		record := tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-replay", Revision: 2, Understanding: "2号机器人把红色方块放到右侧目标区",
			Steps: []tasks.RevisionStep{{StepID: "receiver", RobotID: "robot-2", Status: tasks.StepPending}},
		}}
		activities := tasks.ToolActivitiesFromEvents(stream, nil)
		tasks.OverlayActivityStatuses(&record, activities)
		return tasks.ProjectExperience(tasks.ExperienceInput{
			Task:     &tasks.Task{ID: "task-replay", CurrentRevision: 2, AggregateVersion: 9},
			Revision: record, Activities: activities,
		})
	}
	if live, replay := project(events), project(replayed); !reflect.DeepEqual(live, replay) || live.Steps[0].Status != tasks.StepSatisfied || live.Activities[1].EvidenceText == "" {
		t.Fatalf("experience replay mismatch: live=%#v replay=%#v", live, replay)
	}
}

func TestBasicRecoveryGuidanceSupportsLocalBrainFailuresAndSafeUpdates(t *testing.T) {
	failed := tasks.BasicRecoveryGuidance(tasks.RevisionActive, []tasks.ToolActivityInput{{Status: "FAILED"}})
	if failed == nil || !strings.Contains(failed.RobotSafetyState, "停止推进") {
		t.Fatalf("failed guidance=%#v", failed)
	}
	waiting := tasks.BasicRecoveryGuidance(tasks.RevisionWaitingSafePoint, nil)
	if waiting == nil || !strings.Contains(waiting.RobotSafetyState, "安全动作") {
		t.Fatalf("waiting guidance=%#v", waiting)
	}
	if healthy := tasks.BasicRecoveryGuidance(tasks.RevisionActive, nil); healthy != nil {
		t.Fatalf("healthy guidance=%#v", healthy)
	}
}

func TestExperienceFallsBackWithoutEchoingUnknownToolNameInDefaultView(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task:       &tasks.Task{ID: "task-1", CurrentRevision: 1, AggregateVersion: 1},
		Revision:   tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{TaskID: "task-1", Revision: 1}},
		Activities: []tasks.ToolActivityInput{{ToolName: "vendor.secret_raw_tool", Status: "RUNNING"}},
	})
	if view.Activities[0].DisplayName != "机器人能力" || view.Activities[0].Purpose != "机器人正在执行当前步骤" {
		t.Fatalf("fallback=%#v", view.Activities[0])
	}
	if bytes.Contains([]byte(view.Activities[0].DisplayName+view.Activities[0].Purpose), []byte("vendor.secret_raw_tool")) {
		t.Fatal("default view echoed an unknown raw tool name")
	}
}

func TestExperienceUsesServerOwnedKnownToolFallbackAndLabelsStep(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-1", CurrentRevision: 1, AggregateVersion: 1},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-1", Revision: 1, Steps: []tasks.RevisionStep{{StepID: "sender", RobotID: "robot-1"}},
		}},
		Activities: []tasks.ToolActivityInput{{ToolName: "manipulation.pick", Status: "RUNNING", StepID: "sender"}},
	})
	if view.Activities[0].DisplayName != "拿稳物品" || view.Activities[0].Purpose == "" ||
		view.Steps[0].CapabilityLabel != "拿稳物品" {
		t.Fatalf("view=%#v", view)
	}
}

func TestExperienceExplainsRealHandoffStepsWithoutTechnicalIdentifiers(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-handoff", CurrentRevision: 1, AggregateVersion: 1},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-handoff", Revision: 1, Understanding: "1号机器人把red-block放到handoff-zone，2号机器人送到right-target-zone",
			ChangeSet: tasks.ChangeSet{Retained: []string{"intent-000/internal-hash"}, Changed: []string{"intent-001/internal-hash"}, Paused: []string{"old/internal-hash"}},
			Steps: []tasks.RevisionStep{
				{StepID: "intent-000/internal-hash", Action: "pick_and_place", RobotID: "robot-1", ResourceID: "red-block", RequiredPostcondition: "red-block in handoff_zone"},
				{StepID: "intent-001/internal-hash", Action: "fetch", RobotID: "robot-2", ResourceID: "red-block", RequiredPostcondition: "red-block in target_zone/right_side"},
			},
		}},
	})

	if view.Steps[0].Explanation != "1号机器人把红色方块放到交接区" {
		t.Fatalf("sender explanation=%q", view.Steps[0].Explanation)
	}
	if view.Steps[1].Explanation != "2号机器人把红色方块送到右侧目标区" {
		t.Fatalf("receiver explanation=%q", view.Steps[1].Explanation)
	}
	if view.Understanding != "1号机器人把红色方块放到交接区，2号机器人送到右侧目标区" || view.Headline != view.Understanding {
		t.Fatalf("understanding=%q headline=%q", view.Understanding, view.Headline)
	}
	if len(view.ChangePreview.Retained) != 1 || view.ChangePreview.Retained[0] != "1号机器人把红色方块放到交接区" ||
		len(view.ChangePreview.Changed) != 1 || view.ChangePreview.Changed[0] != "2号机器人把红色方块送到右侧目标区" ||
		len(view.ChangePreview.Paused) != 1 || view.ChangePreview.Paused[0] != "机器人会先完成上一版正在执行的安全动作" {
		t.Fatalf("change preview leaked technical steps: %#v", view.ChangePreview)
	}
	for _, step := range view.Steps {
		if bytes.Contains([]byte(step.Explanation), []byte("red-block")) || bytes.Contains([]byte(step.Explanation), []byte("target-zone")) ||
			bytes.Contains([]byte(step.Explanation), []byte("target_zone")) || bytes.Contains([]byte(step.Explanation), []byte("handoff_")) {
			t.Fatalf("technical identifier leaked into user explanation: %q", step.Explanation)
		}
	}
}
