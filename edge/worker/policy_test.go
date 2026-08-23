package worker

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type recordingPolicy struct {
	mu       sync.Mutex
	manifest policy.Manifest
	requests []policy.InferenceRequest
	failures []error
}

func (provider *recordingPolicy) Manifest(context.Context) (policy.Manifest, error) {
	return provider.manifest, nil
}

func (provider *recordingPolicy) Infer(_ context.Context, request policy.InferenceRequest) (policy.Decision, error) {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	provider.requests = append(provider.requests, request)
	if len(provider.failures) > 0 {
		err := provider.failures[0]
		provider.failures = provider.failures[1:]
		return policy.Decision{}, err
	}
	return policy.Decision{
		InferenceID: "policy/" + request.CommandID, ManifestRevision: request.ManifestRevision,
		ObservationID: request.Observation.ObservationID,
		Actions:       []map[string]float64{{"left_arm_gripper.pos": 50}},
	}, nil
}

func TestWorkerRetriesPolicyTimeoutBeforeMotionAndEmitsRecovery(t *testing.T) {
	_, _, cloud := policyTask(t)
	provider := &recordingPolicy{manifest: policyTestManifest(), failures: []error{policy.ErrProviderTimeout}}
	runtimeClient := &learnedRuntime{}
	worker := New(Config{
		RobotID: "robot-1", Adapter: "mujoco", RobotModel: "xlerobot-sim",
		TransformRevision: "mujoco-world-v1", Cloud: cloud, Runtime: runtimeClient, Policy: provider,
		PolicyMaxAttempts: 2, PolicyRetryDelay: time.Microsecond,
	})
	if err := worker.processTask(context.Background(), "task-policy"); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, event := range cloud.events {
		if event.Type == "RECOVERY_ACTIVITY" && event.Payload["recoveryClass"] == "POLICY_RETRY" {
			found = true
			if _, leaked := event.Payload["rawError"]; leaked {
				t.Fatalf("raw error leaked: %#v", event)
			}
		}
	}
	if !found || len(provider.requests) != 3 {
		// One retry for pick and one normal request for place.
		t.Fatalf("found=%v requests=%d", found, len(provider.requests))
	}
}

type learnedRuntime struct {
	mu       sync.Mutex
	commands []runtime.Command
}

func (runtimeClient *learnedRuntime) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{Object: manipulation.SceneRef{ID: "red-block", Confidence: 1}, Destination: manipulation.SceneRef{ID: "handoff-zone", Confidence: 1}}, nil
}
func (runtimeClient *learnedRuntime) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	runtimeClient.mu.Lock()
	defer runtimeClient.mu.Unlock()
	runtimeClient.commands = append(runtimeClient.commands, command)
	return runtime.Result{Success: true, ObservationID: "observation/" + command.CommandID}, nil
}
func (runtimeClient *learnedRuntime) Info(context.Context) (runtime.Snapshot, error) {
	return runtime.Snapshot{RobotID: "robot-1", Adapter: "mujoco", Ready: true, Capabilities: []runtime.Capability{
		{Name: "manipulation.pick", Available: true, InputParameters: []string{"action_chunk"}},
		{Name: "manipulation.place", Available: true, InputParameters: []string{"action_chunk"}},
	}}, nil
}
func (runtimeClient *learnedRuntime) Cancel(context.Context, string, string) (bool, error) {
	return true, nil
}
func (runtimeClient *learnedRuntime) EmergencyStop(context.Context, string) error { return nil }
func (runtimeClient *learnedRuntime) Telemetry(context.Context, string) (telemetry.Snapshot, error) {
	return telemetry.Snapshot{
		ObservedAt: time.Now().UTC(), RobotID: "robot-1", Adapter: "mujoco",
		RobotState: map[string]any{"reward": 0.0},
		Entities:   []telemetry.Entity{{EntityID: "red-block", Category: "block", Confidence: 1}},
	}, nil
}

func policyTestManifest() policy.Manifest {
	return policy.Manifest{
		SchemaVersion: "policy.manifest.v1", PolicyID: "sim-policy", Version: "1",
		Framework: policy.FrameworkDeterministic, ArtifactSHA256: "deterministic:sim-policy-v1",
		Capabilities: []string{"manipulation.pick", "manipulation.place"},
		RobotModels:  []string{"xlerobot-sim"}, Adapters: []string{"mujoco"},
		ObservationSchema: "policy.observation.v1", RequiredObservationSources: []string{"scene", "proprioception"},
		MaxObservationAge: time.Second, ActionSchema: "xlerobot.named-joints.v1", MaxActionChunkLength: 8,
		ActionBounds: map[string]policy.ActionBound{"left_arm_gripper.pos": {Minimum: 0, Maximum: 100}},
	}
}

func policyTask(t *testing.T) (*tasks.Task, *coordinator.IntentNode, *activityCloud) {
	t.Helper()
	parsed, err := intent.NewDeterministicParser().Parse("让1号机器人把红色方块放到交接区")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "task-policy", Adapter: "mujoco", Intent: parsed, CurrentRevision: 1, AggregateVersion: 1}
	node := &coordinator.IntentNode{Index: 0, StepID: "handoff/sender", TaskRevision: 1, AggregateVersion: 1, CommandID: "task-policy/intent/0", RobotID: "robot-1"}
	return task, node, &activityCloud{task: task, node: node}
}

func TestWorkerUsesPolicyOnlyForLearnedPhysicalToolsAndRedactsActionsFromEvents(t *testing.T) {
	_, _, cloud := policyTask(t)
	provider := &recordingPolicy{manifest: policyTestManifest()}
	runtimeClient := &learnedRuntime{}
	worker := New(Config{
		RobotID: "robot-1", Adapter: "mujoco", RobotModel: "xlerobot-sim",
		TransformRevision: "mujoco-world-v1", Cloud: cloud, Runtime: runtimeClient, Policy: provider,
	})
	if err := worker.processTask(context.Background(), "task-policy"); err != nil {
		t.Fatal(err)
	}
	if len(provider.requests) != 2 {
		t.Fatalf("policy requests = %d, want pick and place", len(provider.requests))
	}
	policyCommands := 0
	for _, command := range runtimeClient.commands {
		if command.Capability != "manipulation.pick" && command.Capability != "manipulation.place" {
			continue
		}
		policyCommands++
		if _, ok := command.Parameters["action_chunk"]; !ok {
			t.Fatalf("missing action chunk: %#v", command.Parameters)
		}
		if _, ok := command.Parameters["policy_execution"]; !ok {
			t.Fatalf("missing policy evidence: %#v", command.Parameters)
		}
	}
	if policyCommands != 2 {
		t.Fatalf("policy commands = %d", policyCommands)
	}
	for _, event := range cloud.events {
		arguments, _ := event.Payload["arguments"].(map[string]any)
		if _, leaked := arguments["action_chunk"]; leaked {
			t.Fatalf("action chunk leaked in event: %#v", event)
		}
	}
}

func TestWorkerFailsBeforeInvocationWhenRuntimeRequiresPolicyButNoneConfigured(t *testing.T) {
	_, _, cloud := policyTask(t)
	runtimeClient := &learnedRuntime{}
	worker := New(Config{RobotID: "robot-1", Adapter: "mujoco", Cloud: cloud, Runtime: runtimeClient})
	err := worker.processTask(context.Background(), "task-policy")
	if !errors.Is(err, ErrPolicyRequired) || len(runtimeClient.commands) != 3 {
		// observe_scene, resolve_targets and plan_grasp are safe before the first learned physical tool.
		t.Fatalf("error=%v commands=%d", err, len(runtimeClient.commands))
	}
}
