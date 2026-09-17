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

// eventBool reads a boolean that may have survived a JSON round trip. A missing
// or unreadable value is false: the safe default for a field that gates advice is
// "no prohibition stated", because inventing a prohibition would block work the
// system did not actually forbid, and inventing permission is not possible here
// since the field is only ever read for display.
func eventBool(value any) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		return typed == "true"
	default:
		return false
	}
}

// eventFloat reads a numeric field that may have survived a JSON round trip as
// any of the numeric types. An unreadable value is zero, which for a confidence
// means "not stated" rather than "certain".
func eventFloat(value any) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case float32:
		return float64(typed)
	case int:
		return float64(typed)
	case int64:
		return float64(typed)
	case uint64:
		return float64(typed)
	default:
		return 0
	}
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

// AgentEventsFromEvents projects the agent-authored entries of the durable
// ledger for a replay.
//
// This is what makes an observer's work visible where a person already looks.
// An OpsAgent finding is appended to the same per-task ledger as a tool
// activity, under its topic name, so the replay reads it out here rather than a
// second store having to be consulted and reconciled.
//
// Only events that carry an agent attribution are projected. A tool activity or
// a state change is execution, not an agent's statement about it, and mixing the
// two would make the replay attribute an observer's diagnosis to the robot.
func AgentEventsFromEvents(events []TaskEvent) []AgentEventView {
	projected := make([]AgentEventView, 0)
	for _, event := range events {
		if !isAgentEvent(event) {
			continue
		}
		agent, _ := event.Payload["agent"].(string)
		version, _ := event.Payload["agentVersion"].(string)
		view := AgentEventView{
			Sequence: event.Sequence, OccurredAt: event.OccurredAt, Topic: event.Type,
			Agent: agent, AgentVersion: version, StepID: event.StepID,
			Summary: agentEventSummary(event), Code: agentEventCode(event),
			Severity: agentEventSeverity(event), EvidenceIDs: eventStrings(event.Payload["evidence"]),
			RecommendedActions:      eventStrings(event.Payload["recommendedActions"]),
			AutomaticRetryForbidden: eventBool(event.Payload["automaticRetryForbidden"]),
			MissingEvidence:         eventStrings(event.Payload["missingEvidence"]),
			Confidence:              eventFloat(event.Payload["confidence"]),
			Payload:                 safeAgentPayload(event.Payload),
		}
		projected = append(projected, view)
	}
	return projected
}

// isAgentEvent reports whether a ledger entry was written by an agent.
//
// The attribution is the field an agent's publisher stamps, not the topic name,
// so a future agent's topics are visible without this function being taught
// about them: adding an agent must not require changing the replay.
func isAgentEvent(event TaskEvent) bool {
	if event.Payload == nil {
		return false
	}
	agent, ok := event.Payload["agent"].(string)
	return ok && agent != ""
}

// safeAgentPayload drops the transport bookkeeping from what a reader sees,
// keeping the structured facts. The attribution fields are lifted into their own
// view fields, so repeating them inside the payload would be noise.
func safeAgentPayload(payload map[string]any) map[string]any {
	if len(payload) == 0 {
		return nil
	}
	result := make(map[string]any, len(payload))
	for key, value := range payload {
		switch key {
		case "agent", "agentVersion", "causationId", "correlationId":
			continue
		default:
			result[key] = value
		}
	}
	if len(result) == 0 {
		return nil
	}
	return result
}

// agentEventSummary reads the human sentence from whichever payload shape the
// event carries. It falls back to the event's own message rather than inventing
// one: a summary that does not come from the event is a statement the replay
// cannot support.
func agentEventSummary(event TaskEvent) string {
	if event.Payload != nil {
		for _, key := range []string{"message", "summary", "action", "reason", "detail"} {
			if text, ok := event.Payload[key].(string); ok && text != "" {
				return humanizeExperienceText(text)
			}
		}
	}
	return humanizeExperienceText(event.Message)
}

func agentEventCode(event TaskEvent) string {
	if event.Payload == nil {
		return ""
	}
	// A finding carries its own code; a hypothesis carries the failure class it
	// was classified under. Both are worth showing.
	for _, key := range []string{"code", "reasonCode", "category", "reason", "capability"} {
		if text, ok := event.Payload[key].(string); ok && text != "" {
			return text
		}
	}
	return ""
}

func agentEventSeverity(event TaskEvent) string {
	if event.Payload == nil {
		return ""
	}
	if text, ok := event.Payload["severity"].(string); ok {
		return text
	}
	if text, ok := event.Payload["current"].(string); ok {
		return text
	}
	return ""
}
