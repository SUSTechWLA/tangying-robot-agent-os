package tasks

import (
	"encoding/json"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

// DecisionContextFor exposes richer event bindings without changing legacy views.
func DecisionContextFor(task Task, stage string, now time.Time) agentcontext.DecisionContext {
	d := ContextFor(task, stage, now)
	if d.DecisionMetadata == nil {
		native := decisionMetadata(task, d)
		d.DecisionMetadata = &native
	}
	return agentcontext.DecisionFrom(d)
}
func decisionMetadata(task Task, d agentcontext.Document) agentcontext.DecisionContext {
	p := agentcontext.DecisionFrom(d)
	if len(task.Events) > 0 {
		var seq int64
		for _, event := range task.Events {
			if int64(event.Sequence) > seq {
				seq = int64(event.Sequence)
			}
		}
		p.Snapshot.LedgerSequence = &seq
	}
	p.Snapshot.OmittedEvents = 0
	p.Snapshot.Complete = len(task.Events) > 0
	for i, event := range task.Events {
		if event.Sequence != uint64(i+1) {
			p.Snapshot.Complete = false
			break
		}
	}
	if !p.Snapshot.Complete {
		p.Missing = append(p.Missing, "/snapshot/ledger_continuity")
	}
	p.Goal["task_state"] = task.State
	p.Goal["revision_state"] = task.RevisionState
	p.Goal["task_approval_record"] = map[string]any{"approved": task.Approved, "approved_by": task.ApprovedBy, "approved_at": task.ApprovedAt, "grants_current_action": false}
	stringValue := func(v any) *string {
		s, ok := v.(string)
		if !ok || s == "" {
			return nil
		}
		return &s
	}
	byEvent := map[string][]int{}
	for i, r := range p.Records {
		parts := strings.SplitN(r.ID, ":", 4)
		if len(parts) >= 3 && parts[0] == "event" {
			prefix := strings.Join(parts[:3], ":")
			byEvent[prefix] = append(byEvent[prefix], i)
		}
	}
	for i, event := range task.Events {
		prefix := contextEventID(event, i)
		for _, index := range byEvent[prefix] {
			r := p.Records[index]
			r.Source = "task_ledger"
			r.StepID = stringValue(contextStepID(event))
			r.AttemptID = stringValue(contextString(event.Payload, "attempt_id", "attemptId", "commandId", "command_id"))
			payload := contextSourcePayload(event.Payload)
			r.Payload = map[string]any{"event_type": event.Type, "message": event.Message, "ledger_occurred_ms": event.OccurredAt.UnixMilli(), "data": payload}
			if raw, ok := event.Payload["state_report_json"].(string); ok && r.Kind == "verification" {
				var report map[string]any
				decoder := json.NewDecoder(strings.NewReader(raw))
				decoder.UseNumber()
				if decoder.Decode(&report) == nil {
					r.Payload = report
					r.Source = "edge_verifier"
					r.SourceVersion = stringValue(report["verifier_version"])
					r.Scope.EpisodeID = stringValue(report["episode_id"])
					r.AttemptID = stringValue(report["attempt_id"])
					r.ClockDomain = "unknown"
				}
			}
			// The legacy ledger has no guaranteed UTC sensor window or robot binding.
			// Preserve the source payload; never fill those gaps from current task state.
			p.Records[index] = r
		}
	}
	for _, name := range []string{"robot_id", "episode_id"} {
		value := p.Scope.RobotID
		if name == "episode_id" {
			value = p.Scope.EpisodeID
		}
		if value == nil {
			p.Missing = append(p.Missing, "/scope/"+name)
		}
	}
	return p
}
