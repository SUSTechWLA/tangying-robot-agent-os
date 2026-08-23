// Package worker is the fleet edge worker: one process per robot that
// bridges the cloud control plane and the local Robot Runtime.
//
// The worker pulls ready task ids (Redis Stream or HTTP long-poll), claims
// intents from the cloud coordinator, executes them on the Robot Runtime
// over mTLS gRPC, and reports intents, step events and telemetry back. The
// optional mTLS gRPC Link channel keeps the device lease alive (heartbeat)
// and receives server commands (cancel / emergency stop). The Robot Runtime
// never learns that commands originated from a cloud fleet.
package worker

import (
	"context"
	"errors"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/compiler"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/guard"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/recovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Config wires one edge worker.
type TaskCloud interface {
	GetTask(context.Context, string) (*tasks.Task, error)
	NextIntent(context.Context, string, string) (*coordinator.IntentNode, error)
	CompleteIntentRevision(context.Context, string, *coordinator.IntentNode, string) error
	FailIntentRevision(context.Context, string, *coordinator.IntentNode, string, string) error
	AppendEvent(context.Context, string, string, string, string, map[string]any) error
	ReportTelemetry(context.Context, fleettelemetry.Sample) error
}

type RobotRuntime interface {
	Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error)
	Invoke(context.Context, runtime.Command) (runtime.Result, error)
	Info(context.Context) (runtime.Snapshot, error)
	Cancel(context.Context, string, string) (bool, error)
	EmergencyStop(context.Context, string) error
	Telemetry(context.Context, string) (telemetry.Snapshot, error)
}

type Config struct {
	RobotID string
	// Adapter is reported to the fleet (default "mujoco").
	Adapter string
	// Source pulls ready task ids (HTTP long-poll or Redis Stream).
	Source TaskSource
	// Cloud is the HTTP data-plane client.
	Cloud TaskCloud
	// Link is the optional mTLS gRPC presence channel.
	Link *cloudclient.Link
	// Runtime is the Robot Runtime client (mTLS gRPC).
	Runtime RobotRuntime
	// Policy is an optional model-neutral action provider. It is mandatory when
	// the connected Runtime advertises action_chunk for a physical capability.
	Policy            policy.Provider
	PolicyMaxAttempts int
	PolicyRetryDelay  time.Duration
	// PolicyObservation may provide camera/frame-backed observations without
	// changing the canonical policy contract. When nil, local Runtime telemetry
	// is used.
	PolicyObservation interface {
		ObservePolicy(context.Context, runtime.Command) (policy.ObservationBundle, error)
	}
	RobotModel          string
	CalibrationRevision string
	// Observer may replace Runtime for observation-only adapters and tests.
	// When nil, Runtime provides telemetry observations.
	Observer interface {
		Telemetry(context.Context, string) (telemetry.Snapshot, error)
	}
	WorldID           string
	TransformRevision string
	AdapterVersion    string
	// ObservationSequenceBase provides a restart-monotonic high-water mark.
	// Edge entrypoints derive it from Unix time; tests and embedded Local Brain
	// may leave it zero for compact sequences starting at one.
	ObservationSequenceBase uint64
	// WorldPose is an optional [x, y, z, yaw] offset added to the robot pose
	// before reporting (defaults to zeros when empty).
	WorldPose []float64
	// TelemetryInterval bounds telemetry reports (default 2s).
	TelemetryInterval time.Duration
}

// Worker is one robot's edge worker.
type Worker struct {
	config      Config
	observation struct {
		sync.Mutex
		sequence map[string]uint64
		base     uint64
	}
	current struct {
		sync.Mutex
		commandID string
	}
}

func New(config Config) *Worker {
	if config.TelemetryInterval <= 0 {
		config.TelemetryInterval = 2 * time.Second
	}
	if config.WorldID == "" {
		if config.Adapter == "robocasa" {
			config.WorldID = "robocasa-handoff-v1"
		} else {
			config.WorldID = "fleet-default"
		}
	}
	if config.TransformRevision == "" {
		config.TransformRevision = config.Adapter + "-world-v1"
	}
	if config.AdapterVersion == "" {
		config.AdapterVersion = "0.1.0-rc.2"
	}
	if config.PolicyMaxAttempts <= 0 {
		config.PolicyMaxAttempts = 3
	}
	if config.PolicyRetryDelay <= 0 {
		config.PolicyRetryDelay = 250 * time.Millisecond
	}
	worker := &Worker{config: config}
	worker.observation.sequence = map[string]uint64{}
	worker.observation.base = config.ObservationSequenceBase
	return worker
}

// Run drives the task loop (blocking) and the telemetry loop until ctx is
// cancelled.
func (w *Worker) Run(ctx context.Context) error {
	telemetryCtx, stopTelemetry := context.WithCancel(ctx)
	defer stopTelemetry()
	go w.telemetryLoop(telemetryCtx)
	return w.taskLoop(ctx)
}

