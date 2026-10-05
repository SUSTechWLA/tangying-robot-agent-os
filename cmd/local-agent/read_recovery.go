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
			ready, err := readRecoveryCompleted(task.Events, stepID, tool)
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

func readRecoveryCompleted(events []tasks.TaskEvent, step, tool string) (bool, error) {
	start := -1
	for i, event := range events {
		if event.StepID == step && event.Type == "CAPABILITY_READ_RECOVERY_REQUESTED" {
			start = i
		}
	}
	if start < 0 {
		return false, fmt.Errorf("durable recovery request missing")
	}
	plans := map[string]bool{}
	for _, event := range events[start+1:] {
		p := event.Payload
		id, _ := p["planId"].(string)
		if event.Type == agentcontract.TopicOpsRecoveryPlan && p["trigger"] == "ANOMALY_ACTION_FAILED@"+tool && recoveryPlanStep(p) == step {
			if p["verdict"] == string(agentcontract.VerdictEscalate) {
				return false, fmt.Errorf("recovery agent escalated this read: %v", p["escalateReason"])
			}
			if p["verdict"] == string(agentcontract.VerdictPlan) && id != "" {
				plans[id] = true
			}
		}
		if event.Type == agentcontract.TopicOpsRecoveryExecuted && plans[id] && p["actionId"] == "execution.read-history" && p["operatorApproved"] != true {
			if p["executed"] == true && p["verified"] == true && (p["failed"] == nil || p["failed"] == "") {
				return true, nil
			}
			return false, fmt.Errorf("automatic diagnostic read did not verify")
		}
	}
	return false, nil
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
