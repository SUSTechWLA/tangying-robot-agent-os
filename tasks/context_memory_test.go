package tasks

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func memoryActions(t *testing.T, document agentcontext.Document) []contextActionMemory {
	t.Helper()
	var actions []contextActionMemory
	for _, record := range document.Records {
		if !strings.HasPrefix(record.ID, "memory:action:") {
			continue
		}
		if record.Kind != "verification" {
			t.Fatal("exact execution source must remain available for archive retrieval")
		}
		var item contextActionMemory
		if err := json.Unmarshal([]byte(record.Statement[strings.Index(record.Statement, "{"):]), &item); err != nil {
			t.Fatal(err)
		}
		actions = append(actions, item)
	}
	return actions
}

func lifecycleEvent(step, command, robot string, revision int, status string) TaskEvent {
	return TaskEvent{Type: "TOOL_ACTIVITY", StepID: step, Payload: map[string]any{
		"stepId": step, "toolName": "manipulation.pick", "commandId": command, "robotId": robot,
		"taskRevision": revision, "activityStatus": status, "mutatesWorld": true,
	}}
}

func sequenceEvents(task *Task) {
	for i := range task.Events {
		task.Events[i].Sequence = uint64(i + 1)
	}
}

func TestContextRebuildsEarlyActionMemoryAfterLongHistoryAndRestart(t *testing.T) {
	task := Task{ID: "long", Request: "把杯子送回厨房", CurrentRevision: 2}
	task.Events = []TaskEvent{
		lifecycleEvent("same-step", "old-completed", "robot", 1, "RUNNING"),
		lifecycleEvent("same-step", "old-completed", "robot", 1, "CONFIRMED"),
		lifecycleEvent("same-step", "old-unknown", "robot", 1, "RUNNING"),
		lifecycleEvent("same-step", "old-unknown", "robot", 1, "FAILED"),
		lifecycleEvent("same-step", "new-completed", "robot", 2, "RUNNING"),
		lifecycleEvent("same-step", "new-completed", "robot", 2, "CONFIRMED"),
	}
	for i := 0; i < 140; i++ {
		task.Events = append(task.Events, TaskEvent{Type: "ops.root_cause_hypothesis", StepID: "unrelated-background-step", Payload: map[string]any{
			"detail": "diagnostic append", "nested": []any{map[string]any{"agent_context": "must-not-recur", "decision_rounds": "must-not-recur"}},
		}})
	}
	sequenceEvents(&task)
	now := time.Unix(100, 0)
	document := ContextFor(task, "recovery", now)
	actions := memoryActions(t, document)
	if len(actions) != 3 || actions[0].State != "COMPLETION_RECORDED" || actions[1].State != "OUTCOME_UNKNOWN" || !actions[1].Unresolved || actions[1].Revision != 1 || actions[2].Revision != 2 {
		t.Fatalf("lost early completed/unknown records: %+v", actions)
	}
	if document.CurrentStep != "same-step" {
		t.Fatalf("background diagnostics rewrote execution position: %q", document.CurrentStep)
	}
	view := DecisionContextFor(task, "recovery", now)
	if !view.Snapshot.Complete || view.Snapshot.OmittedEvents != 0 || view.Snapshot.LedgerSequence == nil || *view.Snapshot.LedgerSequence != int64(len(task.Events)) {
		t.Fatalf("incorrect full-ledger coverage: %+v", view.Snapshot)
	}
	wire, _ := json.Marshal(task)
	var restarted Task
	if err := json.Unmarshal(wire, &restarted); err != nil {
		t.Fatal(err)
	}
	before, _ := json.Marshal(document)
	after, _ := json.Marshal(ContextFor(restarted, "recovery", now))
	if string(before) != string(after) || strings.Contains(string(after), "must-not-recur") {
		t.Fatal("context drifted after persistent JSON reload or retained recursive summaries")
	}
}

