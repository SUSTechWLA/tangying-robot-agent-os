// Package localapp owns the single-user laptop execution lifecycle. It
// deliberately has no distributed claim or task-lease protocol: SQLite is the
// business-state authority and one worker serializes physical work.
package localapp

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var (
	ErrApprovalRequired = errors.New("operator approval required")
	ErrQueueFull        = middleware.ErrQueueFull
)

type App struct {
	service *tasks.Service
	runner  *agent.Runner
	queue   middleware.Queue[string]

	startOnce sync.Once
	mu        sync.Mutex
	queued    map[string]struct{}
	active    map[string]context.CancelFunc
	pauses    map[string]bool
	resumes   map[string]string
	done      chan struct{}
}

func New(service *tasks.Service, runner *agent.Runner, queue middleware.Queue[string]) *App {
	app := &App{
		service: service,
		runner:  runner,
		queue:   queue,
		queued:  map[string]struct{}{},
		active:  map[string]context.CancelFunc{},
		pauses:  map[string]bool{},
		resumes: map[string]string{},
		done:    make(chan struct{}),
	}
	if runner != nil {
		runner.TaskEvents = func(ctx context.Context, taskID string, event tasks.TaskEvent) error {
			app.mu.Lock()
			defer app.mu.Unlock()
			_, err := service.AppendEvent(ctx, taskID, event)
			return err
		}
	}
	return app
}

func (a *App) Start(ctx context.Context) {
	a.startOnce.Do(func() {
		a.reconcile(ctx)
		go a.work(ctx)
	})
}

func (a *App) Enqueue(taskID string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	task, err := a.service.Get(context.Background(), taskID)
	if err != nil {
		return err
	}
	if !task.Approved {
		return ErrApprovalRequired
	}
	if task.State != taskgraph.StateReady {
		return fmt.Errorf("task %s cannot be enqueued from %s; use explicit recovery", task.ID, task.State)
	}
	return a.enqueueLocked(taskID)
}

func (a *App) enqueueLocked(taskID string) error {
	if _, exists := a.queued[taskID]; exists {
		return nil
	}
	if _, exists := a.active[taskID]; exists {
		return nil
	}
	a.queued[taskID] = struct{}{}
	if err := a.queue.Enqueue(context.Background(), taskID); err != nil {
		delete(a.queued, taskID)
		return err
	}
	return nil
}

