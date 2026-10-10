package agentruntime_test

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

func TestRegisteredPublisherCannotImpersonateExecutionOrVerification(t *testing.T) {
	agent := newPublishingAgent("observer")
	_, runtime := startOrchestrator(t, agent)
	sub, err := runtime.Subscribe("audit", []string{agentcontract.TopicAgentPermissionDenied, agentcontract.TopicOpsAnomalyDetected, agentcontract.TopicActionExecuted, agentcontract.TopicOpsRecoveryExecuted}, 16)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	for _, event := range []agentcontract.Event{
		{Topic: agentcontract.TopicOpsAnomalyDetected, Agent: "task"},
		{Topic: agentcontract.TopicActionExecuted},
		{Topic: agentcontract.TopicOpsRecoveryExecuted},
	} {
		agent.emit(ctx, event)
		got, err := sub.Receive(ctx)
		if err != nil || got.Topic != agentcontract.TopicAgentPermissionDenied || got.Payload["publisher"] != "observer" {
			t.Fatalf("unowned publication accepted: %+v, %v", got, err)
		}
	}
	agent.emit(ctx, agentcontract.Event{Topic: agentcontract.TopicOpsAnomalyDetected})
	got, err := sub.Receive(ctx)
	if err != nil || got.Agent != agent.Name() || got.AgentVersion != agent.Version() || got.ProtocolVersion != agentcontract.EventProtocolV1 {
		t.Fatalf("registered ownership missing: %+v, %v", got, err)
	}
}

func TestEventIDsSurviveRestartAndDurableEnvelopeKeepsCausation(t *testing.T) {
	ids := map[string]bool{}
	for range 2 {
		runtime := agentruntime.New()
		runtime.SetEventSink(func(_ context.Context, event agentcontract.Event) {
			if ids[event.ID] || event.ID == "" {
				t.Fatalf("ID repeated across runtime restart: %q", event.ID)
			}
			ids[event.ID] = true
			durable, ok := agentruntime.TaskEventFromEvent(event)
			if !ok || durable.Payload["eventId"] != event.ID || durable.Payload["protocolVersion"] != agentcontract.EventProtocolV1 || durable.Payload["taskRevision"] != uint64(9) || durable.Payload["causationId"] != "source" || durable.Payload["robotId"] != "robot" || durable.Payload["commandId"] != "command" {
				t.Fatalf("durable envelope lost identity: %+v", durable)
			}
		})
		runtime.Publish(context.Background(), agentcontract.Event{Topic: agentcontract.TopicOpsRecoveryPlan, TaskID: "task", TaskRevision: 9, RobotID: "robot", StepID: "step", CommandID: "command", CausationID: "source"})
		runtime.Close()
	}
}

func TestBusRejectsConflictingEventIdentityAndInconsistentScope(t *testing.T) {
	runtime := agentruntime.New()
	defer runtime.Close()
	var persisted []agentcontract.Event
	runtime.SetEventSink(func(_ context.Context, event agentcontract.Event) { persisted = append(persisted, event) })
	event := agentcontract.Event{ID: "same-id", Topic: agentcontract.TopicActionExecuted, TaskID: "task", StepID: "s1", Payload: map[string]any{"eventId": "same-id", "stepId": "s1", "commandId": "c1", "activityStatus": "RUNNING"}}
	runtime.Publish(context.Background(), event)
	runtime.Publish(context.Background(), event)
	event.Payload = map[string]any{"eventId": "same-id", "stepId": "s1", "commandId": "c1", "activityStatus": "CONFIRMED"}
	runtime.Publish(context.Background(), event)
	if len(persisted) != 2 || persisted[1].Payload["reasonCode"] != "EVENT_ID_CONFLICT" {
		t.Fatalf("conflict accepted or silently lost: %+v", persisted)
	}
	event.ID = "fresh-id"
	runtime.Publish(context.Background(), event) // Envelope and body now disagree.
	if runtime.Rejected() != 2 || len(persisted) != 2 {
		t.Fatalf("inconsistent envelope accepted: rejected=%d persisted=%d", runtime.Rejected(), len(persisted))
	}
}

func TestBusThreeMirrorsCannotHideConflictingSupplementalFields(t *testing.T) {
	for _, field := range []string{"safetyLevel", "receiptObservationId", "evidenceSource"} {
		t.Run(field, func(t *testing.T) {
			runtime := agentruntime.New()
			defer runtime.Close()
			var persisted []agentcontract.Event
			runtime.SetEventSink(func(_ context.Context, event agentcontract.Event) {
				persisted = append(persisted, event)
			})
			event := agentcontract.Event{ID: "mirror-id", Topic: agentcontract.TopicActionExecuted, TaskID: "task", Payload: map[string]any{"activityStatus": "CONFIRMED"}}
			runtime.Publish(context.Background(), event)
			event.Payload = map[string]any{"activityStatus": "CONFIRMED", field: "first-declaration"}
			runtime.Publish(context.Background(), event)
			if len(persisted) != 1 {
				t.Fatalf("compatible mirror was not deduplicated: %+v", persisted)
			}
			if _, mutated := persisted[0].Payload[field]; mutated {
				t.Fatal("dedup normalization changed the original authoritative payload")
			}
			event.Payload = map[string]any{"activityStatus": "CONFIRMED", field: "conflicting-declaration"}
			runtime.Publish(context.Background(), event)
			if len(persisted) != 2 || persisted[1].Payload["reasonCode"] != "EVENT_ID_CONFLICT" {
				t.Fatalf("third mirror silently contradicted second: %+v", persisted)
			}
		})
	}
}

func TestOpsKeepsSeparateRobotsRevisionsAndCommandsOnSameStep(t *testing.T) {
	ops := agentruntime.NewOpsAgent()
	for i, binding := range []agentcontract.RecoveryBinding{
		{TaskID: "task", TaskRevision: 1, RobotID: "a", StepID: "step", CommandID: "one"},
		{TaskID: "task", TaskRevision: 2, RobotID: "a", StepID: "step", CommandID: "one"},
		{TaskID: "task", TaskRevision: 1, RobotID: "b", StepID: "step", CommandID: "one"},
		{TaskID: "task", TaskRevision: 1, RobotID: "a", StepID: "step", CommandID: "two"},
	} {
		payload := binding.Encode()
		payload["eventId"], payload["toolName"], payload["activityStatus"], payload["mutatesWorld"] = string(rune('a'+i)), "navigation.status", "FAILED", false
		if err := ops.OnEvent(context.Background(), agentcontract.Event{TaskID: "task", Topic: agentcontract.TopicActionExecuted, Payload: payload}); err != nil {
			t.Fatal(err)
		}
	}
	count := 0
	for _, finding := range ops.Observe(context.Background()) {
		if finding.Code == agentruntime.AnomalyActionFailed {
			count++
			if !finding.Binding.Complete() || finding.Facts["occurrences"] != 1 {
				t.Fatalf("failure sources merged: %+v", finding)
			}
		}
	}
	if count != 4 {
		t.Fatalf("got %d scoped failures, want 4", count)
	}
}
