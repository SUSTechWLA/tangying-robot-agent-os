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
	ToolName         string         `json:"toolName"`
	Status           string         `json:"status"`
	RobotID          string         `json:"robotId,omitempty"`
	StepID           string         `json:"stepId,omitempty"`
	Arguments        map[string]any `json:"arguments,omitempty"`
	Display          ToolDisplay    `json:"display"`
	CommandID        string         `json:"commandId,omitempty"`
	CatalogRevision  string         `json:"catalogRevision,omitempty"`
	FencingToken     uint64         `json:"fencingToken,omitempty"`
	EvidenceIDs      []string       `json:"evidenceIds,omitempty"`
	TaskRevision     uint64         `json:"taskRevision,omitempty"`
	AggregateVersion uint64         `json:"aggregateVersion,omitempty"`
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
	ToolName         string   `json:"toolName"`
	CommandID        string   `json:"commandId,omitempty"`
	CatalogRevision  string   `json:"catalogRevision,omitempty"`
	FencingToken     uint64   `json:"fencingToken,omitempty"`
	EvidenceIDs      []string `json:"evidenceIds,omitempty"`
	TaskRevision     uint64   `json:"taskRevision,omitempty"`
	AggregateVersion uint64   `json:"aggregateVersion,omitempty"`
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
		Understanding:   humanizeExperienceText(input.Revision.Revision.Understanding),
		ChangePreview:   projectChangePreview(input.Revision.Revision.ChangeSet, input.Revision.Revision.Steps),
		Recovery:        cloneRecovery(input.Recovery),
		AllowedActions:  []string{"update", "view_professional_details"},
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
		fallback := DefaultToolDisplay(activity.ToolName)
		displayName := strings.TrimSpace(activity.Display.DisplayName)
		purpose := strings.TrimSpace(activity.Display.Purpose)
		if displayName == "" {
			displayName = fallback.DisplayName
		}
		if purpose == "" {
			purpose = fallback.Purpose
		}
		if len(activity.Display.SafeArguments) == 0 {
			activity.Display.SafeArguments = fallback.SafeArguments
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
			TaskRevision: activity.TaskRevision, AggregateVersion: activity.AggregateVersion,
		})
		for index := range view.Steps {
			if view.Steps[index].StepID == activity.StepID && view.Steps[index].CapabilityLabel == "" {
				view.Steps[index].CapabilityLabel = displayName
			}
		}
	}
	return view
}

func humanizeExperienceText(value string) string {
	return strings.NewReplacer(
		"right-target-zone", "右侧目标区",
		"left-target-zone", "左侧目标区",
		"handoff-zone", "交接区",
		"target_zone/right_side", "右侧目标区",
		"target_zone/left_side", "左侧目标区",
		"handoff_zone", "交接区",
		"red-block", "红色方块",
	).Replace(value)
}

func projectChangePreview(change ChangeSet, steps []RevisionStep) ChangePreview {
	labels := make(map[string]string, len(steps))
	for _, step := range steps {
		labels[step.StepID] = humanStepExplanation(step)
	}
	used := map[string]bool{}
	projectKnown := func(ids []string, fallback string, matchUnused bool) []string {
		result := make([]string, 0, len(ids))
		for _, id := range ids {
			if label := labels[id]; label != "" {
				result = append(result, label)
				used[id] = true
				continue
			}
			if matchUnused {
				matched := ""
				for _, step := range steps {
					if !used[step.StepID] {
						matched = labels[step.StepID]
						used[step.StepID] = true
						break
					}
				}
				if matched != "" {
					result = append(result, matched)
					continue
				}
			}
			result = append(result, fallback)
		}
		return result
	}
	preview := ChangePreview{}
	preview.Retained = projectKnown(change.Retained, "保留上一版已经确认完成的步骤", false)
	preview.Added = projectKnown(change.Added, "新增一个任务步骤", false)
	preview.Changed = projectKnown(change.Changed, "调整上一版中的任务步骤", true)
	preview.Paused = projectKnown(change.Paused, "机器人会先完成上一版正在执行的安全动作", false)
	return preview
}

