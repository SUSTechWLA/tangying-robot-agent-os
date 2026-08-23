package tasks

import (
	"encoding/json"
	"strconv"
)

// ToolActivitiesFromEvents converts the durable audit stream into the
// transport-neutral input consumed by the user experience projector.
func ToolActivitiesFromEvents(events []TaskEvent, displays map[string]ToolDisplay) []ToolActivityInput {
	activities := make([]ToolActivityInput, 0)
	for _, event := range events {
		if event.Type != "TOOL_ACTIVITY" {
			continue
		}
		toolName, _ := event.Payload["toolName"].(string)
		status, _ := event.Payload["activityStatus"].(string)
		robotID, _ := event.Payload["robotId"].(string)
		stepID, _ := event.Payload["stepId"].(string)
		commandID, _ := event.Payload["commandId"].(string)
		catalogRevision, _ := event.Payload["catalogRevision"].(string)
		arguments, _ := event.Payload["arguments"].(map[string]any)
		arguments = sanitizedActivityArguments(arguments)
		activities = append(activities, ToolActivityInput{
			ToolName: toolName, Status: status, RobotID: robotID, StepID: stepID,
			CommandID: commandID, CatalogRevision: catalogRevision, Arguments: arguments,
			FencingToken: eventUint(event.Payload["fencingToken"]), EvidenceIDs: eventStrings(event.Payload["evidenceIds"]),
			TaskRevision: eventUint(event.Payload["taskRevision"]), AggregateVersion: eventUint(event.Payload["aggregateVersion"]),
			Display: displays[robotID+"\x00"+toolName],
		})
	}
	return activities
}

func sanitizedActivityArguments(arguments map[string]any) map[string]any {
	if len(arguments) == 0 {
		return nil
	}
	result := make(map[string]any, len(arguments))
	for key, value := range arguments {
		if key == "action_chunk" || secretLike(key) {
			continue
		}
		result[key] = value
	}
	if len(result) == 0 {
		return nil
	}
	return result
}

// RecoveryGuidanceFromEvents projects server-authored public recovery text.
// Raw runtime errors are deliberately ignored even if an older producer put
// them in the event payload.
func RecoveryGuidanceFromEvents(events []TaskEvent) *RecoveryGuidance {
	items := make([]RecoveryTimelineItem, 0)
	var latest *TaskEvent
	for index := range events {
		event := &events[index]
		if event.Type != "RECOVERY_ACTIVITY" {
			continue
		}
		known, _ := event.Payload["knownState"].(string)
		action, _ := event.Payload["automaticAction"].(string)
		class, _ := event.Payload["recoveryClass"].(string)
		technicalCode, _ := event.Payload["technicalCode"].(string)
		if known == "" || action == "" || class == "" {
			continue
		}
		items = append(items, RecoveryTimelineItem{
			Class: class, StepID: event.StepID, Attempt: eventUint(event.Payload["attempt"]),
			MaxAttempts: eventUint(event.Payload["maxAttempts"]), KnownState: known,
			Action: action, TechnicalCode: technicalCode,
		})
		latest = event
	}
	if latest == nil {
		return nil
	}
	known, _ := latest.Payload["knownState"].(string)
	safety, _ := latest.Payload["robotSafetyState"].(string)
	action, _ := latest.Payload["automaticAction"].(string)
	return &RecoveryGuidance{
		KnownState: known, RobotSafetyState: safety, AutomaticAction: action,
		Timeline: items,
	}
}

func OverlayActivityStatuses(record *RevisionRecord, activities []ToolActivityInput) {
	latest := map[string]ToolActivityInput{}
	for _, activity := range activities {
		latest[activity.StepID] = activity
	}
	for index := range record.Revision.Steps {
		activity, ok := latest[record.Revision.Steps[index].StepID]
		if !ok {
			continue
		}
		switch activity.Status {
		case "SENDING", "RUNNING":
			record.Revision.Steps[index].Status = StepRunning
		case "AWAITING_EVIDENCE":
			record.Revision.Steps[index].Status = StepAwaitingEvidence
		case "CONFIRMED":
			record.Revision.Steps[index].Status = StepSatisfied
			if len(activity.EvidenceIDs) > 0 {
				record.Revision.Steps[index].HarnessEvidenceIDs = append([]string(nil), activity.EvidenceIDs...)
			}
		case "FAILED":
			record.Revision.Steps[index].Status = StepFailed
		}
	}
}

func eventUint(value any) uint64 {
	switch typed := value.(type) {
	case uint64:
		return typed
	case float64:
		if typed >= 0 {
			return uint64(typed)
		}
	case json.Number:
		parsed, _ := strconv.ParseUint(string(typed), 10, 64)
		return parsed
	}
	return 0
}

func eventStrings(value any) []string {
	switch typed := value.(type) {
	case []string:
		return append([]string(nil), typed...)
	case []any:
		result := make([]string, 0, len(typed))
		for _, item := range typed {
			if text, ok := item.(string); ok {
				result = append(result, text)
			}
		}
		return result
	}
	return nil
}
