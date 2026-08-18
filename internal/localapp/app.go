// Package localapp owns the single-user laptop execution lifecycle. It
// deliberately has no distributed claim or task-lease protocol: SQLite is the
// business-state authority and one worker serializes physical work.
package localapp

import (
	"context"
	"errors"
	"fmt"
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
)

var ErrApprovalRequired = errors.New("operator approval required")

type App struct {
	service *orchestrator.Service
	runner  *agent.Runner
	queue   chan string

	startOnce sync.Once
	mu        sync.Mutex
	queued    map[string]struct{}
	active    map[string]context.CancelFunc
}

func New(service *orchestrator.Service, runner *agent.Runner) *App {
	return &App{
		service: service,
		runner:  runner,
		queue:   make(chan string, 64),
		queued:  map[string]struct{}{},
		active:  map[string]context.CancelFunc{},
	}
}

func (a *App) Start(ctx context.Context) {
	a.startOnce.Do(func() {
		a.reconcile(ctx)
		go a.work(ctx)
	})
}

func (a *App) Enqueue(taskID string) error {
	task, err := a.service.Get(context.Background(), taskID)
	if err != nil {
		return err
	}
	if !task.Approved {
		return ErrApprovalRequired
	}
	if terminal(task.State) {
		return fmt.Errorf("task %s is already terminal: %s", task.ID, task.State)
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if _, exists := a.queued[taskID]; exists {
		return nil
	}
	if _, exists := a.active[taskID]; exists {
		return nil
	}
	a.queued[taskID] = struct{}{}
	a.queue <- taskID
	return nil
}

func (a *App) Cancel(taskID string) error {
	a.mu.Lock()
	cancel := a.active[taskID]
	a.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	task, err := a.service.Get(context.Background(), taskID)
	if err != nil {
		return err
	}
	if terminal(task.State) {
		return nil
	}
	return a.service.Transition(context.Background(), taskID, taskgraph.StateCancelled, "operator cancelled")
}

func (a *App) work(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case taskID := <-a.queue:
			a.run(ctx, taskID)
		}
	}
}

func (a *App) run(parent context.Context, taskID string) {
	a.mu.Lock()
	delete(a.queued, taskID)
	runContext, cancel := context.WithCancel(parent)
	a.active[taskID] = cancel
	a.mu.Unlock()
	defer func() {
		cancel()
		a.mu.Lock()
		delete(a.active, taskID)
		a.mu.Unlock()
	}()

	task, err := a.service.Get(runContext, taskID)
	if err != nil || !task.Approved || terminal(task.State) {
		return
	}
	if task.State != taskgraph.StateReady {
		return
	}
	if err := a.service.Transition(runContext, taskID, taskgraph.StateObserving, "local execution started"); err != nil {
		return
	}
	result, err := a.runner.Run(runContext, task)
	if err != nil {
		if errors.Is(runContext.Err(), context.Canceled) {
			_ = a.service.Transition(context.Background(), taskID, taskgraph.StateCancelled, "local execution cancelled")
			return
		}
		_ = a.service.Transition(context.Background(), taskID, taskgraph.StateRecoverableFailure, err.Error())
		return
	}
	for _, transition := range []struct {
		state  taskgraph.TaskState
		reason string
	}{
		{taskgraph.StatePlanning, "grounding and plan completed"},
		{taskgraph.StateExecuting, "physical skills completed locally"},
		{taskgraph.StateVerifying, "post-action verification completed"},
		{taskgraph.StateSucceeded, "closed-loop task succeeded"},
	} {
		if err := a.service.Transition(runContext, taskID, transition.state, transition.reason); err != nil {
			return
		}
	}
	_, _ = a.service.AppendEvent(runContext, taskID, orchestrator.TaskEvent{
		Type:    "LOCAL_RUN_SUCCEEDED",
		Payload: map[string]any{"completedSteps": result.CompletedSteps},
	})
}

func (a *App) reconcile(ctx context.Context) {
	tasks, err := a.service.List(ctx)
	if err != nil {
		return
	}
	for _, task := range tasks {
		switch task.State {
		case taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting, taskgraph.StateVerifying:
			if taskgraph.CanTransition(task.State, taskgraph.StateRecoverableFailure) {
				_ = a.service.Transition(ctx, task.ID, taskgraph.StateRecoverableFailure, "Local Agent restarted during execution")
			}
		}
	}
}

func terminal(state taskgraph.TaskState) bool {
	return state == taskgraph.StateSucceeded || state == taskgraph.StateCancelled || state == taskgraph.StateFailed
}
