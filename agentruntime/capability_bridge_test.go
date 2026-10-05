package agentruntime_test

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestCommittedCapabilityFailureReachesBusOnce(t *testing.T) {
	ctx := context.Background()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	if err := store.Create(ctx, &tasks.Task{ID: "capability"}); err != nil {
		t.Fatal(err)
	}
	bus := agentruntime.New()
	defer bus.Close()
	service.ObserveEvents(func(ctx context.Context, id string, event tasks.TaskEvent) {
		if projected, ok := agentruntime.ProjectTaskEvent(id, event); ok {
			bus.Publish(ctx, projected)
		}
	})
	sub, err := bus.Subscribe("test", []string{agentcontract.TopicActionExecuted}, 8)
	if err != nil {
		t.Fatal(err)
	}
	payload := map[string]any{"eventId": "capability/dispatch-1", "toolName": "calibration.get", "stepId": "rev-1-cap-01", "activityStatus": "FAILED", "code": "PROVIDER_UNAVAILABLE"}
	// The legacy direct route is already published when its ledger append runs.
	bus.Publish(ctx, agentcontract.Event{ID: "capability/dispatch-1", TaskID: "capability", Topic: agentcontract.TopicActionExecuted, Payload: payload})
	if _, err := service.AppendEvent(ctx, "capability", tasks.TaskEvent{Type: "TOOL_ACTIVITY", StepID: "rev-1-cap-01", Payload: payload}); err != nil {
		t.Fatal(err)
	}
	event, err := sub.Receive(ctx)
	if err != nil || event.Payload["code"] != "PROVIDER_UNAVAILABLE" {
		t.Fatalf("missing activity: %#v %v", event, err)
	}
	bounded, cancel := context.WithTimeout(ctx, 10*time.Millisecond)
	defer cancel()
	if _, err := sub.Receive(bounded); err == nil {
		t.Fatal("legacy direct + ledger produced duplicate action")
	}
	// A capability executor has no direct publisher; its ledger path must work.
	delete(payload, "eventId")
	if _, err := service.AppendEvent(ctx, "capability", tasks.TaskEvent{Type: "TOOL_ACTIVITY", StepID: "rev-1-cap-02", Payload: payload}); err != nil {
		t.Fatal(err)
	}
	bounded2, cancel2 := context.WithTimeout(ctx, time.Second)
	defer cancel2()
	if _, err := sub.Receive(bounded2); err != nil {
		t.Fatalf("capability activity never reached bus: %v", err)
	}
}

