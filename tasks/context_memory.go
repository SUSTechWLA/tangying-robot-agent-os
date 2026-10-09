package tasks

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

// contextActionMemory is a lossless lifecycle reduction, not a model summary or
// an execution authority. Recorded completion does not establish a current
// physical condition, and an unbound receipt never closes another attempt.
type contextActionMemory struct {
	Revision         int      `json:"source_revision"`
	RobotID          string   `json:"robot_id"`
	StepID           string   `json:"step_id"`
	CommandID        string   `json:"command_id"`
	Tool             string   `json:"tool"`
	State            string   `json:"recorded_state"`
	Unresolved       bool     `json:"unresolved_dispatch"`
	OutcomeUnknown   bool     `json:"outcome_unknown"`
	MutatesWorld     *bool    `json:"mutates_world"`
	EvidenceIDs      []string `json:"evidence_ids"`
	SourceIDs        []string `json:"source_event_ids"`
	EventID          string   `json:"event_id,omitempty"`
	IdentityConflict bool     `json:"event_identity_conflict,omitempty"`
	lastEvent        int
}

func contextEventID(event TaskEvent, index int) string {
	return fmt.Sprintf("event:%d:%d", event.Sequence, index)
}

func contextString(payload map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := payload[key].(string); ok && value != "" {
			return value
		}
	}
	return ""
}

func contextStepID(event TaskEvent) string {
	payloadStep := contextString(event.Payload, "stepId", "step_id")
	if event.StepID != "" {
		if payloadStep != "" && payloadStep != event.StepID {
			return "" // Conflicting identities cannot close a previous action.
		}
		return event.StepID
	}
	return payloadStep
}

func contextRevision(value any) int {
	wire, err := json.Marshal(value)
	if err != nil {
		return 0
	}
	// No float truncation or signed/overflow conversion: an invalid binding is
	// unknown, never the identity of a different revision.
	number, err := strconv.ParseUint(string(wire), 10, 64)
	if err != nil || number > uint64(math.MaxInt) {
		return 0
	}
	return int(number)
}

func contextEventScope(taskID string, event TaskEvent) agentcontext.Scope {
	scope := agentcontext.Scope{TaskID: taskID, RobotID: contextString(event.Payload, "robotId", "robot_id")}
	explicit := false
	for _, key := range []string{"taskRevision", "plan_revision", "revision"} {
		if value, exists := event.Payload[key]; exists {
			revision := contextRevision(value)
			if revision == 0 || (explicit && revision != scope.PlanRevision) {
				scope.PlanRevision = 0
				return scope
			}
			explicit, scope.PlanRevision = true, revision
		}
	}
	// These two producer-owned identities encode revision explicitly. Plain
	// legacy step names have no revision; never fill it from task.CurrentRevision.
	step := contextStepID(event)
	for _, binding := range []struct{ prefix, separator string }{{"rev-", "-cap-"}, {"revision-", "/"}} {
		if suffix, ok := strings.CutPrefix(step, binding.prefix); ok {
			if revision, _, ok := strings.Cut(suffix, binding.separator); ok {
				n, err := strconv.ParseUint(revision, 10, 64)
				if err == nil && n <= uint64(math.MaxInt) {
					if explicit && scope.PlanRevision != int(n) {
						scope.PlanRevision = 0
					} else {
						scope.PlanRevision = int(n)
					}
				}
			}
		}
	}
	return scope
}

// Remove previous model inputs at every nesting level. Do not summarize their
// summaries: all memory is regenerated directly from original durable events.
func contextSourceValue(value any) any {
	switch value := value.(type) {
	case map[string]any:
		return contextSourcePayload(value)
	case []any:
		result := make([]any, len(value))
		for i, item := range value {
			result[i] = contextSourceValue(item)
		}
		return result
	default:
		return value
	}
}

func contextSourcePayload(payload map[string]any) map[string]any {
	result := make(map[string]any, len(payload))
	for key, value := range payload {
		if key != "agent_context" && key != "decision_rounds" {
			result[key] = contextSourceValue(value)
		}
	}
	return result
}

func contextActionStatus(event TaskEvent) string {
	switch event.Type {
	case "TOOL_ACTIVITY", "action.executed":
		return contextString(event.Payload, "activityStatus")
	case "CAPABILITY_CALL":
		return "RUNNING"
	case "CAPABILITY_RECEIPT":
		return "AWAITING_EVIDENCE"
	case "CAPABILITY_VERIFIED":
		return "CONFIRMED"
	case "CAPABILITY_FAILED":
		return "FAILED"
	case "POLICY_PREPARATION_FAILED", "action.preparation_failed":
		return "PREPARATION_FAILED"
	default:
		return ""
	}
}

