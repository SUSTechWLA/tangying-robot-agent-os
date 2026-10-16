package edgeagent

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type completedCall struct {
	ChildID       string `json:"childId"`
	StepID        string `json:"stepId"`
	Binding       string `json:"binding"`
	Tool          string `json:"tool"`
	Mutates       bool   `json:"mutates"`
	EffectBinding string `json:"effectBinding"`
}
type checkpoint struct {
	Version               string          `json:"schemaVersion"`
	ObjectiveDigest       string          `json:"objectiveDigest"`
	StartedAt             time.Time       `json:"startedAt"`
	Generation            int             `json:"generation"`
	Steps                 int             `json:"steps"`
	Reads                 int             `json:"reads"`
	ChildID               string          `json:"childId,omitempty"`
	PlanDigest            string          `json:"planDigest,omitempty"`
	Index                 int             `json:"index"`
	Pending               bool            `json:"pending"`
	Complete              bool            `json:"complete"`
	Completed             []completedCall `json:"completed"`
	Proof                 map[string]any  `json:"proof,omitempty"`
	Diagnosis             map[string]any  `json:"diagnosis,omitempty"`
	CompletionRetries     int             `json:"completionRetries,omitempty"`
	CompletionBlocked     bool            `json:"completionBlocked,omitempty"`
	CompletionReadPending bool            `json:"completionReadPending,omitempty"`
}

func commit(ctx context.Context, store eventstore.Store, key string, version uint64, value any, kind, taskID string, payload map[string]any) error {
	wire, err := json.Marshal(value)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	id := fmt.Sprintf("%s/%020d", key, version+1)
	return store.Commit(ctx, eventstore.CommitRequest{ExpectedVersion: version, State: eventstore.AggregateState{AggregateID: key, Version: version + 1, Data: wire, UpdatedAt: now}, Events: []eventstore.DomainEvent{{EventID: id, AggregateType: "edge_goal", AggregateID: key, AggregateVersion: version + 1, EventType: kind, CorrelationID: taskID, IdempotencyKey: id, Payload: payload, OccurredAt: now}}, Checkpoint: &eventstore.Checkpoint{AggregateID: key, Version: version + 1, Data: wire, EventCursor: id, CreatedAt: now}})
}

// childEvents is the durable source of receipts, independent of cloud delivery.
// A child plan gets its own immutable identity; replanning never reuses its keys.
type childEvents struct {
	store    eventstore.Store
	parent   capabilityEvents
	parentID string
}
type capabilityEvents interface {
	Get(context.Context, string) (*tasks.Task, error)
	AppendEvent(context.Context, string, tasks.TaskEvent) (*tasks.Task, error)
}

func (e childEvents) Get(ctx context.Context, id string) (*tasks.Task, error) {
	s, found, err := e.store.LoadState(ctx, "edge-child/"+id)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, tasks.ErrTaskNotFound
	}
	var task tasks.Task
	if err = json.Unmarshal(s.Data, &task); err != nil {
		return nil, err
	}
	return &task, nil
}
func (e childEvents) AppendEvent(ctx context.Context, id string, event tasks.TaskEvent) (*tasks.Task, error) {
	s, found, err := e.store.LoadState(ctx, "edge-child/"+id)
	if err != nil || !found {
		return nil, fmt.Errorf("child journal unavailable: %v", err)
	}
	var task tasks.Task
	if err = json.Unmarshal(s.Data, &task); err != nil {
		return nil, err
	}
	event.Sequence = uint64(len(task.Events) + 1)
	event.OccurredAt = time.Now().UTC()
	task.Events = append(task.Events, event)
	if err = commit(ctx, e.store, s.AggregateID, s.Version, task, event.Type, e.parentID, event.Payload); err != nil {
		return nil, err
	}
	// Advisory parent progress never supplies the child's durable receipt. The
	// final proof is mandatory and retransmitted before reporting completion.
	if e.parent != nil {
		_, _ = e.parent.AppendEvent(ctx, e.parentID, tasks.TaskEvent{Type: "EDGE_CHILD_EVENT", Message: event.Type, Payload: map[string]any{"role": "task", "childId": id, "childStepId": event.StepID, "kind": event.Type, "eventDigest": capability.Fingerprint(event), "detail": event.Payload}})
		if event.Type == "CAPABILITY_STOP_CONFIRMED" {
			parent, err := e.parent.Get(ctx, e.parentID)
			if err != nil {
				return nil, err
			}
			_, err = e.parent.AppendEvent(ctx, e.parentID, tasks.TaskEvent{Type: "CAPABILITY_STOP_CONFIRMED", StepID: fmt.Sprintf("rev-%d-cap-%02d", parent.CurrentRevision, 1), Payload: map[string]any{"tool": "edge.goal", "childId": id, "childStepId": event.StepID, "stopEvidence": event.Payload}})
			if err != nil {
				return nil, err
			}
		}
	}
	return &task, nil
}
