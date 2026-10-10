package main

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/autorecovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestRecoveryProtocolCarriesActualFailureThroughAllAgentsAndDurableReplay(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	if err := store.Create(ctx, &tasks.Task{ID: "task-recovery", CurrentRevision: 2}); err != nil {
		t.Fatal(err)
	}
	bus := agentruntime.New()
	defer bus.Close()
	filter := agentruntime.NewLedgerFilter(0)
	bus.SetEventSink(func(ctx context.Context, event agentcontract.Event) {
		if !filter.Admit(event) {
			return
		}
		if durable, ok := agentruntime.TaskEventFromEvent(event); ok {
			if _, err := service.AppendEvent(ctx, event.TaskID, durable); err != nil {
				t.Fatal(err)
			}
		}
	})
	sub, err := bus.Subscribe("recovery-trigger", []string{agentcontract.TopicOpsAnomalyDetected}, 8)
	if err != nil {
		t.Fatal(err)
	}
	prefix := recoveryGuardPrefix("current-step", "navigation.status")
	for _, event := range prefix[:2] {
		if _, err := service.AppendEvent(ctx, "task-recovery", event); err != nil {
			t.Fatal(err)
		}
	}
	projected, ok := agentruntime.ProjectTaskEvent("task-recovery", prefix[1])
	if !ok {
		t.Fatal("source failure was not projected")
	}
	projected.Payload["code"] = "PROVIDER_UNAVAILABLE"
	ops := agentruntime.NewOpsAgent()
	ops.SetPublish(bus.Publish)
	if err := ops.OnEvent(ctx, projected); err != nil {
		t.Fatal(err)
	}
	ops.Observe(ctx)
	anomaly, err := sub.Receive(ctx)
	if err != nil {
		t.Fatal(err)
	}
	finding, ok := findingFromEvent(anomaly)
	if !ok || !finding.Binding.Complete() {
		t.Fatalf("failure lost binding: %+v", finding)
	}
	recovery := agentruntime.NewRecoveryAgent(nil)
	recovery.SetPublish(bus.Publish)
	plan := recovery.Recover(ctx, finding)
	if plan == nil || plan.Binding.SourceEventID != prefix[1].Payload["eventId"] {
		t.Fatalf("plan lost failed source: %+v", plan)
	}
	executor := &protocolRecordingExecutor{record: recoveryExecutionRecorder(bus)}
	supervisor := autorecovery.Supervisor{Catalog: agentruntime.DefaultRecoveryCatalog(), Executor: executor}
	if _, err := supervisor.Run(ctx, plan); err != nil {
		t.Fatal(err)
	}
	if executor.binding != plan.Binding {
		t.Fatalf("executor received another execution: %+v", executor.binding)
	}
	snapshot, err := service.Get(ctx, "task-recovery")
	if err != nil {
		t.Fatal(err)
	}
	ready, err := readRecoveryCompleted(snapshot.Events, snapshot.ID, "current-step", "navigation.status")
	if err != nil || !ready {
		t.Fatalf("real publishers failed durable matching: %v, %v; events=%+v", ready, err, snapshot.Events)
	}
}

// This fake models the existing verifier boundary, not a robot. The integration
// assertion is that Supervisor passes the source binding to the real recorder.
type protocolRecordingExecutor struct {
	binding agentcontract.RecoveryBinding
	record  func(context.Context, recoveryexec.Request, recoveryexec.Result, error)
}

func (e *protocolRecordingExecutor) Execute(ctx context.Context, request recoveryexec.Request) (recoveryexec.Result, error) {
	e.binding = request.Binding
	result := recoveryexec.Result{ActionID: request.Action.ID, Executed: true, Verified: true}
	e.record(ctx, request, result, nil)
	return result, nil
}

func TestRecoveryProtocolRejectsForeignOrUnknownIdentityAndBrokenCausation(t *testing.T) {
	for _, field := range []string{"taskId", "taskRevision", "robotId", "stepId", "commandId", "sourceEventId", "anomalyEventId", "planEventId"} {
		for _, target := range []string{"plan", "execution"} {
			t.Run(target+"/"+field, func(t *testing.T) {
				plan := recoveryGuardPlan("current-step", "navigation.status", "p1", "PLAN")
				done := recoveryGuardExecution("p1")
				selected := &plan
				if target == "execution" {
					selected = &done
				}
				binding := selected.Payload["binding"].(map[string]any)
				if field == "taskRevision" {
					binding[field] = uint64(1)
				} else {
					binding[field] = "foreign"
				}
				events := append(recoveryGuardPrefix("current-step", "navigation.status"), plan, done)
				if ready, _ := readRecoveryCompleted(events, "task-recovery", "current-step", "navigation.status"); ready {
					t.Fatal("foreign execution authorized retry")
				}
				delete(binding, field)
				if ready, _ := readRecoveryCompleted(events, "task-recovery", "current-step", "navigation.status"); ready {
					t.Fatal("unknown identity authorized retry")
				}
			})
		}
	}
	for _, index := range []int{2, 3, 4} {
		events := append(recoveryGuardPrefix("current-step", "navigation.status"), recoveryGuardPlan("current-step", "navigation.status", "p1", "PLAN"), recoveryGuardExecution("p1"))
		events[index].Payload["causationId"] = "old-event"
		if ready, _ := readRecoveryCompleted(events, "task-recovery", "current-step", "navigation.status"); ready {
			t.Fatalf("broken causation at %d authorized retry", index)
		}
	}
}