// HandleCommand processes server commands pushed over the Link channel.
func (w *Worker) HandleCommand(ctx context.Context, command *fleetv1.ServerCommand) {
	switch command.Type {
	case "cancel_step":
		w.cancelCurrent(ctx, command.Args["reason"])
	case "emergency_stop":
		reason := command.Args["reason"]
		if reason == "" {
			reason = "operator emergency stop"
		}
		if err := w.config.Runtime.EmergencyStop(ctx, reason); err != nil {
			log.Printf("edge-worker %s: emergency stop failed: %v", w.config.RobotID, err)
		} else {
			log.Printf("edge-worker %s: emergency stop latched (%s)", w.config.RobotID, reason)
		}
	case "ping":
		log.Printf("edge-worker %s: ping from fleet", w.config.RobotID)
	default:
		log.Printf("edge-worker %s: unknown server command %q", w.config.RobotID, command.Type)
	}
}

func (w *Worker) taskLoop(ctx context.Context) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		taskID, err := w.config.Source.Next(ctx)
		if err != nil {
			if errors.Is(err, ErrSourceStopped) || errors.Is(err, context.Canceled) {
				return err
			}
			log.Printf("edge-worker %s: pull task: %v", w.config.RobotID, err)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Second):
			}
			continue
		}
		if taskID == "" {
			continue
		}
		if err := retryTask(ctx, 60, time.Second, func() error {
			return w.processTask(ctx, taskID)
		}); err != nil {
			log.Printf("edge-worker %s: task %s failed: %v", w.config.RobotID, taskID, err)
		}
	}
}

// retryTask keeps ownership of a dequeued task notification until the
// control plane can be queried again. Redis Streams acknowledgements happen
// before intent claim, so dropping a transient claim error here would leave
// a READY task with no future wakeup after a control-plane restart.
func retryTask(
	ctx context.Context,
	maxAttempts int,
	delay time.Duration,
	process func() error,
) error {
	if maxAttempts <= 0 {
		maxAttempts = 1
	}
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		err := process()
		if err == nil {
			return nil
		}
		if attempt == maxAttempts {
			return err
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			if !timer.Stop() {
				<-timer.C
			}
			return ctx.Err()
		case <-timer.C:
		}
	}
	return nil
}

func (w *Worker) processTask(ctx context.Context, taskID string) error {
	task, err := w.config.Cloud.GetTask(ctx, taskID)
	if err != nil {
		return fmt.Errorf("fetch task: %w", err)
	}
	if isTerminalTaskState(string(task.State)) {
		return nil
	}
	for {
		node, err := w.config.Cloud.NextIntent(ctx, taskID, w.config.RobotID)
		if err != nil {
			return fmt.Errorf("claim intent: %w", err)
		}
		if node == nil {
			return nil
		}
		task, err = w.config.Cloud.GetTask(ctx, taskID)
		if err != nil {
			return fmt.Errorf("refresh claimed task: %w", err)
		}
		if task.CurrentRevision != node.TaskRevision {
			return fmt.Errorf("%w: claimed revision=%d current revision=%d", coordinator.ErrStaleTaskRevision,
				node.TaskRevision, task.CurrentRevision)
		}
		if err := w.runIntent(ctx, task, node); err != nil {
			_ = w.config.Cloud.FailIntentRevision(ctx, taskID, node, w.config.RobotID, err.Error())
			return err
		}
	}
}