func TestContextUnboundOrDifferentAttemptCannotClosePendingAction(t *testing.T) {
	for _, tc := range []struct {
		name string
		edit func(*TaskEvent)
	}{
		{"missing command", func(event *TaskEvent) { delete(event.Payload, "commandId") }},
		{"different command", func(event *TaskEvent) { event.Payload["commandId"] = "command-other" }},
		{"different robot", func(event *TaskEvent) { event.Payload["robotId"] = "robot-other" }},
		{"different revision", func(event *TaskEvent) { event.Payload["taskRevision"] = 2 }},
		{"fractional revision", func(event *TaskEvent) { event.Payload["taskRevision"] = 1.2 }},
		{"conflicting revision", func(event *TaskEvent) { event.Payload["plan_revision"] = 2 }},
		{"conflicting step", func(event *TaskEvent) { event.Payload["stepId"] = "another-step" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			started := lifecycleEvent("pick", "command", "robot", 1, "RUNNING")
			confirmed := lifecycleEvent("pick", "command", "robot", 1, "CONFIRMED")
			tc.edit(&confirmed)
			task := Task{ID: "t", Request: "pick", CurrentRevision: 1, Events: []TaskEvent{started, confirmed}}
			actions := memoryActions(t, ContextFor(task, "recovery", time.Unix(1, 0)))
			if len(actions) != 2 || !actions[0].Unresolved || actions[0].State != "DISPATCH_RECORDED" {
				t.Fatalf("foreign receipt closed pending physical dispatch: %+v", actions)
			}
		})
	}
}

func TestContextCapabilityCompletionRequiresMatchingCommandAndKeepsEvidence(t *testing.T) {
	for _, missingCommand := range []bool{false, true} {
		t.Run(fmt.Sprint(missingCommand), func(t *testing.T) {
			task := Task{ID: "t", Request: "建图", CurrentRevision: 3, Plan: &orchestration.Bundle{Capabilities: &capability.Plan{
				RobotID: "robot", Calls: []capability.Call{{Tool: "mapping.build"}},
			}}}
			call := TaskEvent{Type: "CAPABILITY_CALL", StepID: "rev-3-cap-01", Payload: map[string]any{"tool": "mapping.build", "commandId": "c", "robotId": "robot", "taskRevision": 3}}
			verified := TaskEvent{Type: "CAPABILITY_VERIFIED", StepID: call.StepID, Payload: map[string]any{"tool": "mapping.build", "commandId": "c", "robotId": "robot", "taskRevision": 3, "evidenceIds": []string{"e-1"}}}
			if missingCommand {
				delete(verified.Payload, "commandId")
			}
			task.Events = []TaskEvent{call, verified}
			d := ContextFor(task, "handoff", time.Unix(1, 0))
			actions := memoryActions(t, d)
			if missingCommand {
				if len(actions) != 2 || !actions[0].Unresolved || d.Steps[0].State != "DISPATCH_RECORDED" {
					t.Fatalf("unbound historical capability receipt closed dispatch: %+v", actions)
				}
			} else if len(actions) != 1 || actions[0].Unresolved || actions[0].State != "COMPLETION_RECORDED" || !reflect.DeepEqual(actions[0].EvidenceIDs, []string{"e-1"}) || d.Steps[0].State != "COMPLETION_RECORDED" {
				t.Fatalf("lost bound historical completion: %+v", actions)
			}
		})
	}
}

