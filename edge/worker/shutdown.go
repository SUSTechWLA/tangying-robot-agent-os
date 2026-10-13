package worker

import (
	"context"
	"log"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

// invokeTracked retains command ownership until cancellation has been attempted
// on a fresh bounded context. Closing a gRPC stream alone is not stop evidence.
// The durable ACCEPTED checkpoint remains uncertain if the result is lost.
func (w *Worker) invokeTracked(ctx context.Context, command runtime.Command) (runtime.Result, error) {
	if err := ctx.Err(); err != nil {
		return runtime.Result{}, err
	}
	if !command.Deadline.IsZero() {
		var cancel context.CancelFunc
		ctx, cancel = context.WithDeadline(ctx, command.Deadline)
		defer cancel()
	}
	if err := ctx.Err(); err != nil {
		return runtime.Result{}, err
	}
	w.setCurrent(command.CommandID)
	defer w.clearCurrent(command.CommandID)
	stopDone := make(chan struct{})
	requestStop := func() {
		stopCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), w.config.ControlTimeout)
		defer cancel()
		accepted, err := w.config.Runtime.Cancel(stopCtx, command.CommandID, "edge execution context ended")
		if err != nil || !accepted {
			log.Printf("edge-worker %s: command %s stop unconfirmed: accepted=%v error=%v", w.config.RobotID, command.CommandID, accepted, err)
		}
	}
	stop := context.AfterFunc(ctx, func() { defer close(stopDone); requestStop() })
	result, err := w.config.Runtime.Invoke(ctx, command)
	if !stop() {
		<-stopDone
	} else if err != nil && !agent.IsReadOnlyCapability(string(command.Capability)) {
		// A disconnected execution stream does not prove that motion stopped.
		// Attempt an owned controlled stop, but retain outcome uncertainty even
		// when cancellation is acknowledged: the action may already have run.
		requestStop()
	}
	return result, err
}
