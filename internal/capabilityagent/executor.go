package capabilityagent

import (
	"context"
	"errors"
	"fmt"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/types/known/structpb"
	"reflect"
	"strings"
	"time"
)

var ErrOutcomeUnknown = errors.New("capability outcome requires reconciliation")
var ErrNeedsInput = errors.New("capability goal needs operator input")

// ErrRejected is a provider statement of no admission, not a transport failure.
var ErrRejected = errors.New("capability request rejected before effects")

type TaskEvents interface {
	Get(context.Context, string) (*tasks.Task, error)
	AppendEvent(context.Context, string, tasks.TaskEvent) (*tasks.Task, error)
}

type Executor struct {
	RequireOperationLease bool
	TrustedRead           func(string) bool
	// Range limits a fleet lease to its single immutable capability node. Zero runs all.
	StartIndex       int
	EndIndex         int
	Provider         Provider
	Store            middleware.ExecutionStore
	Tasks            TaskEvents
	PollInterval     time.Duration
	OperationTimeout time.Duration
}
type Legacy func(context.Context, capability.Call, string) error

func (e *Executor) record(ctx context.Context, id, step, kind string, data map[string]any) error {
	_, err := e.Tasks.AppendEvent(ctx, id, tasks.TaskEvent{Type: kind, StepID: step, Payload: project(data)})
	if err == nil {
		status := ""
		switch kind {
		case "CAPABILITY_CALL":
			status = "RUNNING"
		case "CAPABILITY_RECEIPT":
			status = "AWAITING_EVIDENCE"
		case "CAPABILITY_VERIFIED":
			status = "CONFIRMED"
		case "CAPABILITY_FAILED":
			status = "FAILED"
		}
		if status != "" {
			_, err = e.Tasks.AppendEvent(ctx, id, tasks.TaskEvent{Type: "TOOL_ACTIVITY", StepID: step, Payload: map[string]any{
				"toolName": data["tool"], "stepId": step, "activityStatus": status, "arguments": data["arguments"], "commandId": data["commandId"]}})
		}
	}
	return err
}
func (e *Executor) receipt(ctx context.Context, id, step string) (map[string]any, error) {
	task, err := e.Tasks.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	for i := len(task.Events) - 1; i >= 0; i-- {
		event := task.Events[i]
		if event.StepID == step && event.Type == "CAPABILITY_RECEIPT" {
			return event.Payload, nil
		}
	}
	return nil, nil
}
func (e *Executor) call(ctx context.Context, robot, name, id string, args map[string]any) (map[string]any, error) {
	return e.callLeased(ctx, robot, name, id, args, "", "")
}
func (e *Executor) callLeased(ctx context.Context, robot, name, id string, args map[string]any, operation, owner string) (map[string]any, error) {
	callCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	values, err := structpb.NewStruct(args)
	if err != nil {
		return nil, err
	}
	request := &robotv1.ServiceRequest{RobotId: robot, Name: name, RequestId: id, Parameters: values, OperationId: operation, OperationOwnerId: owner}
	if id != "" || operation != "" {
		request.OperationLeaseMs = 30000
	}
	response, err := e.Provider.CallService(callCtx, request)
	if err != nil {
		return nil, err
	}
	if response == nil {
		return nil, fmt.Errorf("empty service response")
	}
	if !response.Ok {
		if response.Result.AsMap()["outcome"] == "REJECTED" {
			return nil, fmt.Errorf("%w: %s: %s", ErrRejected, response.Code, response.Message)
		}
		return nil, fmt.Errorf("%s: %s", response.Code, response.Message)
	}
	return response.Result.AsMap(), nil
}
func stepID(task *tasks.Task, index int) string {
	return fmt.Sprintf("rev-%d-cap-%02d", task.CurrentRevision, index+1)
}
func (e *Executor) Run(ctx context.Context, task *tasks.Task, before func(context.Context) error, legacy Legacy) ([]string, error) {
	var completed []string
	if task.Plan == nil || task.Plan.Capabilities == nil || !task.Approved {
		return nil, fmt.Errorf("approved capability plan required")
	}
	plan := task.Plan.Capabilities
	for index, call := range plan.Calls {
		if index < e.StartIndex || (e.EndIndex > 0 && index >= e.EndIndex) {
			continue
		}
		if err := ctx.Err(); err != nil {
			return completed, err
		}
		if before != nil {
			if err := before(ctx); err != nil {
				return completed, err
			}
		}
		robot, entries, err := Catalogue(ctx, e.Provider)
		if err != nil {
			return completed, err
		}
		if robot != plan.RobotID || capability.Fingerprint(entries) != plan.CatalogRevision {
			return completed, fmt.Errorf("capability catalog or robot binding changed; create and approve a new plan")
		}
		manifest, ok := entries[call.Tool]
		if !ok {
			return completed, fmt.Errorf("capability unavailable: %s", call.Tool)
		}
		if err := capability.Validate(call.Arguments, manifest.InputSchema); err != nil {
			return completed, err
		}
		if op := manifest.Contract.Operation; op != nil && e.RequireOperationLease && !op.LeaseSupported {
			return completed, fmt.Errorf("provider operation requires Runtime lease watchdog")
		}
		id := stepID(task, index)
		status, err := e.Store.StepStatus(ctx, task.ID, id)
		if err != nil {
			return completed, err
		}
		if status == middleware.StepCompleted {
			completed = append(completed, id)
			continue
		}
		identity := fmt.Sprintf("%s/%s", task.ID, id)
		level := "read_only"
		if manifest.MutatesWorld {
			level = "physical_motion"
		}
		record := middleware.StepRecord{TaskID: task.ID, StepID: id, IdempotencyKey: identity, Capability: call.Tool, SafetyLevel: level}
		if call.Tool == "robot.task" {
			// Legacy runner owns its own durable child records and physical verification.
			// No service receipt is invented for a composite call.
			if legacy == nil {
				return completed, fmt.Errorf("legacy task capability not configured")
			}
			if err := e.record(ctx, task.ID, id, "CAPABILITY_CALL", map[string]any{"tool": call.Tool, "arguments": call.Arguments, "commandId": identity}); err != nil {
				return completed, err
			}
			if err := legacy(ctx, call, id+"-"); err != nil {
				message := err.Error()
				if len(message) > 1024 {
					message = message[:1024]
				}
				_ = e.record(ctx, task.ID, id, "CAPABILITY_FAILED", map[string]any{"tool": call.Tool, "code": "LEGACY_CHILD_FAILED", "message": message})
				return completed, err
			}
			if err := e.record(ctx, task.ID, id, "CAPABILITY_VERIFIED", map[string]any{"tool": call.Tool, "evidence": map[string]any{"basis": "LEGACY_RUNNER_VERIFIED_CHILD_STEPS"}}); err != nil {
				return completed, err
			}
			if err := e.Store.MarkStepCompleted(ctx, record); err != nil {
				return completed, err
			}
			completed = append(completed, id)
			continue
		}
		receipt, err := e.receipt(ctx, task.ID, id)
		if err != nil {
			return completed, err
		}
		if receipt != nil && receipt["binding"] != capability.Fingerprint(call) {
			return completed, fmt.Errorf("%w: changed recorded arguments", ErrOutcomeUnknown)
		}
		if status == middleware.StepStarted && receipt == nil && manifest.MutatesWorld {
			return completed, fmt.Errorf("%w: %s has no durable receipt", ErrOutcomeUnknown, call.Tool)
		}
		var result map[string]any
		if receipt != nil {
			result, _ = receipt["result"].(map[string]any)
		} else {
			if err := e.Store.MarkStepStarted(ctx, record); err != nil {
				return completed, err
			}
			if err := e.record(ctx, task.ID, id, "CAPABILITY_CALL", map[string]any{"tool": call.Tool, "arguments": call.Arguments, "commandId": identity, "catalogRevision": plan.CatalogRevision}); err != nil {
				return completed, err
			}
			result, err = e.call(ctx, robot, call.Tool, identity, call.Arguments)
			if err != nil {
				if manifest.MutatesWorld && !errors.Is(err, ErrRejected) {
					return completed, fmt.Errorf("%w: %v", ErrOutcomeUnknown, err)
				}
				_ = e.Store.MarkStepFailed(ctx, record)
				return completed, err
			}
			receipt = map[string]any{"tool": call.Tool, "binding": capability.Fingerprint(call), "result": result, "deadline": time.Now().UTC().Add(e.timeout()).Format(time.RFC3339Nano)}
			if err := e.record(ctx, task.ID, id, "CAPABILITY_RECEIPT", receipt); err != nil {
				return completed, fmt.Errorf("%w: receipt persistence failed", ErrOutcomeUnknown)
			}
		}
		if result["decision"] == "ambiguous" {
			return completed, fmt.Errorf("%w: select a map from the recorded candidates", ErrNeedsInput)
		}
		if manifest.Contract.OutputSchema != nil {
			if err := capability.Validate(result, manifest.Contract.OutputSchema); err != nil {
				return completed, fmt.Errorf("invalid capability output: %w", err)
			}
		}
		if op := manifest.Contract.Operation; op != nil && result["decision"] != "reuse" {
			observed, err := e.wait(ctx, task.ID, id, robot, identity, result, op, receipt)
			if err != nil {
				return completed, err
			}
			result = observed
		}
		evidence, err := e.verify(ctx, robot, call, result, manifest.Contract.Verification)
		if err != nil {
			return completed, fmt.Errorf("completion verification failed: %w", err)
		}
		if err := e.record(ctx, task.ID, id, "CAPABILITY_VERIFIED", map[string]any{"tool": call.Tool, "evidence": evidence, "operationResult": result}); err != nil {
			return completed, err
		}
		if err := e.Store.MarkStepCompleted(ctx, record); err != nil {
			return completed, err
		}
		completed = append(completed, id)
	}
	return completed, nil
}
func (e *Executor) timeout() time.Duration {
	if e.OperationTimeout > 0 {
		return e.OperationTimeout
	}
	return 45 * time.Minute
}