func (w *Worker) runIntent(ctx context.Context, task *tasks.Task, node *coordinator.IntentNode) error {
	index := node.Index
	intents := task.Intent.Tasks()
	if index < 0 || index >= len(intents) {
		return fmt.Errorf("intent index %d out of range", index)
	}
	intent := intents[index]
	prefix := fmt.Sprintf("intent-%d", index)
	_ = w.config.Cloud.AppendEvent(ctx, task.ID, "INTENT_STARTED", prefix,
		fmt.Sprintf("robot %s starts %s", w.config.RobotID, intent.Action), nil)

	runtimeSnapshot, err := w.preflight(ctx, task)
	if err != nil {
		return err
	}
	grounded, err := w.config.Runtime.Ground(ctx, intent)
	if err != nil {
		return fmt.Errorf("ground intent %d: %w", index, err)
	}
	grounded.TaskID = task.ID
	grounded.RobotID = w.config.RobotID
	grounded.Action = intent.Action
	grounded.KeepUpright = intent.Constraints.KeepUpright
	grounded.StepIDPrefix = fmt.Sprintf("task%02d-", index+1)

	plan := manipulation.Plan(grounded, time.Now().Add(time.Minute))
	if err := guard.New(manipulation.Catalog()).Validate(plan); err != nil {
		return fmt.Errorf("validate plan: %w", err)
	}
	graph, err := compiler.New().Compile(plan)
	if err != nil {
		return fmt.Errorf("compile plan: %w", err)
	}
	type confirmedTool struct {
		command     runtime.Command
		evidenceIDs []string
	}
	confirmed := make([]confirmedTool, 0, len(graph.Order))
	for _, stepID := range graph.Order {
		step := graph.Nodes[stepID].Step
		command := commandForIntentNode(task.ID, step, node, w.config.RobotID)
		command, err = w.preparePolicyCommandWithRecovery(ctx, command, runtimeSnapshot, node)
		if err != nil {
			return fmt.Errorf("prepare policy for step %s: %w", stepID, err)
		}
		w.setCurrent(command.CommandID)
		_ = w.config.Cloud.AppendEvent(ctx, task.ID, "TOOL_ACTIVITY", node.StepID, "",
			toolActivityPayload(node, command, w.config.RobotID, "SENDING", nil))
		_ = w.config.Cloud.AppendEvent(ctx, task.ID, "TOOL_ACTIVITY", node.StepID, "",
			toolActivityPayload(node, command, w.config.RobotID, "RUNNING", nil))
		result, invokeErr := w.config.Runtime.Invoke(ctx, command)
		w.clearCurrent(command.CommandID)
		if invokeErr != nil {
			payload := toolActivityPayload(node, command, w.config.RobotID, "FAILED", nil)
			payload["error"] = invokeErr.Error()
			_ = w.config.Cloud.AppendEvent(ctx, task.ID, "TOOL_ACTIVITY", node.StepID, "", payload)
			return fmt.Errorf("step %s: %w", stepID, invokeErr)
		}
		if !result.Success {
			payload := toolActivityPayload(node, command, w.config.RobotID, "FAILED", nil)
			payload["errorCode"] = result.Code
			_ = w.config.Cloud.AppendEvent(ctx, task.ID, "TOOL_ACTIVITY", node.StepID, "", payload)
			return fmt.Errorf("step %s failed: %s %s", stepID, result.Code, result.Message)
		}
		evidence := []string(nil)
		if result.ObservationID != "" {
			evidence = []string{result.ObservationID}
		}
		_ = w.config.Cloud.AppendEvent(ctx, task.ID, "TOOL_ACTIVITY", node.StepID, "",
			toolActivityPayload(node, command, w.config.RobotID, "AWAITING_EVIDENCE", evidence))
		confirmed = append(confirmed, confirmedTool{command: command, evidenceIDs: evidence})
	}
	if err := retryWorldCompletion(ctx, 40, 250*time.Millisecond, func(attemptCtx context.Context) error {
		return w.config.Cloud.CompleteIntentRevision(attemptCtx, task.ID, node, w.config.RobotID)
	}); err != nil {
		return err
	}
	for _, tool := range confirmed {
		_ = w.config.Cloud.AppendEvent(ctx, task.ID, "TOOL_ACTIVITY", node.StepID, "",
			toolActivityPayload(node, tool.command, w.config.RobotID, "CONFIRMED", tool.evidenceIDs))
	}
	_ = w.config.Cloud.AppendEvent(ctx, task.ID, "INTENT_SUCCEEDED", prefix,
		fmt.Sprintf("robot %s finished %s", w.config.RobotID, intent.Action), nil)
	return nil
}

func (w *Worker) preparePolicyCommandWithRecovery(
	ctx context.Context,
	command runtime.Command,
	snapshot runtime.Snapshot,
	node *coordinator.IntentNode,
) (runtime.Command, error) {
	for attempt := 1; attempt <= w.config.PolicyMaxAttempts; attempt++ {
		prepared, err := w.preparePolicyCommand(ctx, command, snapshot)
		if err == nil {
			return prepared, nil
		}
		activity := recovery.Classify(err)
		_ = w.config.Cloud.AppendEvent(ctx, command.TaskID, "RECOVERY_ACTIVITY", command.StepID, "", map[string]any{
			"recoveryClass": string(activity.Class), "attempt": uint64(attempt),
			"maxAttempts": uint64(w.config.PolicyMaxAttempts), "knownState": activity.KnownState,
			"robotSafetyState": activity.RobotSafetyState, "automaticAction": activity.AutomaticAction,
			"technicalCode": activity.TechnicalCode, "commandId": command.CommandID,
			"taskRevision": command.TaskRevision, "aggregateVersion": command.AggregateVersion,
			"fencingToken": command.FencingToken, "intentStepId": func() string {
				if node == nil {
					return ""
				}
				return node.StepID
			}(),
		})
		if !activity.Retryable || attempt == w.config.PolicyMaxAttempts {
			return runtime.Command{}, err
		}
		timer := time.NewTimer(w.config.PolicyRetryDelay)
		select {
		case <-ctx.Done():
			if !timer.Stop() {
				<-timer.C
			}
			return runtime.Command{}, ctx.Err()
		case <-timer.C:
		}
	}
	return runtime.Command{}, errors.New("policy retry exhausted")
}