func TestRevisionContextKeepsEarlyConstraintsAtTheirSourceRevision(t *testing.T) {
	old := RevisionRecord{Revision: TaskRevision{TaskID: "task", Revision: 1, Request: "保持杯子直立并避开人员", Intent: manipulation.Intent{Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true}}}, Status: RevisionSuperseded}
	current := RevisionRecord{Revision: TaskRevision{TaskID: "task", Revision: 2, BaseRevision: 1, Request: "改送到厨房"}, Status: RevisionActive}
	foreign := RevisionRecord{Revision: TaskRevision{TaskID: "another-task", Revision: 3, Request: "do-not-import"}}
	task := Task{ID: "task", Request: current.Revision.Request, CurrentRevision: 2}
	d := ContextForRevisions(task, []RevisionRecord{current, foreign, old}, "recovery", time.Unix(1, 0))
	if d.Goal != current.Revision.Request || d.Scope.PlanRevision != 2 {
		t.Fatal("old request replaced current task scope")
	}
	var oldSource, coverage bool
	for _, record := range d.Records {
		if record.ID == "revision-source:1" {
			oldSource = record.Kind == "guard" && record.Scope.PlanRevision == 1 && strings.Contains(record.Statement, "保持杯子直立") && strings.Contains(record.Statement, `"keepUpright":true`) && strings.Contains(record.Statement, `"avoidHumans":true`)
		}
		if record.ID == "revision-source:coverage" {
			coverage = strings.Contains(record.Statement, "完整=true")
		}
		if strings.Contains(record.Statement, "do-not-import") {
			t.Fatal("cross-task revision imported")
		}
	}
	if !oldSource || !coverage {
		t.Fatal("early constraints or revision provenance lost")
	}
	missing := ContextForRevisions(task, []RevisionRecord{current}, "recovery", time.Unix(1, 0))
	for _, record := range missing.Records {
		if record.ID == "revision-source:coverage" && !strings.Contains(record.Statement, "完整=false") {
			t.Fatal("missing historical request was treated as recovered")
		}
	}
	wire, _ := json.Marshal([]RevisionRecord{current, old})
	var reloaded []RevisionRecord
	_ = json.Unmarshal(wire, &reloaded)
	before, _ := json.Marshal(d)
	after, _ := json.Marshal(ContextForRevisions(task, reloaded, "recovery", time.Unix(1, 0)))
	if string(before) != string(after) {
		t.Fatal("revision memory changed on reload")
	}
}

func TestDecisionContextDoesNotClaimCompleteHistoryAcrossLedgerGap(t *testing.T) {
	task := Task{ID: "t", Request: "inspect", Events: []TaskEvent{{Sequence: 1}, {Sequence: 4}}}
	p := DecisionContextFor(task, "ops", time.Unix(1, 0))
	if p.Snapshot.Complete || p.Snapshot.OmittedEvents != 0 {
		t.Fatalf("source gap hidden by complete projection: %+v", p.Snapshot)
	}
}

func TestCurrentStepUsesLastExecutionEventButUnknownAttemptRemainsBlocking(t *testing.T) {
	task := Task{ID: "t", Request: "complete route", CurrentRevision: 1, Events: []TaskEvent{
		lifecycleEvent("first", "command-1", "robot", 1, "CONFIRMED"),
		lifecycleEvent("last", "command-2", "robot", 1, "CONFIRMED"),
		{Type: "ops.recovery_plan", StepID: "diagnostic-only"},
	}}
	d := ContextFor(task, "handoff", time.Unix(1, 0))
	if d.CurrentStep != "last" {
		t.Fatalf("completed phase cursor = %q", d.CurrentStep)
	}
	task.Events = append([]TaskEvent{lifecycleEvent("uncertain", "command-unknown", "robot", 1, "FAILED")}, task.Events...)
	d = ContextFor(task, "handoff", time.Unix(1, 0))
	if d.CurrentStep != "uncertain" {
		t.Fatalf("unrelated completion hid blocking unknown action: %q", d.CurrentStep)
	}
}

func TestPolicyPreparationFailureNeverCreatesPhysicalDispatchMemory(t *testing.T) {
	task := Task{ID: "t", Request: "pick", CurrentRevision: 1, Events: []TaskEvent{{
		Type: "POLICY_PREPARATION_FAILED", StepID: "pick", Payload: map[string]any{
			"tool": "manipulation.pick", "toolName": "policy.prepare", "commandId": "command", "taskRevision": 1,
			"phase": "pre_dispatch", "physicalDispatched": false, "mutatesWorld": false, "rejected": true,
		},
	}}}
	actions := memoryActions(t, ContextFor(task, "recovery", time.Unix(1, 0)))
	if len(actions) != 1 || actions[0].Unresolved || actions[0].OutcomeUnknown || actions[0].State != "REJECTED_BEFORE_DISPATCH" {
		t.Fatalf("policy rejection invented physical action: %+v", actions)
	}
}