func at(value map[string]any, path string) any {
	var current any = value
	for _, part := range strings.Split(path, ".") {
		m, ok := current.(map[string]any)
		if !ok {
			return nil
		}
		current = m[part]
	}
	return current
}
func contains(values []string, value any) bool {
	for _, v := range values {
		if value == v {
			return true
		}
	}
	return false
}
func (e *Executor) wait(ctx context.Context, task, step, robot, id string, result map[string]any, op *capability.Operation, receipt map[string]any) (map[string]any, error) {
	identity, ok := at(result, op.IdentityPath).(string)
	if !ok || identity == "" {
		return nil, fmt.Errorf("%w: operation did not publish identity", ErrOutcomeUnknown)
	}
	interval := e.PollInterval
	if interval <= 0 {
		interval = time.Second
	}
	timeout := e.timeout()
	if receipt != nil {
		if raw, ok := receipt["deadline"].(string); ok {
			end, err := time.Parse(time.RFC3339Nano, raw)
			if err != nil {
				return nil, fmt.Errorf("%w: invalid recorded deadline", ErrOutcomeUnknown)
			}
			timeout = time.Until(end)
		}
	}
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	lastState := ""
	for {
		var status map[string]any
		var err error
		if op.LeaseSupported {
			status, err = e.callLeased(ctx, robot, op.StatusService, "", map[string]any{}, identity, id)
		} else {
			status, err = e.call(ctx, robot, op.StatusService, "", map[string]any{})
		}
		if err == nil {
			if at(status, op.StatusIdentityPath) != identity {
				return nil, fmt.Errorf("%w: operation identity changed", ErrOutcomeUnknown)
			}
			state := at(status, op.StatePath)
			if contains(op.Success, state) {
				return status, nil
			}
			if contains(op.Failure, state) {
				return nil, fmt.Errorf("operation ended: %v (%v)", state, status["message"])
			}
			if !contains(op.Running, state) {
				return nil, fmt.Errorf("%w: unexpected operation state %v", ErrOutcomeUnknown, state)
			}
			if state != lastState {
				lastState, _ = state.(string)
				if err := e.record(ctx, task, step, "CAPABILITY_PROGRESS", map[string]any{"operationId": identity, "state": state}); err != nil {
					// Cancellation can arrive after the event is committed but before
					// its acknowledgement. Reconcile the owned operation even then.
					if ctx.Err() != nil {
						err = ctx.Err()
					}
					return nil, e.stop(task, step, robot, id, identity, op, err)
				}
			}
		}
		if err != nil && strings.Contains(err.Error(), "OPERATION_OWNER_MISMATCH") {
			return nil, fmt.Errorf("%w: operation ownership changed", ErrOutcomeUnknown)
		}
		if err != nil && strings.Contains(err.Error(), "OPERATION_LEASE_EXPIRED") {
			return nil, e.stop(task, step, robot, id, identity, op, fmt.Errorf("%w: operation lease expired", ErrOutcomeUnknown))
		}
		select {
		case <-ctx.Done():
			return nil, e.stop(task, step, robot, id, identity, op, ctx.Err())
		case <-deadline.C:
			return nil, e.stop(task, step, robot, id, identity, op, fmt.Errorf("operation exceeded configured timeout"))
		case <-ticker.C:
		}
	}
}
func (e *Executor) stop(task, step, robot, id, identity string, op *capability.Operation, cause error) error {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	status, err := e.call(ctx, robot, op.StatusService, "", map[string]any{})
	if err != nil || at(status, op.StatusIdentityPath) != identity {
		return fmt.Errorf("%w: cannot establish cancellation ownership", ErrOutcomeUnknown)
	}
	if contains(op.Success, at(status, op.StatePath)) || contains(op.Failure, at(status, op.StatePath)) {
		if err := e.record(ctx, task, step, "CAPABILITY_STOP_CONFIRMED", map[string]any{"operationId": identity, "state": at(status, op.StatePath)}); err != nil {
			return fmt.Errorf("%w: stop evidence not persisted", ErrOutcomeUnknown)
		}
		return cause
	}
	if _, err := e.call(ctx, robot, op.CancelService, id+"/cancel", map[string]any{}); err != nil {
		return fmt.Errorf("%w: cancel reply unavailable", ErrOutcomeUnknown)
	}
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		status, err = e.call(ctx, robot, op.StatusService, "", map[string]any{})
		if err == nil && at(status, op.StatusIdentityPath) == identity && (contains(op.Success, at(status, op.StatePath)) || contains(op.Failure, at(status, op.StatePath))) {
			if err := e.record(ctx, task, step, "CAPABILITY_STOP_CONFIRMED", map[string]any{"operationId": identity, "state": at(status, op.StatePath)}); err != nil {
				return fmt.Errorf("%w: stop evidence not persisted", ErrOutcomeUnknown)
			}
			return cause
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("%w: stop not confirmed", ErrOutcomeUnknown)
		case <-ticker.C:
		}
	}
}
func (e *Executor) verify(ctx context.Context, robot string, call capability.Call, result map[string]any, v *capability.Verification) (map[string]any, error) {
	if v == nil {
		return map[string]any{"readResult": result}, nil
	}
	observed, err := e.call(ctx, robot, v.Service, "", map[string]any{})
	if err != nil {
		return nil, err
	}
	for _, path := range v.Required {
		value := at(observed, path)
		if value == nil || value == "" {
			return nil, fmt.Errorf("missing completion evidence %s", path)
		}
	}
	sources := map[string]any{"arguments": call.Arguments, "result": result}
	for target, source := range v.Match {
		expected := at(sources, source)
		if expected == nil || !reflect.DeepEqual(at(observed, target), expected) {
			return nil, fmt.Errorf("completion evidence differs at %s", target)
		}
	}
	return observed, nil
}

