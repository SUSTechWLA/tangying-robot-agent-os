package worker

import (
	"context"
	"errors"
	"slices"
	"sync"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type activityCloud struct {
	mu                  sync.Mutex
	task                *tasks.Task
	node                *coordinator.IntentNode
	events              []tasks.TaskEvent
	completeSawAwaiting bool
	failed              bool
}

func (c *activityCloud) GetTask(context.Context, string) (*tasks.Task, error) { return c.task, nil }
func (c *activityCloud) NextIntent(context.Context, string, string) (*coordinator.IntentNode, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	node := c.node
	c.node = nil
	return node, nil
}
func (c *activityCloud) CompleteIntentRevision(_ context.Context, _ string, _ *coordinator.IntentNode, _ string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, event := range c.events {
		if event.Type == "TOOL_ACTIVITY" && event.Payload["activityStatus"] == "AWAITING_EVIDENCE" {
			c.completeSawAwaiting = true
		}
	}
	return nil
}
func (c *activityCloud) FailIntentRevision(context.Context, string, *coordinator.IntentNode, string, string) error {
	c.failed = true
	return nil
}
func (c *activityCloud) AppendEvent(_ context.Context, _ string, eventType, stepID, message string, payload map[string]any) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.events = append(c.events, tasks.TaskEvent{Type: eventType, StepID: stepID, Message: message, Payload: payload})
	return nil
}
func (c *activityCloud) ReportTelemetry(context.Context, fleettelemetry.Sample) error { return nil }

type activityRuntime struct {
	invokes     *int
	groundCalls *int
	skills      *[]string
}

func (r activityRuntime) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	if r.groundCalls != nil {
		(*r.groundCalls)++
	}
	return manipulation.GroundedTask{
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 1},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 1},
	}, nil
}
func (r activityRuntime) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	if r.invokes != nil {
		*r.invokes++
	}
	if r.skills != nil {
		*r.skills = append(*r.skills, string(command.Capability))
	}
	return runtime.Result{Success: true, ObservationID: "observation/" + command.CommandID, VerificationConfidence: 1}, nil
}

func TestWorkerExecutesResetPreludeWithoutGrounding(t *testing.T) {
	groundCalls := 0
	var invoked []string
	cloud := &activityCloud{
		task: &tasks.Task{ID: "task-prepare", Adapter: "mujoco", Approved: true,
			CurrentRevision: 1, AggregateVersion: 1,
			Intent: manipulation.Intent{Action: manipulation.ActionPrepareSimulation, RobotID: "robot-1"}},
		node: &coordinator.IntentNode{Index: 0, StepID: "intent-000/prepare", TaskRevision: 1,
			AggregateVersion: 1, CommandID: "task-prepare/revision/1/step/intent-000/prepare", RobotID: "robot-1"},
	}
	worker := New(Config{RobotID: "robot-1", Adapter: "mujoco", Cloud: cloud,
		Runtime: activityRuntime{groundCalls: &groundCalls, skills: &invoked}})
	if err := worker.processTask(context.Background(), "task-prepare"); err != nil {
		t.Fatal(err)
	}
	want := []string{"simulation.reset_episode", "observe_scene", "verify_episode_ready"}
	if groundCalls != 0 || !slices.Equal(invoked, want) {
		t.Fatalf("groundCalls=%d invoked=%v", groundCalls, invoked)
	}
}

func TestWorkerRefusesClaimFromSupersededTaskRevision(t *testing.T) {
	parsed, _ := intent.NewDeterministicParser().Parse("让1号机器人把红色杯子放进右侧收纳盒")
	cloud := &activityCloud{
		task: &tasks.Task{ID: "task-stale", Adapter: "mujoco", Intent: parsed, CurrentRevision: 3, AggregateVersion: 8},
		node: &coordinator.IntentNode{Index: 0, StepID: "intent-000/old", TaskRevision: 2,
			AggregateVersion: 7, CommandID: "old-command", RobotID: "robot-1"},
	}
	invokes := 0
	worker := New(Config{RobotID: "robot-1", Cloud: cloud, Runtime: activityRuntime{invokes: &invokes}})
	err := worker.processTask(context.Background(), "task-stale")
	if !errors.Is(err, coordinator.ErrStaleTaskRevision) || invokes != 0 || cloud.failed {
		t.Fatalf("err=%v invokes=%d failed=%v", err, invokes, cloud.failed)
	}
}
func (activityRuntime) Info(context.Context) (runtime.Snapshot, error) {
	return runtime.Snapshot{RobotID: "robot-1", Adapter: "mujoco", Ready: true}, nil
}
func (activityRuntime) Cancel(context.Context, string, string) (bool, error) { return true, nil }
func (activityRuntime) EmergencyStop(context.Context, string) error          { return nil }
func (activityRuntime) Telemetry(context.Context, string) (telemetry.Snapshot, error) {
	return telemetry.Snapshot{}, nil
}

func TestWorkerReportsStructuredToolActivityBeforeHarnessCompletion(t *testing.T) {
	parsed, err := intent.NewDeterministicParser().Parse("让1号机器人把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	cloud := &activityCloud{
		task: &tasks.Task{ID: "task-1", Adapter: "mujoco", Intent: parsed, CurrentRevision: 2,
			AggregateVersion: 7},
		node: &coordinator.IntentNode{Index: 0, StepID: "intent-000/pick-place", TaskRevision: 2,
			AggregateVersion: 7, CommandID: "task-1/revision/2/step/intent-000/pick-place", RobotID: "robot-1"},
	}
	worker := New(Config{RobotID: "robot-1", Cloud: cloud, Runtime: activityRuntime{}})
	if err := worker.processTask(context.Background(), "task-1"); err != nil {
		t.Fatal(err)
	}
	if cloud.failed || !cloud.completeSawAwaiting {
		t.Fatalf("failed=%v completeSawAwaiting=%v", cloud.failed, cloud.completeSawAwaiting)
	}
	statuses := map[string]bool{}
	for _, event := range cloud.events {
		if event.Type != "TOOL_ACTIVITY" {
			continue
		}
		status, _ := event.Payload["activityStatus"].(string)
		statuses[status] = true
		if event.Payload["taskRevision"] != uint64(2) || event.Payload["stepId"] != "intent-000/pick-place" {
			t.Fatalf("activity lost revision identity: %#v", event)
		}
	}
	for _, required := range []string{"SENDING", "RUNNING", "AWAITING_EVIDENCE", "CONFIRMED"} {
		if !statuses[required] {
			t.Fatalf("missing %s in %#v", required, statuses)
		}
	}
}