func retryWorldCompletion(
	ctx context.Context,
	maxAttempts int,
	delay time.Duration,
	complete func(context.Context) error,
) error {
	if maxAttempts <= 0 {
		maxAttempts = 1
	}
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		err := complete(ctx)
		if err == nil {
			return nil
		}
		if !errors.Is(err, cloudclient.ErrWorldNotReady) || attempt == maxAttempts {
			return err
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			if !timer.Stop() {
				<-timer.C
			}
			return ctx.Err()
		case <-timer.C:
		}
	}
	return nil
}

func commandForIntentNode(
	taskID string,
	step taskgraph.SkillStep,
	node *coordinator.IntentNode,
	robotID string,
) runtime.Command {
	command := agent.CommandForStep(taskID, step)
	command.RobotID = robotID
	if node == nil {
		return command
	}
	command.CatalogRevision = node.CatalogRevision
	command.WorldRevisionBasis = node.WorldRevision
	command.TaskRevision = node.TaskRevision
	command.AggregateVersion = node.AggregateVersion
	command.StepID = node.StepID
	command.CommandID = node.CommandID + "/tool/" + step.ID
	command.IdempotencyKey = command.CommandID
	if step.SafetyLevel == string(skills.SafetyPhysical) {
		command.ResourceID = node.ResourceID
		command.FencingToken = node.FencingToken
	}
	return command
}

func toolActivityPayload(
	node *coordinator.IntentNode,
	command runtime.Command,
	robotID string,
	status string,
	evidenceIDs []string,
) map[string]any {
	payload := map[string]any{
		"toolName": string(command.Capability), "activityStatus": status, "robotId": robotID,
		"commandId": command.CommandID, "arguments": safePolicyParameters(command.Parameters),
	}
	if node != nil {
		payload["taskRevision"] = node.TaskRevision
		payload["aggregateVersion"] = node.AggregateVersion
		payload["stepId"] = node.StepID
		payload["intentCommandId"] = node.CommandID
		payload["fencingToken"] = node.FencingToken
		payload["catalogRevision"] = node.CatalogRevision
	}
	if len(evidenceIDs) > 0 {
		payload["evidenceIds"] = append([]string(nil), evidenceIDs...)
	}
	return payload
}

// preflight asks the Robot Runtime for its capability snapshot and fails
// closed when the adapter mismatch or a planned skill is unavailable. The
// ground/plan happens after so the plan can be checked against reality.
func (w *Worker) preflight(ctx context.Context, task *tasks.Task) (runtime.Snapshot, error) {
	snapshot, err := w.config.Runtime.Info(ctx)
	if err != nil {
		return runtime.Snapshot{}, fmt.Errorf("fetch robot capabilities: %w", err)
	}
	expected := tasks.NormalizeAdapter(task.Adapter)
	if expected != "" && expected != "auto" && snapshot.Adapter != "" && snapshot.Adapter != expected {
		return runtime.Snapshot{}, fmt.Errorf("%w: requested=%s connected=%s", runtime.ErrAdapterMismatch, expected, snapshot.Adapter)
	}
	if !snapshot.PhysicalReady() {
		return runtime.Snapshot{}, fmt.Errorf("%w: %s (%v)", runtime.ErrRobotNotReady, snapshot.RobotID, snapshot.Blockers)
	}
	return snapshot, nil
}

func (w *Worker) setCurrent(commandID string) {
	w.current.Lock()
	defer w.current.Unlock()
	w.current.commandID = commandID
}

func (w *Worker) clearCurrent(commandID string) {
	w.current.Lock()
	defer w.current.Unlock()
	if w.current.commandID == commandID {
		w.current.commandID = ""
	}
}

func (w *Worker) cancelCurrent(ctx context.Context, reason string) {
	w.current.Lock()
	commandID := w.current.commandID
	w.current.Unlock()
	if commandID == "" {
		log.Printf("edge-worker %s: cancel requested but no command is running", w.config.RobotID)
		return
	}
	accepted, err := w.config.Runtime.Cancel(ctx, commandID, reason)
	if err != nil {
		log.Printf("edge-worker %s: cancel %s failed: %v", w.config.RobotID, commandID, err)
		return
	}
	log.Printf("edge-worker %s: cancel %s accepted=%v", w.config.RobotID, commandID, accepted)
}

func isTerminalTaskState(state string) bool {
	switch state {
	case "SUCCEEDED", "FAILED", "CANCELLED", "SAFETY_STOPPED":
		return true
	default:
		return false
	}
}

var _ = fleettelemetry.Sample{}