func TestContextMemoryCanReconcileOnlyTheSameUnknownCommand(t *testing.T) {
	task := Task{ID: "t", Request: "pick", CurrentRevision: 1, Events: []TaskEvent{
		lifecycleEvent("pick", "command", "robot", 1, "RUNNING"),
		lifecycleEvent("pick", "command", "robot", 1, "FAILED"),
		lifecycleEvent("pick", "command", "robot", 1, "CONFIRMED"),
	}}
	actions := memoryActions(t, ContextFor(task, "recovery", time.Unix(1, 0)))
	if len(actions) != 1 || actions[0].Unresolved || actions[0].OutcomeUnknown || actions[0].State != "COMPLETION_RECORDED" || len(actions[0].SourceIDs) != 3 {
		t.Fatalf("durable reconciliation of the same command was lost: %+v", actions)
	}
}

func TestContextMemoryDeduplicatesIdenticalEventContentAndBridgeMirror(t *testing.T) {
	first := lifecycleEvent("pick", "command", "robot", 1, "RUNNING")
	first.Payload["eventId"] = "same-fact"
	duplicate := first
	duplicate.Sequence = 9 // Re-appending the same fact is a new ledger position.
	mirror := lifecycleEvent("pick", "command", "robot", 1, "RUNNING")
	mirror.Type = "action.executed"
	mirror.Payload["eventId"], mirror.Payload["agent"], mirror.Payload["correlationId"] = "same-fact", "task", "t"
	task := Task{ID: "t", Request: "pick", CurrentRevision: 1, Events: []TaskEvent{first, duplicate, mirror}}
	actions := memoryActions(t, ContextFor(task, "recovery", time.Unix(1, 0)))
	if len(actions) != 1 || !actions[0].Unresolved || actions[0].IdentityConflict || len(actions[0].SourceIDs) != 1 {
		t.Fatalf("identical retransmission duplicated or conflicted with original fact: %+v", actions)
	}
}

func TestContextMemoryRetainsConflictingEventIDsWithoutClosingDispatch(t *testing.T) {
	for _, tc := range []struct {
		name string
		edit func(*TaskEvent)
	}{
		{"different status", func(event *TaskEvent) { event.Payload["activityStatus"] = "CONFIRMED" }},
		{"different command", func(event *TaskEvent) { event.Payload["commandId"] = "another-command" }},
		{"different revision", func(event *TaskEvent) { event.Payload["taskRevision"] = 2 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			first := lifecycleEvent("pick", "command", "robot", 1, "RUNNING")
			first.Payload["eventId"] = "colliding-id"
			conflict := lifecycleEvent("pick", "command", "robot", 1, "RUNNING")
			conflict.Payload["eventId"] = "colliding-id"
			tc.edit(&conflict)
			later := lifecycleEvent("pick", "command", "robot", 1, "CONFIRMED")
			later.Payload["eventId"] = "later-completion"
			task := Task{ID: "t", Request: "pick", CurrentRevision: 1, Events: []TaskEvent{first, conflict, conflict, later}}
			sequenceEvents(&task)
			document := ContextFor(task, "recovery", time.Unix(1, 0))
			actions := memoryActions(t, document)
			if len(actions) != 3 || actions[2].State != "COMPLETION_RECORDED" {
				t.Fatalf("conflicting variants were omitted or exact retransmission was duplicated: %+v", actions)
			}
			for _, item := range actions[:2] {
				if !item.IdentityConflict || item.EventID != "colliding-id" || item.State != "EVENT_ID_CONFLICT" || !item.Unresolved || !item.OutcomeUnknown || len(item.SourceIDs) != 1 {
					t.Fatalf("conflicting event identity closed or obscured a dispatch: %+v", item)
				}
			}
			wire, _ := json.Marshal(task)
			var reloaded Task
			if err := json.Unmarshal(wire, &reloaded); err != nil {
				t.Fatal(err)
			}
			before, _ := json.Marshal(document)
			after, _ := json.Marshal(ContextFor(reloaded, "recovery", time.Unix(1, 0)))
			if string(before) != string(after) {
				t.Fatal("event conflict projection changed after persistent reload")
			}
		})
	}
}

