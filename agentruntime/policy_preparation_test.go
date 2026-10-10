package agentruntime_test

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestPolicyPreparationDiagnosticReachesOpsLiveAndAfterRestart(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	if err := store.Create(ctx, &tasks.Task{ID: "policy-task"}); err != nil {
		t.Fatal(err)
	}
	bus := agentruntime.New()
	defer bus.Close()
	service.ObserveEvents(func(ctx context.Context, id string, event tasks.TaskEvent) {
		if projected, ok := agentruntime.ProjectTaskEvent(id, event); ok {
			bus.Publish(ctx, projected)
		}
	})
	sub, err := bus.Subscribe("preparation-test", []string{agentcontract.TopicActionAll}, 8)
	if err != nil {
		t.Fatal(err)
	}
	updated, err := service.AppendEvent(ctx, "policy-task", tasks.TaskEvent{Type: "POLICY_PREPARATION_FAILED", StepID: "rev-1-pick", Payload: map[string]any{
		"tool": "manipulation.pick", "stepId": "rev-1-pick", "phase": "pre_dispatch", "physicalDispatched": false,
		"code": "POLICY_PROVIDER_TIMEOUT",
	}})
	if err != nil {
		t.Fatal(err)
	}
	event, err := sub.Receive(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if event.Topic != agentcontract.TopicActionPreparationFailed || event.Topic == agentcontract.TopicActionExecuted {
		t.Fatalf("preparation was reported as dispatch: %#v", event)
	}
	observer := agentruntime.NewOpsAgent()
	if err := observer.OnEvent(ctx, event); err != nil {
		t.Fatal(err)
	}
	assertPolicyPreparationFinding(t, observer.Observe(ctx))

	projected, ok := agentruntime.TaskEventFromEvent(event)
	if !ok {
		t.Fatal("projected event cannot be archived")
	}
	if _, err := service.AppendEvent(ctx, "policy-task", projected); err != nil {
		t.Fatal(err)
	}
	bounded, stop := context.WithTimeout(ctx, 10*time.Millisecond)
	defer stop()
	if _, err := sub.Receive(bounded); err == nil {
		t.Fatal("archiving projected event created a bus loop")
	}
	restarted := agentruntime.NewOpsAgent()
	restarted.History = staticHistory{tasks: []string{"policy-task"}, events: map[string][]agentcontract.TaskEvent{
		"policy-task": {{Type: updated.Events[0].Type, Payload: updated.Events[0].Payload}, {Type: projected.Type, Payload: projected.Payload}},
	}}
	assertPolicyPreparationFinding(t, restarted.Observe(ctx))
}

func assertPolicyPreparationFinding(t *testing.T, findings []agentruntime.Finding) {
	t.Helper()
	count := 0
	for _, finding := range findings {
		if finding.Code != agentruntime.AnomalyActionFailed {
			continue
		}
		count++
		if finding.TaskID != "policy-task" || finding.Component != "policy.prepare" || finding.Facts["stepId"] != "rev-1-pick" ||
			finding.Facts["capability"] != "manipulation.pick" || finding.Facts["phase"] != "pre_dispatch" || finding.Facts["physicalDispatched"] != false ||
			finding.Facts["occurrences"] != 1 || finding.Category != string(closedloop.Transient) || finding.AutomaticRetryForbidden {
			t.Fatalf("incorrect preparation finding: %#v", finding)
		}
	}
	if count != 1 {
		t.Fatalf("expected one preparation finding, got %d: %#v", count, findings)
	}
}

func TestPreparationTopicCannotDowngradeAnUnknownPhysicalOutcome(t *testing.T) {
	observer := agentruntime.NewOpsAgent()
	ctx := context.Background()
	if err := observer.OnEvent(ctx, agentcontract.Event{TaskID: "task-1", Topic: agentcontract.TopicActionPreparationFailed,
		Payload: map[string]any{"phase": "pre_dispatch", "physicalDispatched": true, "code": "POLICY_PROVIDER_TIMEOUT"}}); err != nil {
		t.Fatal(err)
	}
	if err := observer.OnEvent(ctx, agentcontract.Event{TaskID: "task-1", Topic: agentcontract.TopicActionExecuted,
		Payload: map[string]any{"toolName": "manipulation.pick", "stepId": "pick", "activityStatus": "FAILED", "code": "RPC_UNAVAILABLE", "mutatesWorld": true, "outcomeUnknown": true}}); err != nil {
		t.Fatal(err)
	}
	count := 0
	for _, finding := range observer.Observe(ctx) {
		if finding.Code != agentruntime.AnomalyActionFailed {
			continue
		}
		count++
		if finding.Component != "manipulation.pick" || !finding.AutomaticRetryForbidden || finding.Category != string(closedloop.UnknownOutcome) {
			t.Fatalf("unknown physical outcome weakened: %#v", finding)
		}
	}
	if count != 1 {
		t.Fatalf("unexpected preparation accepted: %d findings", count)
	}
}
