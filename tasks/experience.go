package tasks

import (
	"fmt"
	"strings"
)

type ToolDisplay struct {
	DisplayName   string   `json:"displayName"`
	Purpose       string   `json:"purpose"`
	SafeArguments []string `json:"safeArguments,omitempty"`
}

type ToolActivityInput struct {
	ToolName        string         `json:"toolName"`
	Status          string         `json:"status"`
	RobotID         string         `json:"robotId,omitempty"`
	StepID          string         `json:"stepId,omitempty"`
	Arguments       map[string]any `json:"arguments,omitempty"`
	Display         ToolDisplay    `json:"display"`
	CommandID       string         `json:"commandId,omitempty"`
	CatalogRevision string         `json:"catalogRevision,omitempty"`
	FencingToken    uint64         `json:"fencingToken,omitempty"`
	EvidenceIDs     []string       `json:"evidenceIds,omitempty"`
}

type ToolActivity struct {
	DisplayName   string            `json:"displayName"`
	Purpose       string            `json:"purpose"`
	Status        string            `json:"status"`
	StatusText    string            `json:"statusText"`
	RobotID       string            `json:"robotId,omitempty"`
	StepID        string            `json:"stepId,omitempty"`
	SafeArguments map[string]string `json:"safeArguments,omitempty"`
	EvidenceText  string            `json:"evidenceText,omitempty"`
}

type ProfessionalActivity struct {
	ToolName        string   `json:"toolName"`
	CommandID       string   `json:"commandId,omitempty"`
	CatalogRevision string   `json:"catalogRevision,omitempty"`
	FencingToken    uint64   `json:"fencingToken,omitempty"`
	EvidenceIDs     []string `json:"evidenceIds,omitempty"`
}

type ExperienceStep struct {
	StepID          string     `json:"stepId"`
	Status          StepStatus `json:"status"`
	StatusText      string     `json:"statusText"`
	Explanation     string     `json:"explanation"`
	AssignedRobot   string     `json:"assignedRobot,omitempty"`
	CapabilityLabel string     `json:"capabilityLabel,omitempty"`
	EvidenceText    string     `json:"evidenceText,omitempty"`
}

type ChangePreview struct {
	Retained []string `json:"retained"`
	Changed  []string `json:"changed"`
	Added    []string `json:"added"`
	Paused   []string `json:"paused"`
}

type RecoveryGuidance struct {
	KnownState       string   `json:"knownState"`
	RobotSafetyState string   `json:"robotSafetyState"`
	AutomaticAction  string   `json:"automaticAction"`
	UserActions      []string `json:"userActions,omitempty"`
}

type ProfessionalDetails struct {
	Activities []ProfessionalActivity `json:"activities"`
}

type TaskExperience struct {
	SchemaVersion    string              `json:"schemaVersion"`
	TaskID           string              `json:"taskId"`
	Revision         uint64              `json:"revision"`
	AggregateVersion uint64              `json:"aggregateVersion"`
	Headline         string              `json:"headline"`
	OriginalRequest  string              `json:"originalRequest"`
	Understanding    string              `json:"understanding"`
	UpdateStatus     RevisionStatus      `json:"updateStatus"`
	ChangePreview    ChangePreview       `json:"changePreview"`
	Steps            []ExperienceStep    `json:"steps"`
	Activities       []ToolActivity      `json:"activities"`
	Recovery         *RecoveryGuidance   `json:"recovery,omitempty"`
	AllowedActions   []string            `json:"allowedActions"`
	Professional     ProfessionalDetails `json:"professional"`
}

type ExperienceInput struct {
	Task       *Task
	Revision   RevisionRecord
	Activities []ToolActivityInput
	Recovery   *RecoveryGuidance
}

