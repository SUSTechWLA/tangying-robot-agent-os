package agent

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Exercise the real two publisher paths used by local execution, including
// Service.AppendEvent's durable identity and the production ledger projection.
// No invoker, robot client or service connection is installed.
func TestRunnerActionAndDurableMirrorPreserveExactCommandArguments(t *testing.T) {
	for _, tc := range []struct {
		name      string
		arguments map[string]any
		fence     uint64
	}{
		{"nil arguments and zero fence", nil, 0},
		{"empty arguments and zero fence", map[string]any{}, 0},
		{"grounded navigation pose", map[string]any{"goalPose": []float64{1, 2, 0, 1, 0, 0, 0}}, 0},
		{"nested exact command", map[string]any{"goalPose": []float64{1, 2, 0, 1, 0, 0, 0}, "binding": map[string]any{"sequence": uint64(9007199254740993)}}, 9007199254740993},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
			task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
			if err != nil {
				t.Fatal(err)
			}
			bus := agentruntime.New()
			service.ObserveEvents(func(ctx context.Context, taskID string, event tasks.TaskEvent) {
				projected, ok := agentruntime.ProjectTaskEvent(taskID, event)
				if !ok {
					t.Fatal("real tool event was not projectable")
				}
				bus.Publish(ctx, projected)
			})
			var direct agentcontract.Event
			runner := &Runner{Events: func(ctx context.Context, event agentcontract.Event) {
				direct = event
				bus.Publish(ctx, event)
			}, TaskEvents: func(ctx context.Context, id string, event tasks.TaskEvent) error {
				_, err := service.AppendEvent(ctx, id, event)
				return err
			}}
			command := runtime.Command{CommandID: "original-command", RobotID: "gazebo-home_furnished", StepID: "navigation",
				Capability: runtime.CapabilityNavigate, TaskRevision: 1, AggregateVersion: 1,
				FencingToken: tc.fence, Parameters: tc.arguments}
			runner.publishToolActivity(ctx, task, command, "SENDING", nil, "")
			if bus.Published() != 1 || bus.Rejected() != 0 {
				t.Fatalf("same command emitted a conflict: published=%d rejected=%d", bus.Published(), bus.Rejected())
			}
			loaded, err := service.Get(ctx, task.ID)
			if err != nil {
				t.Fatal(err)
			}
			mirror := loaded.Events[len(loaded.Events)-1]
			if mirror.Payload["eventId"] != direct.ID || mirror.Type != "TOOL_ACTIVITY" {
				t.Fatal("two paths lost their common event identity")
			}
			want, _ := json.Marshal(command.Parameters)
			got, _ := json.Marshal(direct.Payload["arguments"])
			if string(got) != string(want) || direct.Payload["fencingToken"] != tc.fence {
				t.Fatalf("direct source altered actual command: %s want %s fence=%v", got, want, direct.Payload["fencingToken"])
			}
		})
	}
}

func TestRunnerMirrorStillRejectsChangedCommandParametersAndFence(t *testing.T) {
	for _, field := range []string{"arguments", "fencingToken"} {
		t.Run(field, func(t *testing.T) {
			ctx := context.Background()
			bus := agentruntime.New()
			var rejectedID string
			runner := &Runner{Events: bus.Publish, TaskEvents: func(ctx context.Context, taskID string, event tasks.TaskEvent) error {
				if field == "arguments" {
					event.Payload[field] = map[string]any{"goalPose": []float64{99, 2, 0, 1, 0, 0, 0}}
				} else {
					event.Payload[field] = uint64(1)
				}
				projected, ok := agentruntime.ProjectTaskEvent(taskID, event)
				if !ok {
					t.Fatal("conflict fixture lost event scope")
				}
				rejectedID = projected.ID
				bus.Publish(ctx, projected)
				return nil
			}}
			subscription, err := bus.Subscribe("audit", []string{agentcontract.TopicAgentPermissionDenied}, 8)
			if err != nil {
				t.Fatal(err)
			}
			defer subscription.Close()
			runner.publishToolActivity(ctx, &tasks.Task{ID: "task"}, runtime.Command{CommandID: "original-command", RobotID: "robot", StepID: "navigation",
				Capability: runtime.CapabilityNavigate, TaskRevision: 1, AggregateVersion: 1,
				Parameters: map[string]any{"goalPose": []float64{1, 2, 0, 1, 0, 0, 0}}}, "SENDING", nil, "")
			if bus.Published() != 2 || bus.Rejected() != 1 {
				t.Fatalf("changed command accepted: published=%d rejected=%d", bus.Published(), bus.Rejected())
			}
			denied, err := subscription.Receive(ctx)
			if err != nil || denied.Payload["reasonCode"] != "EVENT_ID_CONFLICT" || denied.CausationID != rejectedID {
				t.Fatalf("conflict lost reason or cause: %+v %v", denied, err)
			}
		})
	}
}