func appendContextMemory(d *agentcontext.Document, task Task) {
	intent, _ := json.Marshal(task.Intent)
	d.Records = append(d.Records, agentcontext.Record{ID: "task:intent", Kind: "guard", Scope: d.Scope,
		Statement: "当前持久意图及约束（包括子任务约束，未额外授予权限）：" + string(intent)})
	d.Constraints = append(d.Constraints, "阶段记忆由完整持久事件确定性重建；完成回执不是当前物理事实。未决动作必须按同一命令身份对账，不能用不同尝试或不同版本的成功覆盖。")

	var actions []*contextActionMemory
	byCommand := map[string]*contextActionMemory{}
	// Check the whole input before reducing it: a later conflicting use of an
	// event ID invalidates the earlier variant too, including a completion claim.
	eventVariants := map[string][]int{}
	eventContents := map[string]map[string]bool{}
	conflictedEvents := map[string]bool{}
	for i, event := range task.Events {
		id := contextString(event.Payload, "eventId")
		if id == "" || contextActionStatus(event) == "" {
			continue
		}
		content := event.Type + ":" + contextEventContent(event, i)
		if eventContents[id][content] {
			continue
		}
		for _, prior := range eventVariants[id] {
			if !contextSameEvent(task.Events[prior], event) {
				conflictedEvents[id] = true
			}
		}
		if eventContents[id] == nil {
			eventContents[id] = map[string]bool{}
		}
		eventContents[id][content] = true
		eventVariants[id] = append(eventVariants[id], i)
	}
	seenEvents := map[string]map[string]bool{}
	seenEquivalent := map[string]bool{}
	for i, event := range task.Events {
		status := contextActionStatus(event)
		if status == "" {
			continue
		}
		eventID := contextString(event.Payload, "eventId")
		identityConflict := conflictedEvents[eventID]
		if eventID != "" {
			if !identityConflict && seenEquivalent[eventID] {
				continue // Every retained variant was checked above for equivalence.
			}
			content := contextEventContent(event, i)
			if seenEvents[eventID][content] {
				continue
			}
			if seenEvents[eventID] == nil {
				seenEvents[eventID] = map[string]bool{}
			}
			seenEvents[eventID][content] = true
			seenEquivalent[eventID] = true
		}
		if !identityConflict && i > 0 && contextCapabilityMirror(task.Events[i-1], event) {
			continue
		}
		scope := contextEventScope(task.ID, event)
		step, tool := contextStepID(event), contextString(event.Payload, "toolName", "tool")
		command := contextString(event.Payload, "commandId", "command_id", "attempt_id", "attemptId")
		group := fmt.Sprintf("%d\x00%s\x00%s\x00%s", scope.PlanRevision, scope.RobotID, step, tool)
		key := group + "\x00" + command
		item := byCommand[key]
		if command == "" || step == "" || identityConflict {
			item = nil
		}
		if item == nil {
			item = &contextActionMemory{Revision: scope.PlanRevision, RobotID: scope.RobotID, StepID: step, CommandID: command, Tool: tool}
			actions = append(actions, item)
			if command != "" && !identityConflict {
				byCommand[key] = item
			}
		}
		item.SourceIDs = append(item.SourceIDs, contextEventID(event, i))
		item.lastEvent = i
		if mutates, ok := event.Payload["mutatesWorld"].(bool); ok {
			item.MutatesWorld = &mutates
		}
		if refs := eventStrings(event.Payload["evidenceIds"]); len(refs) > 0 {
			item.EvidenceIDs = append([]string(nil), refs...)
		}
		unknown, _ := event.Payload["outcomeUnknown"].(bool)
		switch status {
		case "SENDING", "RUNNING":
			item.State, item.Unresolved = "DISPATCH_RECORDED", true
		case "AWAITING_EVIDENCE":
			item.State, item.Unresolved = "RECEIPT_AWAITING_EVIDENCE", true
		case "CONFIRMED":
			// This is a historical closure claim only, never VERIFIED now.
			item.State, item.Unresolved, item.OutcomeUnknown = "COMPLETION_RECORDED", false, false
		case "FAILED", "PREPARATION_FAILED":
			rejected, _ := event.Payload["rejected"].(bool)
			noDispatch, explicit := event.Payload["physicalDispatched"].(bool)
			readOnly := item.MutatesWorld != nil && !*item.MutatesWorld
			knownNoDispatch := rejected || (explicit && !noDispatch)
			if knownNoDispatch && !item.OutcomeUnknown {
				item.State, item.Unresolved = "REJECTED_BEFORE_DISPATCH", false
			} else if readOnly && !unknown && !item.OutcomeUnknown {
				item.State, item.Unresolved = "READ_FAILED", false
			} else {
				item.State, item.Unresolved, item.OutcomeUnknown = "OUTCOME_UNKNOWN", true, true
			}
		}
		if unknown {
			item.State, item.Unresolved, item.OutcomeUnknown = "OUTCOME_UNKNOWN", true, true
		}
		if identityConflict {
			// Never place conflicting variants in byCommand: even a later normal
			// completion cannot silently erase this source-integrity failure.
			item.EventID, item.IdentityConflict = eventID, true
			item.State, item.Unresolved, item.OutcomeUnknown = "EVENT_ID_CONFLICT", true, true
		}
	}
	completed, unresolved := 0, 0
	var current *contextActionMemory
	stepStates := map[int]*contextActionMemory{}
	for i, item := range actions {
		if item.State == "COMPLETION_RECORDED" {
			completed++
		}
		if item.Unresolved {
			unresolved++
		}
		wire, _ := json.Marshal(item)
		d.Records = append(d.Records, agentcontext.Record{ID: fmt.Sprintf("memory:action:%d", i), Kind: "guard",
			Scope: agentcontext.Scope{TaskID: task.ID, RobotID: item.RobotID, PlanRevision: item.Revision}, EvidenceIDs: item.EvidenceIDs,
			Statement: "持久动作阶段记录（历史回执不证明当前物理状态）：" + string(wire)})
		if item.Revision == 0 || item.Revision != int(task.CurrentRevision) {
			continue
		}
		if d.Scope.RobotID != "" && item.RobotID != d.Scope.RobotID {
			continue
		}
		if contextMemoryLater(current, item) {
			current = item
		}
		for index := range d.Steps {
			step := &d.Steps[index]
			if step.ID != item.StepID && fmt.Sprintf("revision-%d/%s", item.Revision, step.ID) != item.StepID {
				continue
			}
			if contextMemoryLater(stepStates[index], item) {
				stepStates[index] = item
			}
		}
	}
	if current != nil {
		d.CurrentStep = current.StepID
	}
	for index, item := range stepStates {
		d.Steps[index].State = item.State
	}
	d.Records = append(d.Records, agentcontext.Record{ID: "memory:coverage", Kind: "guard", Scope: d.Scope,
		Statement: fmt.Sprintf("确定性阶段记忆版本=task-ledger-memory.v1；输入事件=%d；历史完成记录=%d；未决派发记录=%d；当前revision=%d；来源缺失的revision保持0，不继承当前版本。旧revision的完整请求及约束以revision-source记录为准；未导入的版本保持未知。", len(task.Events), completed, unresolved, task.CurrentRevision)})
}

