package tasks_test

import (
	"bytes"
	"encoding/json"
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
