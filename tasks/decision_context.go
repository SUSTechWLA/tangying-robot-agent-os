package tasks

import (
	"encoding/json"
	"fmt"
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
	p.Snapshot.OmittedEvents = len(task.Events) - 64
	if p.Snapshot.OmittedEvents < 0 {
		p.Snapshot.OmittedEvents = 0
	}
	p.Snapshot.Complete = p.Snapshot.OmittedEvents == 0
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
	byID := map[string]int{}
	for i, r := range p.Records {
		byID[r.ID] = i
	}
	first := p.Snapshot.OmittedEvents
	for i, event := range task.Events[first:] {
		prefix := fmt.Sprintf("event:%d:%d", event.Sequence, first+i)
		for id, index := range byID {
			if id != prefix && (len(id) <= len(prefix) || id[:len(prefix)+1] != prefix+":") {
				continue
			}
			r := p.Records[index]
			r.Source = "task_ledger"
			r.StepID = stringValue(event.StepID)
			r.AttemptID = stringValue(event.Payload["attempt_id"])
			payload := map[string]any{}
			for k, v := range event.Payload {
				if k != "agent_context" && k != "decision_rounds" {
					payload[k] = v
				}
			}
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