func (a *App) Cancel(taskID string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	cancel := a.active[taskID]
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

// Pause finishes the current tool before stopping. It is not an emergency stop.
func (a *App) Pause(taskID string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	task, err := a.service.Get(context.Background(), taskID)
	if err != nil {
		return err
	}
	if task.State == taskgraph.StatePaused {
		return nil
	}
	if task.State == taskgraph.StateReady {
		if !task.Approved {
			return ErrApprovalRequired
		}
		return a.service.Transition(context.Background(), taskID, taskgraph.StatePaused, "operator paused before execution")
	}
	if task.State != taskgraph.StateExecuting {
		return fmt.Errorf("task cannot pause from %s", task.State)
	}
	if !a.pauses[taskID] {
		if _, err := a.service.AppendEvent(context.Background(), taskID, tasks.TaskEvent{Type: "LOCAL_PAUSE_REQUESTED", Message: "将在当前工具完成并保存后暂停"}); err != nil {
			return err
		}
		a.pauses[taskID] = true
	}
	return nil
}

func (a *App) Resume(taskID string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	task, err := a.service.Get(context.Background(), taskID)
	if err != nil {
		return err
	}
	if !task.Approved {
		return ErrApprovalRequired
	}
	if task.State != taskgraph.StatePaused && task.State != taskgraph.StateRecoverableFailure {
		return fmt.Errorf("task cannot resume from %s", task.State)
	}
	if _, running := a.active[taskID]; running {
		return errors.New("previous execution is still stopping")
	}
	if err := a.runner.CheckRecovery(context.Background(), taskID); err != nil {
		return err
	}
	if _, pending := a.resumes[taskID]; pending {
		return nil
	}
	a.resumes[taskID] = fmt.Sprintf("%d", time.Now().UnixNano())
	if err := a.enqueueLocked(taskID); err != nil {
		delete(a.resumes, taskID)
		return err
	}
	return nil
}

func (a *App) Wait(ctx context.Context) error {
	select {
	case <-a.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (a *App) RevisionBasis(ctx context.Context, taskID string) (tasks.RevisionBasis, error) {
	task, err := a.service.Get(ctx, taskID)
	if err != nil {
		return tasks.RevisionBasis{}, err
	}
	history, err := a.service.ListRevisions(ctx, taskID)
	if err != nil {
		return tasks.RevisionBasis{}, err
	}
	a.mu.Lock()
	_, active := a.active[taskID]
	a.mu.Unlock()
	basis := tasks.RevisionBasis{EvidenceValidity: map[string]bool{}}
	if !active {
		return basis, nil
	}
	for _, record := range history {
		if record.Revision.Revision != task.CurrentRevision {
			continue
		}
		for _, step := range record.Revision.Steps {
			basis.RunningStepIDs = append(basis.RunningStepIDs, step.StepID)
		}
		break
	}
	return basis, nil
}

func (a *App) ProposeRevision(ctx context.Context, command tasks.ProposeRevisionCommand) (*tasks.RevisionRecord, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	task, err := a.service.Get(ctx, command.TaskID)
	if err != nil {
		return nil, err
	}
	if terminal(task.State) {
		return nil, fmt.Errorf("terminal task cannot be revised")
	}
	if _, active := a.active[command.TaskID]; !active {
		if err := a.checkRevisionRecovery(ctx, task); err != nil {
			return nil, err
		}
	}
	history, err := a.service.ListRevisions(ctx, command.TaskID)
	if err != nil {
		return nil, err
	}
	basis := tasks.RevisionBasis{EvidenceValidity: map[string]bool{}}
	if _, active := a.active[command.TaskID]; active {
		for _, record := range history {
			if record.Revision.Revision != task.CurrentRevision {
				continue
			}
			for _, step := range record.Revision.Steps {
				basis.RunningStepIDs = append(basis.RunningStepIDs, step.StepID)
			}
			break
		}
	}
	return a.service.ProposeRevision(ctx, command, basis)
}

func (a *App) ConfirmRevision(
	ctx context.Context,
	taskID string,
	revision uint64,
	expectedCurrentRevision uint64,
	idempotencyKey string,
) (*tasks.RevisionRecord, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	task, err := a.service.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	if terminal(task.State) {
		return nil, fmt.Errorf("terminal task cannot be revised")
	}
	_, active := a.active[taskID]
	if !active {
		if err := a.checkRevisionRecovery(ctx, task); err != nil {
			return nil, err
		}
	}
	return a.service.ConfirmRevision(ctx, tasks.ConfirmRevisionCommand{
		TaskID: taskID, Revision: revision, ExpectedCurrentRevision: expectedCurrentRevision,
		IdempotencyKey: idempotencyKey, Actor: "local-owner", WaitForSafePoint: active,
	})
}

func (a *App) work(ctx context.Context) {
	defer close(a.done)
	for {
		select {
		case <-ctx.Done():
			return
		default:
			taskID, err := a.queue.Dequeue(ctx)
			if err != nil {
				return
			}
			a.run(ctx, taskID)
		}
	}
}

func (a *App) run(parent context.Context, taskID string) {
	a.mu.Lock()
	delete(a.queued, taskID)
	attempt := a.resumes[taskID]
	delete(a.resumes, taskID)
	runContext, cancel := context.WithCancel(parent)
	a.active[taskID] = cancel
	defer func() {
		cancel()
		a.mu.Lock()
		delete(a.active, taskID)
		delete(a.pauses, taskID)
		a.mu.Unlock()
	}()

	task, err := a.service.Get(runContext, taskID)
	if err != nil || !task.Approved || terminal(task.State) {
		a.mu.Unlock()
		return
	}
	if attempt == "" && task.State != taskgraph.StateReady {
		a.mu.Unlock()
		return
	}
	if attempt != "" {
		if err := a.runner.CheckRecovery(runContext, taskID); err != nil {
			a.mu.Unlock()
			return
		}
		if task.State == taskgraph.StateRecoverableFailure {
			if err := a.service.Transition(runContext, taskID, taskgraph.StateSafeRecovery, "operator requested recovery of recorded execution"); err != nil {
				a.mu.Unlock()
				return
			}
		}
	}
	if err := a.service.Transition(runContext, taskID, taskgraph.StateObserving, "local execution started"); err != nil {
		a.mu.Unlock()
		return
	}
	if err := a.service.Transition(runContext, taskID, taskgraph.StatePlanning, "grounding and local planning started"); err != nil {
		a.mu.Unlock()
		return
	}
	if err := a.service.Transition(runContext, taskID, taskgraph.StateExecuting, "local physical execution started"); err != nil {
		a.mu.Unlock()
		return
	}
	a.mu.Unlock()
	for {
		result, err := a.runner.RunControlled(runContext, task, agent.RunControl{ObservationAttempt: attempt, BeforeStep: func(ctx context.Context) error {
			if err := ctx.Err(); err != nil {
				return err
			}
			a.mu.Lock()
			defer a.mu.Unlock()
			if a.pauses[taskID] {
				return agent.ErrPauseRequested
			}
			return nil
		}})
		if err != nil {
			a.mu.Lock()
			if errors.Is(err, agent.ErrResumeBindingChanged) {
				_, _ = a.service.AppendEvent(context.Background(), taskID, tasks.TaskEvent{Type: "LOCAL_RECOVERY_BLOCKED", Payload: map[string]any{"reasonCode": "RESUME_BINDING_CHANGED"}})
			}
			state, reason := taskgraph.StateRecoverableFailure, err.Error()
			if errors.Is(err, agent.ErrPauseRequested) {
				state, reason = taskgraph.StatePaused, "当前工具结果已保存，等待用户继续"
			}
			if parent.Err() != nil {
				state, reason = taskgraph.StateRecoverableFailure, "Local Agent stopped; inspect recorded execution before resuming"
			} else if runContext.Err() != nil {
				state, reason = taskgraph.StateCancelled, "operator cancelled"
			}
			_ = a.service.Transition(context.Background(), taskID, state, reason)
			a.mu.Unlock()
			return
		}
		a.mu.Lock()
		waiting := a.waitingRevision(runContext, taskID)
		if waiting != 0 {
			_, activationErr := a.service.ActivateWaitingRevision(runContext, taskID, waiting,
				fmt.Sprintf("%s/revision/%d/local-safe-point", taskID, waiting))
			a.mu.Unlock()
			if activationErr != nil {
				_ = a.service.Transition(context.Background(), taskID, taskgraph.StateRecoverableFailure, activationErr.Error())
				return
			}
			task, err = a.service.Get(runContext, taskID)
			if err != nil {
				return
			}
			continue
		}
		finalErr := error(nil)
		for _, transition := range []struct {
			state  taskgraph.TaskState
			reason string
		}{
			{taskgraph.StateVerifying, "post-action verification completed"},
			{taskgraph.StateSucceeded, "closed-loop task succeeded"},
		} {
			if transitionErr := a.service.Transition(runContext, taskID, transition.state, transition.reason); transitionErr != nil {
				finalErr = transitionErr
				break
			}
		}
		delete(a.active, taskID)
		a.mu.Unlock()
		if finalErr != nil {
			return
		}
		_, _ = a.service.AppendEvent(runContext, taskID, tasks.TaskEvent{
			Type: "LOCAL_RUN_SUCCEEDED", Payload: map[string]any{
				"completedSteps": result.CompletedSteps, "taskRevision": task.CurrentRevision,
			},
		})
		return
	}
}

func (a *App) waitingRevision(ctx context.Context, taskID string) uint64 {
	history, err := a.service.ListRevisions(ctx, taskID)
	if err != nil {
		return 0
	}
	var waiting uint64
	for _, record := range history {
		if record.Status == tasks.RevisionWaitingSafePoint && record.Revision.Revision > waiting {
			waiting = record.Revision.Revision
		}
	}
	return waiting
}

func (a *App) reconcile(ctx context.Context) {
	tasks, err := a.service.List(ctx)
	if err != nil {
		return
	}
	for _, task := range tasks {
		switch task.State {
		case taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting, taskgraph.StateVerifying, taskgraph.StateSafeRecovery:
			if taskgraph.CanTransition(task.State, taskgraph.StateRecoverableFailure) {
				_ = a.service.Transition(ctx, task.ID, taskgraph.StateRecoverableFailure, "Local Agent restarted during execution")
			}
		}
	}
}

func terminal(state taskgraph.TaskState) bool {
	return state == taskgraph.StateSucceeded || state == taskgraph.StateCancelled || state == taskgraph.StateFailed
}