// DefaultToolDisplay is the conservative server-owned vocabulary used when
// an older or temporarily disconnected runtime has not advertised display
// metadata. Unknown vendor tools remain hidden from the default user view.
func DefaultToolDisplay(toolName string) ToolDisplay {
	switch strings.TrimSpace(toolName) {
	case "navigation.navigate":
		return ToolDisplay{DisplayName: "移动到指定位置", Purpose: "让机器人安全到达任务位置", SafeArguments: []string{"targetRef"}}
	case "arm.move":
		return ToolDisplay{DisplayName: "调整机械臂", Purpose: "把机械臂移动到合适姿态", SafeArguments: []string{"targetRef"}}
	case "manipulation.pick":
		return ToolDisplay{DisplayName: "拿稳物品", Purpose: "拿起并保持任务中的物品", SafeArguments: []string{"targetRef", "objectId"}}
	case "manipulation.place":
		return ToolDisplay{DisplayName: "放下物品", Purpose: "把物品放到指定区域", SafeArguments: []string{"targetRef", "destinationId", "objectId"}}
	case "observe_scene":
		return ToolDisplay{DisplayName: "查看周围环境", Purpose: "确认机器人周围的物品和位置"}
	case "state.get":
		return ToolDisplay{DisplayName: "检查机器人状态", Purpose: "确认机器人当前是否可以继续工作"}
	case "safety.emergency_stop":
		return ToolDisplay{DisplayName: "立即停止机器人", Purpose: "遇到危险时停止所有动作"}
	case "resolve_targets":
		return ToolDisplay{DisplayName: "确认目标物品", Purpose: "找出任务中的物品和目标位置", SafeArguments: []string{"objectId", "destinationId"}}
	case "plan_grasp":
		return ToolDisplay{DisplayName: "规划拿取动作", Purpose: "选择安全稳定的拿取方式", SafeArguments: []string{"objectId", "destinationId"}}
	case "verify_grasp":
		return ToolDisplay{DisplayName: "确认已经拿稳", Purpose: "检查物品是否真的被机器人拿住", SafeArguments: []string{"objectId"}}
	case "verify_placement":
		return ToolDisplay{DisplayName: "确认已经放好", Purpose: "检查物品是否真的到达目标位置", SafeArguments: []string{"objectId", "destinationId"}}
	case "recover_to_safe_pose":
		return ToolDisplay{DisplayName: "回到安全姿态", Purpose: "让机器人恢复到可继续工作的安全状态"}
	default:
		return ToolDisplay{DisplayName: "机器人能力", Purpose: "机器人正在执行当前步骤"}
	}
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
	resource := humanReference(step.ResourceID)
	destination := "指定位置"
	postcondition := strings.Fields(strings.TrimSpace(step.RequiredPostcondition))
	if len(postcondition) >= 3 {
		destination = humanReference(postcondition[len(postcondition)-1])
	}
	switch strings.TrimSpace(step.Action) {
	case "pick_and_place":
		return fmt.Sprintf("%s把%s放到%s", robot, resource, destination)
	case "fetch":
		return fmt.Sprintf("%s把%s送到%s", robot, resource, destination)
	case "navigate", "move":
		return fmt.Sprintf("%s移动到%s", robot, destination)
	case "observe":
		return robot + "查看周围环境并确认任务状态"
	default:
		if step.RequiredPostcondition != "" {
			return fmt.Sprintf("%s完成%s到%s的任务", robot, resource, destination)
		}
		return robot + "执行当前步骤"
	}
}

func humanReference(reference string) string {
	switch strings.TrimSpace(reference) {
	case "red-block":
		return "红色方块"
	case "handoff-zone":
		return "交接区"
	case "handoff_zone":
		return "交接区"
	case "right-target-zone":
		return "右侧目标区"
	case "target_zone/right_side":
		return "右侧目标区"
	case "left-target-zone":
		return "左侧目标区"
	case "target_zone/left_side":
		return "左侧目标区"
	case "delivery_tray", "delivery-tray":
		return "交付托盘"
	case "":
		return "指定物品"
	default:
		return strings.TrimSpace(reference)
	}
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