func ProjectExperience(input ExperienceInput) TaskExperience {
	view := TaskExperience{
		SchemaVersion:   "task.experience.v1",
		UpdateStatus:    input.Revision.Status,
		OriginalRequest: input.Revision.Revision.Request,
		Understanding:   input.Revision.Revision.Understanding,
		ChangePreview: ChangePreview{
			Retained: append([]string(nil), input.Revision.Revision.ChangeSet.Retained...),
			Changed:  append([]string(nil), input.Revision.Revision.ChangeSet.Changed...),
			Added:    append([]string(nil), input.Revision.Revision.ChangeSet.Added...),
			Paused:   append([]string(nil), input.Revision.Revision.ChangeSet.Paused...),
		},
		Recovery:       cloneRecovery(input.Recovery),
		AllowedActions: []string{"update", "view_professional_details"},
	}
	if input.Task != nil {
		view.TaskID = input.Task.ID
		view.Revision = input.Task.CurrentRevision
		view.AggregateVersion = input.Task.AggregateVersion
	}
	if view.TaskID == "" {
		view.TaskID = input.Revision.Revision.TaskID
	}
	if view.Revision == 0 {
		view.Revision = input.Revision.Revision.Revision
	}
	view.Headline = "当前任务"
	if view.Understanding != "" {
		view.Headline = view.Understanding
	}
	for _, step := range input.Revision.Revision.Steps {
		view.Steps = append(view.Steps, ExperienceStep{
			StepID: step.StepID, Status: step.Status, StatusText: humanStepStatus(step.Status),
			Explanation: humanStepExplanation(step), AssignedRobot: step.RobotID,
			EvidenceText: humanEvidence(step),
		})
	}
	for _, activity := range input.Activities {
		displayName := strings.TrimSpace(activity.Display.DisplayName)
		purpose := strings.TrimSpace(activity.Display.Purpose)
		if displayName == "" {
			displayName = "机器人能力"
		}
		if purpose == "" {
			purpose = "机器人正在执行当前步骤"
		}
		projected := ToolActivity{
			DisplayName: displayName, Purpose: purpose, Status: activity.Status,
			StatusText: humanActivityStatus(activity.Status), RobotID: activity.RobotID,
			StepID: activity.StepID, SafeArguments: safeArgumentProjection(activity.Arguments, activity.Display.SafeArguments),
		}
		if len(activity.EvidenceIDs) > 0 {
			projected.EvidenceText = "环境已经确认动作结果"
		}
		view.Activities = append(view.Activities, projected)
		view.Professional.Activities = append(view.Professional.Activities, ProfessionalActivity{
			ToolName: activity.ToolName, CommandID: activity.CommandID, CatalogRevision: activity.CatalogRevision,
			FencingToken: activity.FencingToken, EvidenceIDs: append([]string(nil), activity.EvidenceIDs...),
		})
	}
	return view
}

func safeArgumentProjection(arguments map[string]any, allowed []string) map[string]string {
	if len(arguments) == 0 || len(allowed) == 0 {
		return nil
	}
	projected := map[string]string{}
	for _, name := range allowed {
		name = strings.TrimSpace(name)
		if name == "" || secretLike(name) {
			continue
		}
		value, exists := arguments[name]
		if !exists {
			continue
		}
		projected[name] = fmt.Sprint(value)
	}
	if len(projected) == 0 {
		return nil
	}
	return projected
}

func secretLike(name string) bool {
	lower := strings.ToLower(name)
	for _, fragment := range []string{"password", "secret", "token", "bearer", "credential", "private", "api_key", "apikey"} {
		if strings.Contains(lower, fragment) {
			return true
		}
	}
	return false
}

func humanActivityStatus(status string) string {
	switch status {
	case "SENDING":
		return "正在发送动作"
	case "RUNNING":
		return "机器人正在执行"
	case "AWAITING_EVIDENCE":
		return "正在确认动作结果"
	case "CONFIRMED":
		return "环境已经确认完成"
	case "FAILED":
		return "动作需要处理"
	default:
		return "等待机器人反馈"
	}
}

func humanStepStatus(status StepStatus) string {
	switch status {
	case StepSatisfied:
		return "已完成"
	case StepRunning:
		return "正在执行"
	case StepAwaitingEvidence:
		return "正在确认结果"
	case StepReady:
		return "准备执行"
	case StepFailed:
		return "需要处理"
	case StepCancelledByRevision:
		return "已由新任务替换"
	default:
		return "等待执行"
	}
}

func humanStepExplanation(step RevisionStep) string {
	robot := "机器人"
	if step.RobotID != "" {
		robot = strings.TrimPrefix(step.RobotID, "robot-") + "号机器人"
	}
	return robot + "执行当前步骤"
}

func humanEvidence(step RevisionStep) string {
	if step.Status == StepSatisfied && len(step.HarnessEvidenceIDs) > 0 {
		return "环境已经确认这一步完成"
	}
	if step.Status == StepAwaitingEvidence {
		return "动作已发送，正在检查环境是否真的改变"
	}
	return "等待环境证据"
}

func cloneRecovery(recovery *RecoveryGuidance) *RecoveryGuidance {
	if recovery == nil {
		return nil
	}
	clone := *recovery
	clone.UserActions = append([]string(nil), recovery.UserActions...)
	return &clone
}
