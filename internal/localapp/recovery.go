package localapp

import (
	"context"
	"errors"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func (a *App) Recovery(ctx context.Context, taskID string) (tasks.LocalRecovery, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	task, err := a.service.Get(ctx, taskID)
	if err != nil {
		return tasks.LocalRecovery{}, err
	}
	view := tasks.LocalRecovery{TaskID: task.ID, State: task.State, CompletedStepIDs: []string{}, UncertainStepIDs: []string{}, PauseRequested: a.pauses[taskID]}
	runs, err := a.runner.ExecutionHistory(ctx, taskID)
	if err != nil {
		return view, err
	}
	_, active := a.active[taskID]
	_, queued := a.queued[taskID]
	for _, run := range runs {
		if run.Status == middleware.StepCompleted {
			view.CompletedStepIDs = append(view.CompletedStepIDs, run.StepID)
		}
		if !active && run.Status == middleware.StepStarted && !agent.IsReadOnlyCapability(run.Capability) {
			view.UncertainStepIDs = append(view.UncertainStepIDs, run.StepID)
		}
	}
	view.RequiresReconciliation = len(view.UncertainStepIDs) > 0
	view.CanPause = task.Approved && !view.PauseRequested && (task.State == taskgraph.StateReady || task.State == taskgraph.StateExecuting)
	view.CanResume = task.Approved && !active && !queued && !view.RequiresReconciliation && (task.State == taskgraph.StatePaused || task.State == taskgraph.StateRecoverableFailure)
	switch {
	case view.RequiresReconciliation:
		view.ReasonCode, view.Reason = "PHYSICAL_OUTCOME_UNKNOWN", "有物理动作缺少完成记录，请先核对机器人和物体状态。系统不会重放，也不允许通过修改任务版本绕过。"
	case view.PauseRequested:
		view.ReasonCode, view.Reason = "PAUSING", "正在等待当前工具结束并保存结果；这不是急停。"
	case queued:
		view.ReasonCode, view.Reason = "QUEUED", "任务已排队，等待当前机器人任务结束。"
	case view.CanResume:
		view.ReasonCode, view.Reason = "RESUME_AVAILABLE", "已保存执行进度。继续时重新感知并验证，只执行尚未完成的动作。"
	case task.State == taskgraph.StateSafetyStopped:
		view.ReasonCode, view.Reason = "SAFETY_STOPPED", "机器人已安全停止，请先解除设备风险；不能通过任务恢复自动解除急停。"
	case task.State == taskgraph.StatePaused && !task.Approved:
		view.ReasonCode, view.Reason = "APPROVAL_REQUIRED", "任务尚未获得动作授权，请先确认执行。"
	default:
		view.ReasonCode, view.Reason = "NO_RECOVERY_REQUIRED", "执行记录已保存，可查看任务时间线。"
	}
	if view.CanResume && task.State == taskgraph.StateRecoverableFailure {
		for index := len(task.Events) - 1; index >= 0; index-- {
			event := task.Events[index]
			if event.Type == "LOCAL_RECOVERY_BLOCKED" && event.Payload["reasonCode"] == "RESUME_BINDING_CHANGED" {
				view.ReasonCode, view.Reason = "RESUME_BINDING_CHANGED", "当前目标与已完成动作的原目标不一致或缺少历史绑定。请核对原对象；系统不会向新目标重复抓取。"
				break
			}
		}
	}
	return view, nil
}

func (a *App) checkRevisionRecovery(ctx context.Context, task *tasks.Task) error {
	if err := a.runner.CheckRecovery(ctx, task.ID); err != nil {
		return err
	}
	if task.State != taskgraph.StatePaused && task.State != taskgraph.StateRecoverableFailure {
		return nil
	}
	runs, err := a.runner.ExecutionHistory(ctx, task.ID)
	if err != nil {
		return err
	}
	for _, run := range runs {
		if run.Status == middleware.StepCompleted && !agent.IsReadOnlyCapability(run.Capability) {
			return errors.New("任务已执行部分物理动作，请先恢复原版本完成任务；不能切换版本重复执行已完成动作")
		}
	}
	return nil
}
