package tasks

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

func commandGuards(t *testing.T, d agentcontext.Document) []contextCommandGuard {
	t.Helper()
	var guards []contextCommandGuard
	for _, record := range d.Records {
		if !strings.HasPrefix(record.ID, "memory:command:") {
			continue
		}
		if record.Kind != "guard" {
			t.Fatal("unresolved command boundary became optional history")
		}
		var guard contextCommandGuard
		if err := json.Unmarshal([]byte(record.Statement[strings.Index(record.Statement, "{"):]), &guard); err != nil {
			t.Fatal(err)
		}
		guard.scope = record.Scope
		guards = append(guards, guard)
	}
	return guards
}

func TestCommandGuardGroupsConflictVariantsWithoutAcceptingLaterCompletion(t *testing.T) {
	task := Task{ID: "t", Request: "route", CurrentRevision: 1}
	for i, status := range []string{"SENDING", "RUNNING", "AWAITING_EVIDENCE", "CONFIRMED"} {
		original := lifecycleEvent("navigate", "command", "robot", 1, status)
		original.Payload["eventId"] = fmt.Sprintf("transition-%d", i)
		original.Payload["arguments"] = nil
		mirror := lifecycleEvent("navigate", "command", "robot", 1, status)
		mirror.Type = "action.executed"
		mirror.Payload["eventId"] = original.Payload["eventId"]
		mirror.Payload["arguments"] = map[string]any{"goalPose": []float64{1, 2, 0, 1, 0, 0, 0}}
		task.Events = append(task.Events, original, mirror)
	}
	task.Events = append(task.Events, lifecycleEvent("navigate", "command", "robot", 1, "CONFIRMED"))
	sequenceEvents(&task)
	d := ContextFor(task, "recovery", time.Unix(1, 0))
	guards := commandGuards(t, d)
	if len(guards) != 1 || guards[0].State != "EVENT_ID_CONFLICT" || !guards[0].Unresolved || !guards[0].OutcomeUnknown || !guards[0].RetryForbidden || !guards[0].IdentityConflict || len(guards[0].SourceRecords) != 9 {
		t.Fatalf("conflict disappeared or its variants multiplied required state: %+v", guards)
	}
	for _, id := range guards[0].SourceRecords {
		found := false
		for _, record := range d.Records {
			if record.ID == id {
				found = record.Kind == "verification" && strings.Contains(record.Statement, "source_event_ids")
			}
		}
		if !found {
			t.Fatalf("source %q is not exactly retrievable", id)
		}
	}
}

func TestCommandGuardsNeverMergeDifferentOrMissingExecutionBindings(t *testing.T) {
	task := Task{ID: "t", Request: "route", CurrentRevision: 2, Events: []TaskEvent{
		lifecycleEvent("same", "command", "robot", 1, "FAILED"),
		lifecycleEvent("same", "command", "robot", 2, "FAILED"),
		lifecycleEvent("same", "command", "other-robot", 1, "FAILED"),
		lifecycleEvent("same", "other-command", "robot", 1, "FAILED"),
		lifecycleEvent("same", "", "robot", 1, "FAILED"),
		lifecycleEvent("same", "", "robot", 1, "FAILED"),
		lifecycleEvent("same", "command", "", 0, "FAILED"),
		lifecycleEvent("same", "command", "", 0, "FAILED"),
	}}
	for range 2 {
		missingTool := lifecycleEvent("same", "command", "robot", 1, "FAILED")
		delete(missingTool.Payload, "toolName")
		task.Events = append(task.Events, missingTool)
	}
	sequenceEvents(&task)
	guards := commandGuards(t, ContextFor(task, "recovery", time.Unix(1, 0)))
	if len(guards) != len(task.Events) {
		t.Fatalf("different or incomplete identities merged: %+v", guards)
	}
	for _, guard := range guards {
		if !guard.Unresolved || !guard.OutcomeUnknown || !guard.RetryForbidden || len(guard.SourceRecords) != 1 {
			t.Fatalf("unresolved exact boundary lost: %+v", guard)
		}
	}
}

func TestLongCompletedHistoryRemainsExactlyRetrievableWithoutFillingGuardBudget(t *testing.T) {
	task := Task{ID: "long-success", Request: "巡检并保留所有历史", CurrentRevision: 1}
	for i := 0; i < 500; i++ {
		step, command := fmt.Sprintf("step-%03d", i), fmt.Sprintf("command-%03d", i)
		task.Events = append(task.Events, lifecycleEvent(step, command, "robot", 1, "RUNNING"), lifecycleEvent(step, command, "robot", 1, "CONFIRMED"))
	}
	sequenceEvents(&task)
	d := ContextFor(task, "recovery", time.Unix(1, 0))
	if len(commandGuards(t, d)) != 0 || d.CurrentStep != "step-499" || len(memoryActions(t, d)) != 500 {
		t.Fatal("completed history was lost or treated as unresolved")
	}
	p, err := agentcontext.ProjectManaged(d, "recovery", agentcontext.DefaultBudget())
	if err != nil || len(p.Text) > agentcontext.DefaultBudget().MaxContextBytes || len(p.Artifacts) == 0 {
		t.Fatalf("long success history exhausted required-state budget: %v", err)
	}
	var view agentcontext.Document
	if err := json.Unmarshal([]byte(p.Text), &view); err != nil {
		t.Fatal(err)
	}
	all := map[string]agentcontext.Record{}
	for _, record := range view.Records {
		all[record.ID] = record
	}
	for _, artifact := range p.Artifacts {
		data, err := artifact.SourceBytes()
		if err != nil {
			t.Fatal(err)
		}
		var records []agentcontext.Record
		if err := json.Unmarshal(data, &records); err != nil {
			t.Fatal(err)
		}
		for _, record := range records {
			all[record.ID] = record
		}
	}
	for _, source := range d.Records {
		got, ok := all[source.ID]
		left, _ := json.Marshal(source)
		right, _ := json.Marshal(got)
		if !ok || string(left) != string(right) {
			t.Fatalf("source record %q was lost or summarized", source.ID)
		}
	}
	// A reader can retrieve the earliest completion with its original command
	// and source event references, rather than trusting only the hot count.
	found := false
	for _, artifact := range p.Artifacts {
		page, err := p.ReadArtifact(d.Scope, artifact.SHA256, "memory:action:0", 0, 4096)
		if err == nil {
			found = page.Complete && strings.Contains(page.Content, "command-000") && strings.Contains(page.Content, "COMPLETION_RECORDED") && strings.Contains(page.Content, "event:1:0")
		}
	}
	if !found {
		t.Fatal("earliest exact completion is not retrievable")
	}
}