func TestFailedActionsKeepTheirTaskScopeForIdenticalStepIDs(t *testing.T) {
	observer := agentruntime.NewOpsAgent()
	for _, id := range []string{"task-a", "task-b"} {
		err := observer.OnEvent(context.Background(), agentcontract.Event{
			TaskID: id, Topic: agentcontract.TopicActionExecuted,
			Payload: map[string]any{"stepId": "rev-1-cap-01", "toolName": "calibration.get", "activityStatus": "FAILED", "code": "PROVIDER_UNAVAILABLE", "mutatesWorld": false},
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	got := map[string]int{}
	for _, finding := range observer.Observe(context.Background()) {
		if finding.Code != agentruntime.AnomalyActionFailed {
			continue
		}
		got[finding.TaskID]++
		if finding.Facts["occurrences"] != 1 || finding.AutomaticRetryForbidden || finding.Category != string(closedloop.Transient) || finding.Facts["failureClass"] != string(closedloop.Transient) {
			t.Fatalf("read error misclassified or merged: %#v", finding)
		}
	}
	if got["task-a"] != 1 || got["task-b"] != 1 || len(got) != 2 {
		t.Fatalf("task attribution lost: %#v", got)
	}
}

func TestReadFailureClassificationDoesNotWeakenPhysicalUnknownOutcomes(t *testing.T) {
	for _, tc := range []struct {
		name       string
		code       string
		readOnly   bool
		unknown    bool
		class      string
		forbidAuto bool
	}{
		{"read unavailable", "PROVIDER_UNAVAILABLE", true, false, string(closedloop.Transient), false},
		{"read deadline", "PROVIDER_DEADLINE_EXCEEDED", true, false, string(closedloop.Transient), false},
		{"normalized read transport", " provider_unavailable ", true, false, string(closedloop.Transient), false},
		{"other read transport", "RPC_UNAVAILABLE", true, false, string(closedloop.Transient), false},
		{"unrecognized read", "NEW_READ_FAILURE", true, false, "READ_ONLY_FAILURE", false},
		{"missing read code", "", true, false, "READ_ONLY_FAILURE", false},
		{"known read permission", "APPROVAL_REQUIRED", true, false, string(closedloop.Permission), false},
		{"write unavailable", "PROVIDER_UNAVAILABLE", false, true, string(closedloop.UnknownOutcome), true},
		{"write deadline", "PROVIDER_DEADLINE_EXCEEDED", false, true, string(closedloop.UnknownOutcome), true},
		{"write rpc outcome unknown", "RPC_UNAVAILABLE", false, true, string(closedloop.UnknownOutcome), true},
		{"missing write code", "", false, false, string(closedloop.UnknownOutcome), true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			observer := agentruntime.NewOpsAgent()
			if err := observer.OnEvent(context.Background(), agentcontract.Event{
				TaskID: "task-classification", Topic: agentcontract.TopicActionExecuted,
				Payload: map[string]any{
					"stepId": "rev-1-cap-01", "toolName": "runtime.service", "activityStatus": "FAILED",
					"code": tc.code, "mutatesWorld": !tc.readOnly, "outcomeUnknown": tc.unknown,
				},
			}); err != nil {
				t.Fatal(err)
			}
			found := false
			for _, finding := range observer.Observe(context.Background()) {
				if finding.Code != agentruntime.AnomalyActionFailed {
					continue
				}
				found = true
				if finding.Category != tc.class || finding.Facts["failureClass"] != tc.class || finding.AutomaticRetryForbidden != tc.forbidAuto {
					t.Fatalf("wrong class or retry authority: %#v", finding)
				}
				if tc.readOnly {
					if strings.Contains(finding.Message, "UNKNOWN_OUTCOME") || strings.Contains(finding.Message, "结果未知") {
						t.Fatalf("read failure presented as unknown physical outcome: %s", finding.Message)
					}
					for _, missing := range finding.MissingEvidence {
						if strings.Contains(missing, "硬件") {
							t.Fatalf("read failure asked to reconcile hardware effects: %s", missing)
						}
					}
				} else if tc.forbidAuto && finding.Severity != agentruntime.SeverityCritical {
					t.Fatalf("physical unknown lost its critical severity: %#v", finding)
				}
			}
			if !found {
				t.Fatal("failure did not reach the observer")
			}
		})
	}
	// These provider codes intentionally remain unknown outside the proven-read
	// observer branch: changing the global mapping would authorize unsafe writes.
	for _, code := range []string{"PROVIDER_UNAVAILABLE", "PROVIDER_DEADLINE_EXCEEDED"} {
		if closedloop.Classify(code) != closedloop.UnknownOutcome {
			t.Fatalf("global physical failure classification changed: %s", code)
		}
	}
}

func TestRestartFailureSweepDeduplicatesTheTwoDurableRoutes(t *testing.T) {
	observer := agentruntime.NewOpsAgent()
	payload := map[string]any{"eventId": "task-a/action/1", "stepId": "rev-1-cap-01", "toolName": "calibration.get", "activityStatus": "FAILED", "code": "PROVIDER_UNAVAILABLE", "mutatesWorld": false}
	observer.History = staticHistory{tasks: []string{"task-a", "task-b"}, events: map[string][]agentcontract.TaskEvent{
		"task-a": {{Type: "TOOL_ACTIVITY", Payload: payload}, {Type: "action.executed", Payload: payload}},
	}}
	failures := 0
	for _, finding := range observer.Observe(context.Background()) {
		if finding.Code != agentruntime.AnomalyActionFailed {
			continue
		}
		failures++
		if finding.TaskID != "task-a" || finding.Facts["occurrences"] != 1 {
			t.Fatalf("restart lost dispatch identity: %#v", finding)
		}
	}
	if failures != 1 {
		t.Fatalf("found %d failures, want one", failures)
	}
}

func TestTwoReadStepsOnSameToolHaveIndependentFreshInvestigations(t *testing.T) {
	ctx := context.Background()
	observer := agentruntime.NewOpsAgent()
	recovery := agentruntime.NewRecoveryAgent(nil)
	now := time.Unix(2000, 0).UTC()
	observer.Now = func() time.Time { return now }
	recovery.Now = func() time.Time { return now }
	var plans []*agentcontract.RecoveryPlanPayload
	observer.Publish = func(ctx context.Context, event agentcontract.Event) {
		if event.Topic != agentcontract.TopicOpsAnomalyDetected || event.Payload["code"] != agentruntime.AnomalyActionFailed {
			return
		}
		facts, _ := event.Payload["facts"].(map[string]any)
		finding := agentruntime.Finding{TaskID: event.TaskID, Code: agentruntime.AnomalyActionFailed, Component: "calibration.get", Facts: facts}
		if plan := recovery.Recover(ctx, finding); plan != nil {
			plans = append(plans, plan)
		}
	}
	for _, step := range []string{"rev-1-cap-01", "rev-1-cap-04"} {
		if err := observer.OnEvent(ctx, agentcontract.Event{TaskID: "long-goal", Topic: agentcontract.TopicActionExecuted,
			Payload: map[string]any{"stepId": step, "toolName": "calibration.get", "activityStatus": "FAILED", "code": "PROVIDER_UNAVAILABLE", "mutatesWorld": false},
		}); err != nil {
			t.Fatal(err)
		}
		observer.Observe(ctx)
		now = now.Add(time.Second)
	}
	if len(plans) != 2 {
		t.Fatalf("same-tool cooldown suppressed a different step: %d plans", len(plans))
	}
	if plans[0].PlanID == plans[1].PlanID || plans[0].Trigger != plans[1].Trigger {
		t.Fatalf("plan identities not separate or public trigger changed: %#v %#v", plans[0], plans[1])
	}
	for i, step := range []string{"rev-1-cap-01", "rev-1-cap-04"} {
		steps, _ := plans[i].Trail["steps"].([]any)
		if len(steps) == 0 {
			t.Fatalf("missing trace: %#v", plans[i].Trail)
		}
		first, _ := steps[0].(map[string]any)
		findings, _ := first["findings"].(map[string]any)
		if findings["stepId"] != step {
			t.Fatalf("investigation not bound to step: %#v", first)
		}
	}
}
