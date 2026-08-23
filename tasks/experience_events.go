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
