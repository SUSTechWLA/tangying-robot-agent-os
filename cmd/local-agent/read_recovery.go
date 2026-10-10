package main

import (
	"context"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The executor waits at a failed read; ops diagnoses it and recovery performs
// its catalogue-scoped diagnostic independently. Only a new, matching, verified
// automatic history read releases this wait. It grants no physical authority.
func awaitReadRecovery(service *tasks.Service) func(context.Context, string, string, string, error) error {
	return func(ctx context.Context, taskID, stepID, tool string, _ error) error {
		ticker := time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()
		for {
			task, err := service.Get(ctx, taskID)
			if err != nil {
				return err
			}
			if err := recoveryRequestStillCurrent(task, stepID); err != nil {
				return err
			}
			ready, err := readRecoveryCompleted(task.Events, taskID, stepID, tool)
			if err != nil || ready {
				return err
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-ticker.C:
			}
		}
	}
}

func readRecoveryCompleted(events []tasks.TaskEvent, taskID, step, tool string) (bool, error) {
	start := -1
	for i, event := range events {
		if event.StepID == step && event.Type == "CAPABILITY_READ_RECOVERY_REQUESTED" {
			start = i
		}
	}
	if start < 0 {
		return false, fmt.Errorf("durable recovery request missing")
	}
	request, err := recoverySourceBinding(taskID, events[start])
	if err != nil || request.TaskRevision == 0 || request.RobotID == "" || request.CommandID == "" || request.StepID != step || events[start].Payload["tool"] != tool {
		return false, fmt.Errorf("durable recovery request has unknown or conflicting execution binding")
	}
	failures := map[string]agentcontract.RecoveryBinding{}
	anomalies := map[string]agentcontract.RecoveryBinding{}
	plans := map[string]agentcontract.RecoveryBinding{}
	for _, event := range events[start+1:] {
		p := event.Payload
		id, _ := p["eventId"].(string)
		if event.Type == "TOOL_ACTIVITY" && p["activityStatus"] == "FAILED" && p["toolName"] == tool && p["mutatesWorld"] == false && p["outcomeUnknown"] != true {
			source, decodeErr := recoverySourceBinding(taskID, event)
			if decodeErr == nil && id != "" && sameRecoveryExecution(request, source) {
				source.SourceEventID = id
				failures[id] = source
			}
			continue
		}
		if p["protocolVersion"] != agentcontract.EventProtocolV1 {
			continue
		}
		binding, decodeErr := agentcontract.DecodeRecoveryBinding(p["binding"])
		if decodeErr != nil || !binding.Complete() || !sameRecoveryExecution(request, binding) {
			continue
		}
		source, found := failures[binding.SourceEventID]
		if !found || !sameRecoveryExecution(source, binding) {
			continue
		}
		if event.Type == agentcontract.TopicOpsAnomalyDetected && p["agent"] == "ops" && id != "" && binding.AnomalyEventID == id && p["causationId"] == binding.SourceEventID {
			anomalies[id] = binding
			continue
		}
		anomaly, found := anomalies[binding.AnomalyEventID]
		if !found || anomaly.SourceEventID != binding.SourceEventID {
			continue
		}
		planID, _ := p["planId"].(string)
		if event.Type == agentcontract.TopicOpsRecoveryPlan && p["agent"] == "recovery" && id != "" && binding.PlanEventID == id && p["causationId"] == binding.AnomalyEventID && p["trigger"] == "ANOMALY_ACTION_FAILED@"+tool && recoveryPlanStep(p) == step {
			if p["verdict"] == string(agentcontract.VerdictEscalate) {
				return false, fmt.Errorf("recovery agent escalated this read: %v", p["escalateReason"])
			}
			if p["verdict"] == string(agentcontract.VerdictPlan) && planID != "" {
				plans[planID] = binding
			}
		}
		if event.Type == agentcontract.TopicOpsRecoveryExecuted && p["agent"] == "recovery-executor" && p["actionId"] == "execution.read-history" && p["operatorApproved"] != true {
			planned, exists := plans[planID]
			if !exists || planned != binding || p["causationId"] != binding.PlanEventID {
				continue
			}
			if p["executed"] == true && p["verified"] == true && (p["failed"] == nil || p["failed"] == "") {
				return true, nil
			}
			return false, fmt.Errorf("automatic diagnostic read did not verify")
		}
	}
	return false, nil
}

func recoverySourceBinding(taskID string, event tasks.TaskEvent) (agentcontract.RecoveryBinding, error) {
	p := make(map[string]any, len(event.Payload)+1)
	for key, value := range event.Payload {
		p[key] = value
	}
	if id, exists := p["taskId"]; exists && id != taskID {
		return agentcontract.RecoveryBinding{}, fmt.Errorf("conflicting task identity")
	}
	p["taskId"] = taskID // The owning ledger is authoritative for task identity.
	if id, exists := p["stepId"]; exists && id != event.StepID {
		return agentcontract.RecoveryBinding{}, fmt.Errorf("conflicting step identity")
	}
	p["stepId"] = event.StepID
	return agentcontract.DecodeRecoveryBinding(p)
}

func sameRecoveryExecution(a, b agentcontract.RecoveryBinding) bool {
	return a.TaskID == b.TaskID && a.TaskRevision == b.TaskRevision && a.RobotID == b.RobotID && a.StepID == b.StepID && a.CommandID == b.CommandID
}

func recoveryRequestStillCurrent(task *tasks.Task, step string) error {
	for i := len(task.Events) - 1; i >= 0; i-- {
		event := task.Events[i]
		if event.Type != "CAPABILITY_READ_RECOVERY_REQUESTED" || event.StepID != step {
			continue
		}
		binding, err := recoverySourceBinding(task.ID, event)
		if err != nil {
			return err
		}
		if binding.TaskRevision != task.CurrentRevision || task.Plan == nil || task.Plan.Capabilities == nil || binding.RobotID != task.Plan.Capabilities.RobotID {
			return fmt.Errorf("read recovery refused: task execution binding changed")
		}
		return nil
	}
	return fmt.Errorf("durable recovery request missing")
}

func recoveryPlanStep(payload map[string]any) string {
	trail, _ := payload["trail"].(map[string]any)
	steps, _ := trail["steps"].([]any)
	for _, raw := range steps {
		step, _ := raw.(map[string]any)
		if step["name"] == "recovery.start" {
			facts, _ := step["findings"].(map[string]any)
			id, _ := facts["stepId"].(string)
			return id
		}
	}
	return ""
}