func TestContextMemoryRecognizesRunnerBridgeSupplementalFieldsOnly(t *testing.T) {
	for _, reverse := range []bool{false, true} {
		t.Run(fmt.Sprint(reverse), func(t *testing.T) {
			raw := lifecycleEvent("pick", "command", "robot", 1, "CONFIRMED")
			delete(raw.Payload, "mutatesWorld") // The legacy TOOL_ACTIVITY does not emit it.
			raw.Payload["eventId"] = "runner-event"
			raw.Payload["aggregateVersion"], raw.Payload["fencingToken"] = 9, 12
			raw.Payload["arguments"] = map[string]any{"target_ref": "cup"}
			raw.Payload["evidenceIds"] = []string{"capture"}
			mirror := TaskEvent{Type: "action.executed", StepID: raw.StepID, Payload: map[string]any{}}
			for key, value := range raw.Payload {
				mirror.Payload[key] = value
			}
			mirror.Payload["safetyLevel"], mirror.Payload["agent"], mirror.Payload["correlationId"] = "physical_motion", "task", "t"
			raw.Payload["receiptObservationId"], raw.Payload["evidenceSource"] = "receipt", "post_tool_observation"
			events := []TaskEvent{raw, mirror}
			if reverse {
				events[0], events[1] = events[1], events[0]
			}
			task := Task{ID: "t", Request: "pick", CurrentRevision: 1, Events: events}
			actions := memoryActions(t, ContextFor(task, "recovery", time.Unix(1, 0)))
			if len(actions) != 1 || actions[0].IdentityConflict || actions[0].State != "COMPLETION_RECORDED" {
				t.Fatalf("normal runner publication became a false conflict: %+v", actions)
			}
			// Both sides now declare safetyLevel and disagree. Supplemental does
			// not mean disposable when there are conflicting source assertions.
			raw.Payload["safetyLevel"] = "read_only"
			actions = memoryActions(t, ContextFor(task, "recovery", time.Unix(1, 0)))
			if len(actions) != 2 || !actions[0].IdentityConflict || !actions[1].IdentityConflict {
				t.Fatalf("conflicting shared bridge fields were ignored: %+v", actions)
			}
		})
	}
}

func TestContextMemoryBridgeCannotHideThirdConflictingVariant(t *testing.T) {
	raw := lifecycleEvent("pick", "command", "robot", 1, "RUNNING")
	raw.Payload["eventId"] = "runner-event"
	var variants []TaskEvent
	variants = append(variants, raw)
	for _, level := range []string{"physical_motion", "read_only"} {
		mirror := TaskEvent{Type: "action.executed", StepID: raw.StepID, Payload: map[string]any{}}
		for key, value := range raw.Payload {
			mirror.Payload[key] = value
		}
		mirror.Payload["safetyLevel"] = level
		variants = append(variants, mirror)
	}
	task := Task{ID: "t", Request: "pick", CurrentRevision: 1, Events: variants}
	actions := memoryActions(t, ContextFor(task, "recovery", time.Unix(1, 0)))
	if len(actions) != 3 {
		t.Fatalf("partially overlapping bridge variants hid a conflict: %+v", actions)
	}
	for _, item := range actions {
		if !item.IdentityConflict || !item.Unresolved {
			t.Fatalf("source conflict was treated as equivalent retransmission: %+v", item)
		}
	}
}
