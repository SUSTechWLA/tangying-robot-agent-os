package agentruntime

import (
	"encoding/json"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

func failureEventPayload(event agentcontract.Event) map[string]any {
	// Live callers and the replay sweep use the same explicit source fields.
	p := clonePayload(event.Payload)
	if event.ID != "" {
		p["eventId"] = event.ID
	}
	if event.StepID != "" {
		p["stepId"] = event.StepID
	}
	if event.TaskRevision != 0 {
		p["taskRevision"] = event.TaskRevision
	}
	if event.RobotID != "" {
		p["robotId"] = event.RobotID
	}
	if event.CommandID != "" {
		p["commandId"] = event.CommandID
	}
	return p
}

func failedActionBinding(taskID string, p map[string]any) agentcontract.RecoveryBinding {
	b := agentcontract.RecoveryBinding{TaskID: taskID}
	b.StepID, _ = p["stepId"].(string)
	b.RobotID, _ = p["robotId"].(string)
	b.CommandID, _ = p["commandId"].(string)
	b.SourceEventID, _ = p["eventId"].(string)
	// Use the same strict integer decoder as durable recovery payloads.
	if value, ok := p["taskRevision"]; ok {
		decoded, err := agentcontract.DecodeRecoveryBinding(map[string]any{"taskRevision": value})
		if err == nil {
			b.TaskRevision = decoded.TaskRevision
		}
	}
	return b
}

func bindingKey(binding agentcontract.RecoveryBinding) string {
	// Each failure is a separate episode. A new revision/robot/command cannot
	// be suppressed by another execution's cooldown or inherit its diagnosis.
	binding.AnomalyEventID, binding.PlanEventID = "", ""
	wire, _ := json.Marshal(binding)
	return string(wire)
}