func contextEventContent(event TaskEvent, index int) string {
	kind := event.Type
	switch kind {
	case "action.executed":
		kind = "TOOL_ACTIVITY"
	case "action.preparation_failed":
		kind = "POLICY_PREPARATION_FAILED"
	}
	payload := make(map[string]any, len(event.Payload))
	for key, value := range event.Payload {
		// These fields are added by the documented ledger/bus bridge and are
		// transport attribution, not a change to the original action fact.
		switch key {
		case "agent", "agentVersion", "causationId", "correlationId":
			continue
		}
		payload[key] = value
	}
	wire, err := json.Marshal(struct {
		Type, StepID, Message string
		Payload               map[string]any
	}{kind, event.StepID, event.Message, payload})
	if err != nil {
		return fmt.Sprintf("unrenderable-event:%d", index)
	}
	return agentcontext.Hash(string(wire))
}

func contextSameEvent(left, right TaskEvent) bool {
	if contextEventContent(left, 0) == contextEventContent(right, 1) {
		return true
	}
	// The legacy runner publishes these two views of one action. Only this
	// known pair may add the documented supplemental fields on one side; a
	// changed shared value (including these fields) remains an identity conflict.
	if !((left.Type == "TOOL_ACTIVITY" && right.Type == "action.executed") || (right.Type == "TOOL_ACTIVITY" && left.Type == "action.executed")) {
		return false
	}
	copyPayload := func(payload map[string]any) map[string]any {
		result := make(map[string]any, len(payload))
		for key, value := range payload {
			result[key] = value
		}
		return result
	}
	left.Payload, right.Payload = copyPayload(left.Payload), copyPayload(right.Payload)
	for _, key := range []string{"safetyLevel", "receiptObservationId", "evidenceSource"} {
		_, leftHas := left.Payload[key]
		_, rightHas := right.Payload[key]
		if leftHas != rightHas {
			delete(left.Payload, key)
			delete(right.Payload, key)
		}
	}
	return contextEventContent(left, 0) == contextEventContent(right, 1)
}

func contextMemoryLater(previous, candidate *contextActionMemory) bool {
	if previous == nil {
		return true
	}
	// A later unrelated success or pending attempt cannot erase an unresolved
	// unknown outcome. Among equally certain records use durable ledger order.
	rank := func(item *contextActionMemory) int {
		if item.OutcomeUnknown {
			return 2
		}
		if item.Unresolved {
			return 1
		}
		return 0
	}
	if rank(previous) != rank(candidate) {
		return rank(candidate) > rank(previous)
	}
	return candidate.lastEvent > previous.lastEvent
}

func contextCapabilityMirror(previous, event TaskEvent) bool {
	if !strings.HasPrefix(previous.Type, "CAPABILITY_") || event.Type != "TOOL_ACTIVITY" || contextActionStatus(previous) != contextActionStatus(event) || contextStepID(previous) != contextStepID(event) {
		return false
	}
	left, _ := json.Marshal(previous.Payload)
	right := make(map[string]any, len(event.Payload))
	for key, value := range event.Payload {
		if key != "toolName" && key != "stepId" && key != "activityStatus" && key != "eventId" {
			right[key] = value
		}
	}
	wire, _ := json.Marshal(right)
	return string(left) == string(wire)
}