// CheckRecovery permits only read retries and acknowledged asynchronous work.
// Losing a synchronous write receipt never authorizes replaying that write.
func (e *Executor) CheckRecovery(ctx context.Context, task *tasks.Task) error {
	reader, ok := e.Store.(middleware.ExecutionReader)
	if !ok {
		return fmt.Errorf("durable execution reader required")
	}
	runs, err := reader.ListStepRuns(ctx, task.ID)
	if err != nil {
		return err
	}
	_, entries, err := Catalogue(ctx, e.Provider)
	if err != nil {
		return err
	}
	for _, run := range runs {
		if run.Status != middleware.StepStarted {
			continue
		}
		m, ok := entries[run.Capability]
		if !ok {
			if e.TrustedRead != nil && e.TrustedRead(run.Capability) {
				continue
			}
			return fmt.Errorf("%w: unknown interrupted capability %s", ErrOutcomeUnknown, run.Capability)
		}
		if !m.MutatesWorld {
			continue
		}
		receipt, err := e.receipt(ctx, task.ID, run.StepID)
		if err != nil {
			return err
		}
		if receipt == nil {
			return fmt.Errorf("%w: interrupted write has no receipt", ErrOutcomeUnknown)
		}
	}
	return nil
}

// Bulk sensor arrays belong to existing map artifacts, not every task event.
func project(data map[string]any) map[string]any {
	out := make(map[string]any, len(data))
	for key, value := range data {
		switch key {
		case "points", "colors", "trajectory", "preview", "cells":
			continue
		}
		if nested, ok := value.(map[string]any); ok {
			out[key] = project(nested)
		} else {
			out[key] = value
		}
	}
	return out
}
