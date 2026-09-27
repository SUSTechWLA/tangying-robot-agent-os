package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type claimRenewer interface {
	RenewIntentRevision(context.Context, string, *coordinator.IntentNode, string) error
}
type cloudEvents struct {
	cloud TaskCloud
	task  *tasks.Task
	node  *coordinator.IntentNode
	robot string
}

func (c cloudEvents) Get(ctx context.Context, id string) (*tasks.Task, error) {
	return c.cloud.GetTask(ctx, id)
}
func (c cloudEvents) AppendEvent(ctx context.Context, id string, e tasks.TaskEvent) (*tasks.Task, error) {
	if e.Payload == nil {
		e.Payload = map[string]any{}
	}
	e.Payload["robotId"] = c.robot
	if strings.HasPrefix(e.Type, "CAPABILITY_") {
		e.Payload["leaseCommandId"] = c.node.CommandID
		e.Payload["catalogRevision"] = c.task.Plan.Capabilities.CatalogRevision
		e.Payload["callBinding"] = capability.Fingerprint(c.task.Plan.Capabilities.Calls[c.node.Index])
	}
	err := c.cloud.AppendEvent(ctx, id, e.Type, e.StepID, e.Message, e.Payload)
	return nil, err
}

type guardedServices struct {
	capabilityagent.Provider
	before func(context.Context) error
	stop   string
	status string
}

func (p guardedServices) CallService(ctx context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	// Only the executor's ownership-checked stop path may run after lease loss.
	if r.Name != p.stop && r.Name != p.status {
		if err := p.before(ctx); err != nil {
			return nil, err
		}
	}
	return p.Provider.CallService(ctx, r)
}

type capabilityInvoker struct {
	w        *Worker
	node     *coordinator.IntentNode
	snapshot runtime.Snapshot
	before   func(context.Context) error
}

func (i capabilityInvoker) Info(ctx context.Context) (runtime.Snapshot, error) {
	return i.w.config.Runtime.Info(ctx)
}
func (i capabilityInvoker) Invoke(ctx context.Context, c runtime.Command) (runtime.Result, error) {
	if err := i.before(ctx); err != nil {
		return runtime.Result{}, err
	}
	c.TaskRevision = i.node.TaskRevision
	c.AggregateVersion = i.node.AggregateVersion
	c.FencingToken = i.node.FencingToken
	c.ResourceID = i.node.ResourceID
	c.CatalogRevision = i.snapshot.CatalogRevision
	var err error
	c, err = i.w.preparePolicyCommandWithRecovery(ctx, c, i.snapshot, i.node)
	if err != nil {
		return runtime.Result{}, err
	}
	i.w.setCurrent(c.CommandID)
	defer i.w.clearCurrent(c.CommandID)
	return i.w.config.Runtime.Invoke(ctx, c)
}

func (w *Worker) runCapabilityIntent(parent context.Context, task *tasks.Task, node *coordinator.IntentNode) error {
	if !task.Approved || task.Plan.Capabilities.RobotID != w.config.RobotID || node.Index < 0 || node.Index >= len(task.Plan.Capabilities.Calls) {
		return errors.New("approved, robot-bound capability claim required")
	}
	if w.config.ExecutionStore == nil {
		return errors.New("durable capability execution store required")
	}
	provider, ok := w.config.Runtime.(capabilityagent.Provider)
	if !ok {
		return errors.New("Runtime does not publish capability services")
	}
	renewer, ok := w.config.Cloud.(claimRenewer)
	if !ok {
		return errors.New("capability goals require renewable cloud claims")
	}
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	w.current.Lock()
	w.current.capabilityCancel = cancel
	w.current.Unlock()
	defer func() { w.current.Lock(); w.current.capabilityCancel = nil; w.current.Unlock() }()
	before := func(ctx context.Context) error {
		return renewer.RenewIntentRevision(ctx, task.ID, node, w.config.RobotID)
	}
	if err := before(ctx); err != nil {
		return err
	}
	// Continuous renewal covers long-running operations and slow physical skills.
	renewalError := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				renewalCtx, done := context.WithTimeout(ctx, 5*time.Second)
				err := before(renewalCtx)
				done()
				if err != nil {
					renewalError <- err
					cancel()
					stopCtx, done := context.WithTimeout(context.Background(), 10*time.Second)
					w.cancelCurrent(stopCtx, "cloud claim renewal lost")
					done()
					return
				}
			}
		}
	}()
	events := cloudEvents{cloud: w.config.Cloud, task: task, node: node, robot: w.config.RobotID}
	_, catalog, err := capabilityagent.Catalogue(ctx, provider)
	if err != nil {
		return err
	}
	stop, status := "", ""
	if op := catalog[task.Plan.Capabilities.Calls[node.Index].Tool].Contract.Operation; op != nil {
		stop = op.CancelService
		status = op.StatusService
	}
	executor := &capabilityagent.Executor{RequireOperationLease: true, Provider: guardedServices{Provider: provider, before: before, stop: stop, status: status}, Store: w.config.ExecutionStore, Tasks: events, StartIndex: node.Index, EndIndex: node.Index + 1, TrustedRead: agent.IsReadOnlyCapability}
	_, err = executor.Run(ctx, task, before, func(ctx context.Context, call capability.Call, prefix string) error {
		var parsed manipulation.Intent
		if len(call.LegacyIntent) == 0 {
			return errors.New("approved composite intent missing")
		}
		if err := json.Unmarshal(call.LegacyIntent, &parsed); err != nil {
			return err
		}
		for _, intent := range parsed.Tasks() {
			if intent.RobotID != "" && intent.RobotID != w.config.RobotID {
				return errors.New("composite targets another robot")
			}
		}
		snapshot, err := w.preflight(ctx, task)
		if err != nil {
			return err
		}
		invoker := capabilityInvoker{w: w, node: node, snapshot: snapshot, before: before}
		runner := agent.NewRunner(w.config.ExecutionStore, w.config.Runtime, invoker)
		runner.TaskEvents = func(ctx context.Context, id string, event tasks.TaskEvent) error {
			_, err := events.AppendEvent(ctx, id, event)
			return err
		}
		child := *task
		child.Plan = nil
		child.Intent = parsed
		_, err = runner.RunControlled(ctx, &child, agent.RunControl{StepPrefix: prefix, BeforeStep: before})
		return err
	})
	select {
	case leaseErr := <-renewalError:
		if strings.Contains(leaseErr.Error(), "operator cancellation requested") && errors.Is(err, context.Canceled) {
			return fmt.Errorf("operator cancellation stopped: %w", err)
		}
		return fmt.Errorf("%w: claim renewal failed: %v; execution: %v", capabilityagent.ErrOutcomeUnknown, leaseErr, err)
	default:
	}
	if err != nil {
		return err
	}
	return w.config.Cloud.CompleteIntentRevision(ctx, task.ID, node, w.config.RobotID)
}
