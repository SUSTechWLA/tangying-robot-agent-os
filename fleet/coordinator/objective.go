package coordinator

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func validateDelegatedEvent(task *tasks.Task, node *IntentNode, e tasks.TaskEvent) error {
	c, err := objective.Decode(task.Plan.Capabilities.Calls[node.Index])
	if err != nil {
		return err
	}
	if e.Type == "EDGE_PLAN_CREATED" {
		var calls []capability.Call
		wire, _ := json.Marshal(e.Payload["calls"])
		if err = json.Unmarshal(wire, &calls); err != nil {
			return err
		}
		if len(calls) < 1 || len(calls) > 16 {
			return fmt.Errorf("invalid delegated child plan")
		}
		for _, call := range calls {
			if err = c.Arguments(call.Tool, call.Arguments); err != nil {
				return err
			}
		}
		if e.Payload["objectiveDigest"] != capability.Fingerprint(c) || e.Payload["planDigest"] != objective.PlanDigest(&capability.Plan{RobotID: c.RobotID, CatalogRevision: c.CatalogRevision, Calls: calls}) {
			return ErrIntentIdentityConflict
		}
	}
	return nil
}

// Cloud checks the final predicates against the approved contract and binds
// each completed child to its declared plan and authenticated verification.
// Sensor truth remains a robot/provider trust boundary, not cloud perception.
func verifyDelegatedGoal(task *tasks.Task, node *IntentNode, proof map[string]any) error {
	c, err := objective.Decode(task.Plan.Capabilities.Calls[node.Index])
	if err != nil {
		return err
	}
	var report struct {
		ObjectiveDigest string    `json:"objectiveDigest"`
		Generation      int       `json:"generation"`
		StartedAt       time.Time `json:"startedAt"`
		VerifiedAt      time.Time `json:"verifiedAt"`
		Steps           int       `json:"steps"`
		Reads           int       `json:"reads"`
		Completed       []struct {
			ChildID string `json:"childId"`
			StepID  string `json:"stepId"`
			Binding string `json:"binding"`
			Tool    string `json:"tool"`
			Mutates bool   `json:"mutates"`
		} `json:"completed"`
		Checks []struct {
			Check     objective.Check `json:"check"`
			Value     any             `json:"value"`
			Satisfied bool            `json:"satisfied"`
		} `json:"checks"`
	}
	wire, _ := json.Marshal(proof)
	if err = json.Unmarshal(wire, &report); err != nil {
		return err
	}
	if report.ObjectiveDigest != capability.Fingerprint(c) || report.Generation < 1 || report.Generation > c.Budget.MaxPlans || len(report.Completed) < 1 || len(report.Completed) > c.Budget.MaxSteps || len(report.Checks) != len(c.Completion) {
		return ErrIntentUnverifiable
	}
	if report.StartedAt.IsZero() || report.VerifiedAt.Before(report.StartedAt) || report.VerifiedAt.After(c.ExpiresAt) || report.VerifiedAt.Sub(report.StartedAt) > time.Duration(c.Budget.TimeoutSeconds)*time.Second || report.Steps < len(report.Completed) || report.Steps > c.Budget.MaxSteps || report.Reads < len(c.Completion) || report.Reads > c.Budget.MaxReads {
		return ErrIntentUnverifiable
	}
	for i, check := range report.Checks {
		if !check.Satisfied || !objective.Equal(check.Check, c.Completion[i]) || !objective.Equal(check.Value, c.Completion[i].Equals) {
			return fmt.Errorf("%w: objective postcondition differs", ErrIntentUnverifiable)
		}
	}
	plans := map[string][]capability.Call{}
	verified := map[string]string{}
	for _, e := range task.Events {
		if e.Payload["leaseCommandId"] != node.CommandID || e.Payload["robotId"] != node.Claimed {
			continue
		}
		child, _ := e.Payload["childId"].(string)
		if e.Type == "EDGE_PLAN_CREATED" {
			var calls []capability.Call
			wire, _ := json.Marshal(e.Payload["calls"])
			if json.Unmarshal(wire, &calls) == nil {
				plans[child] = calls
			}
		}
		if e.Type == "EDGE_CHILD_EVENT" && e.Payload["kind"] == "CAPABILITY_VERIFIED" {
			detail, _ := e.Payload["detail"].(map[string]any)
			step, _ := e.Payload["childStepId"].(string)
			tool, _ := detail["tool"].(string)
			if evidence, ok := detail["evidence"].(map[string]any); ok && len(evidence) > 0 {
				verified[child+"/"+step] = tool
			}
		}
	}
	seen := map[string]bool{}
	writes := map[string]int{}
	for _, done := range report.Completed {
		key := done.ChildID + "/" + done.StepID
		if seen[key] || verified[key] != done.Tool {
			return ErrIntentUnverifiable
		}
		seen[key] = true
		matched := false
		var effect string
		for i, call := range plans[done.ChildID] {
			if done.StepID == fmt.Sprintf("rev-%d-cap-%02d", task.CurrentRevision, i+1) && call.Tool == done.Tool && capability.Fingerprint(call) == done.Binding {
				matched = true
				effect = objective.EffectKey(call)
			}
		}
		if !matched {
			return ErrIntentIdentityConflict
		}
		if done.Mutates {
			writes[effect]++
			if writes[effect] > c.ExecutionLimit(done.Tool) {
				return fmt.Errorf("confirmed write repeated")
			}
		}
	}
	// ExpiresAt bounds new work; completion outbox may acknowledge evidence
	// produced before expiry. Event timestamps retain that distinction.
	return nil
}
