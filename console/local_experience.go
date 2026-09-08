package console

import (
	"fmt"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Local Runner expands each natural-language intent into manipulation tool
// steps. These identifiers differ from the immutable intent-000/fingerprint
// plan IDs; projecting a single confirmed tool as a completed intent is wrong.
var localExperienceTools = map[string]string{
	"observe": "observe_scene", "resolve": "resolve_targets", "navigate": "navigation.navigate",
	"observe_after_navigation": "observe_scene", "plan_grasp": "plan_grasp",
	"pick": "manipulation.pick", "verify_grasp": "verify_grasp",
	"place": "manipulation.place", "verify_place": "verify_placement",
}

func projectLocalTaskExperience(task *tasks.Task, record tasks.RevisionRecord, activities []tasks.ToolActivityInput) tasks.TaskExperience {
	// This is a read-only view, not a change to execution/recovery state.
	record.Revision.Steps = append([]tasks.RevisionStep(nil), record.Revision.Steps...)
	current := make([]tasks.ToolActivityInput, 0, len(activities))
	for _, activity := range activities {
		revision := activity.TaskRevision
		if revision == 0 {
			revision = 1 // Legacy events belong only to the original revision.
		}
		if revision == record.Revision.Revision {
			current = append(current, activity)
		}
	}
	tasks.OverlayActivityStatuses(&record, current)
	labels, evidence := map[int]string{}, map[int]string{}
	count := len(record.Revision.Intent.Tasks())
	for index := range record.Revision.Steps {
		step := &record.Revision.Steps[index]
		if step.IntentIndex < 0 || step.IntentIndex >= count || !strings.HasPrefix(step.StepID, fmt.Sprintf("intent-%03d/", step.IntentIndex)) {
			continue
		}
		prefix := ""
		if count > 1 {
			prefix = fmt.Sprintf("task%02d-", step.IntentIndex+1)
		}
		confirmed := false
		for _, activity := range current {
			logical, _, _ := strings.Cut(activity.StepID, "/resume-read/")
			if !strings.HasPrefix(logical, prefix) || (step.RobotID != "" && activity.RobotID != step.RobotID) {
				continue
			}
			stage := strings.TrimPrefix(logical, prefix)
			if tool, known := localExperienceTools[stage]; !known || tool != activity.ToolName {
				continue
			}
			physical := stage == "navigate" || stage == "pick" || stage == "place"
			// Resume refreshes prior reads. Keep a completed placement until an
			// actual new physical action or failed placement recheck contradicts it.
			if confirmed && !physical && !(stage == "verify_place" && activity.Status == "FAILED") {
				continue
			}
			switch activity.Status {
			case "SENDING", "RUNNING", "AWAITING_EVIDENCE":
				step.Status = tasks.StepRunning
				if stage == "verify_place" {
					step.Status = tasks.StepAwaitingEvidence
				}
				confirmed = false
			case "CONFIRMED":
				confirmed = false
				step.Status = tasks.StepRunning
				if stage == "place" || stage == "verify_place" {
					step.Status = tasks.StepAwaitingEvidence
				}
				if stage == "verify_place" {
					for _, capture := range activity.EvidenceIDs {
						if strings.TrimSpace(capture) != "" {
							step.Status, confirmed = tasks.StepSatisfied, true
							break
						}
					}
				}
			case "FAILED":
				step.Status, confirmed = tasks.StepFailed, false
			default:
				continue
			}
			labels[index] = tasks.DefaultToolDisplay(activity.ToolName).DisplayName
			switch step.Status {
			case tasks.StepRunning:
				evidence[index] = "正在执行，完成后会观测确认结果"
			case tasks.StepAwaitingEvidence:
				evidence[index] = "正在检查物品是否放到目标位置"
			case tasks.StepSatisfied:
				evidence[index] = "已通过放置观测确认"
			case tasks.StepFailed:
				evidence[index] = "这一步未通过确认，请查看执行记录"
			}
		}
	}
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: task, Revision: record, Activities: activities,
		Recovery: tasks.BasicRecoveryGuidance(task.RevisionState, activities),
	})
	for index, label := range labels {
		view.Steps[index].CapabilityLabel, view.Steps[index].EvidenceText = label, evidence[index]
	}
	return view
}
