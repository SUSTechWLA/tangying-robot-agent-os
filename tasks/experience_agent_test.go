package tasks_test

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The acceptance criterion for the agent runtime is that a person reviewing a
// finished task can see what both agents said, in the same place they already
// look. These tests pin the projection that makes that true.

func agentEventLedger() []tasks.TaskEvent {
	occurred := time.Unix(1000, 0).UTC()
	return []tasks.TaskEvent{
		{Sequence: 1, Type: "TASK_CREATED", OccurredAt: occurred},
		// Execution: a tool the robot ran. It is not an agent statement.
		{Sequence: 2, Type: "TOOL_ACTIVITY", OccurredAt: occurred, StepID: "pick", Payload: map[string]any{
			"toolName": "manipulation.pick", "activityStatus": "SENDING", "stepId": "pick",
		}},
		// The observing agent's finding, written under its own topic.
		{Sequence: 3, Type: "ops.anomaly_detected", OccurredAt: occurred, StepID: "pick", Payload: map[string]any{
			"agent": "ops", "agentVersion": "1", "correlationId": "task-1",
			"anomalyId": "ANOMALY_UNVERIFIED_MUTATION@execution", "severity": "critical",
			"component": "execution", "code": "ANOMALY_UNVERIFIED_MUTATION",
			"message": "有物理动作已下发但没有确认结果", "evidence": []any{"obs-1", "obs-2"},
		}},
		{Sequence: 4, Type: "ops.root_cause_hypothesis", OccurredAt: occurred, Payload: map[string]any{
			"agent": "ops", "category": "UNKNOWN_OUTCOME", "confidence": 1.0,
			"summary": "结果未知", "automaticRetryForbidden": true,
		}},
		// The executing agent's own event, attributed to it.
		{Sequence: 5, Type: "agent.health_changed", OccurredAt: occurred, Payload: map[string]any{
			"agent": "task", "agentVersion": "1", "previous": "UNKNOWN", "current": "HEALTHY",
		}},
		{Sequence: 6, Type: "STATE_CHANGED", OccurredAt: occurred, Payload: map[string]any{
			"previousState": "PLANNING", "state": "EXECUTING",
		}},
	}
}

func TestAgentEventsFromEventsProjectsOnlyAgentStatements(t *testing.T) {
	projected := tasks.AgentEventsFromEvents(agentEventLedger())
	if len(projected) != 3 {
		t.Fatalf("projected %d agent events, want 3: %#v", len(projected), projected)
	}
	for _, view := range projected {
		if view.Agent == "" {
			t.Fatalf("an event with no attributed agent was projected: %#v", view)
		}
	}
	// Execution is not an agent's statement about execution. Folding a tool
	// activity into this list would attribute the robot's action to an agent.
	for _, view := range projected {
		if view.Topic == "TOOL_ACTIVITY" || view.Topic == "STATE_CHANGED" || view.Topic == "TASK_CREATED" {
			t.Fatalf("an execution event was projected as an agent statement: %#v", view)
		}
	}
}

func TestAgentEventsCarryWhatAReaderNeeds(t *testing.T) {
	projected := tasks.AgentEventsFromEvents(agentEventLedger())
	anomaly, ok := findAgentEvent(projected, "ops.anomaly_detected")
	if !ok {
		t.Fatalf("the observer's finding is missing: %#v", projected)
	}
	if anomaly.Agent != "ops" {
		t.Fatalf("publishing agent = %q, want ops", anomaly.Agent)
	}
	if anomaly.Severity != "critical" {
		t.Fatalf("severity = %q; a console cannot rank findings without it", anomaly.Severity)
	}
	if anomaly.Code != "ANOMALY_UNVERIFIED_MUTATION" {
		t.Fatalf("code = %q", anomaly.Code)
	}
	if len(anomaly.EvidenceIDs) != 2 {
		t.Fatalf("evidence = %#v; a finding with no evidence reference cannot be reviewed", anomaly.EvidenceIDs)
	}
	if anomaly.StepID != "pick" {
		t.Fatalf("step id = %q, want the step the finding is about", anomaly.StepID)
	}
	if anomaly.Summary == "" {
		t.Fatal("a finding shown to a person must carry a readable summary")
	}
	// The correlation id is transport bookkeeping and is lifted out of the
	// payload rather than repeated inside it.
	if _, present := anomaly.Payload["correlationId"]; present {
		t.Fatalf("transport bookkeeping leaked into the projected payload: %#v", anomaly.Payload)
	}
	if _, present := anomaly.Payload["agent"]; present {
		t.Fatalf("the attribution is a view field and must not be duplicated: %#v", anomaly.Payload)
	}
}

func TestAgentEventsAreOrderedByLedgerSequence(t *testing.T) {
	projected := tasks.AgentEventsFromEvents(agentEventLedger())
	for index := 1; index < len(projected); index++ {
		if projected[index].Sequence <= projected[index-1].Sequence {
			t.Fatalf("agent events are out of ledger order: %#v", projected)
		}
	}
}

// The replay is what a person opens, so the check that matters is on the whole
// experience: both agents must be visible there.
func TestReplayShowsBothAgents(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{
		ID: "task-replay", Intent: parsed, State: taskgraph.StateReady,
		Approved: true, CurrentRevision: 1,
	}
	if err := store.Create(context.Background(), task); err != nil {
		t.Fatal(err)
	}

	// Record the agent-authored entries the way the runtime does: appended to
	// the same durable ledger as everything else.
	for _, event := range agentEventLedger()[2:] {
		if _, err := service.AppendEvent(context.Background(), task.ID, event); err != nil {
			t.Fatal(err)
		}
	}
	current, err := service.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	// The task is seeded directly rather than created through the service, so
	// it has no revision record in the store. The projection only needs the
	// coordinates and the request, so they are supplied here rather than
	// widening the test into a revision-lifecycle test it is not about.
	record := tasks.RevisionRecord{Revision: tasks.TaskRevision{
		TaskID: current.ID, Revision: 1, Request: current.Request,
	}}

	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: current, Revision: record, Events: current.Events,
	})

	agents := map[string]bool{}
	for _, event := range view.AgentEvents {
		agents[event.Agent] = true
	}
	if !agents["ops"] {
		t.Fatalf("the replay does not show the observing agent: %#v", view.AgentEvents)
	}
	if !agents["task"] {
		t.Fatalf("the replay does not show the executing agent: %#v", view.AgentEvents)
	}
}

// Adding the field must not change what an existing caller sees. A caller with
// no raw events gets exactly the experience it got before.
func TestReplayWithoutEventsIsUnchanged(t *testing.T) {
	store := tasks.NewMemoryStore()
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "task-legacy", Intent: parsed, CurrentRevision: 1}
	record := tasks.RevisionRecord{Revision: tasks.TaskRevision{TaskID: task.ID, Revision: 1, Request: "r"}}
	view := tasks.ProjectExperience(tasks.ExperienceInput{Task: task, Revision: record})
	if len(view.AgentEvents) != 0 {
		t.Fatalf("agent events appeared without any ledger: %#v", view.AgentEvents)
	}
	// The field is omitted when empty, so an existing consumer's JSON is
	// unchanged.
	if view.SchemaVersion != "task.experience.v1" {
		t.Fatalf("schema version changed to %q", view.SchemaVersion)
	}
	_ = store
}

func findAgentEvent(events []tasks.AgentEventView, topic string) (tasks.AgentEventView, bool) {
	for _, event := range events {
		if event.Topic == topic {
			return event, true
		}
	}
	return tasks.AgentEventView{}, false
}
