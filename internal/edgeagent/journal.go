package edgeagent

import (
	"bytes"
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
	return commitSnapshot(ctx, store, key, version, value, kind, taskID, payload, true)
}

func commitSnapshot(ctx context.Context, store eventstore.Store, key string, version uint64, value any, kind, taskID string, payload map[string]any, checkpoint bool) error {
	wire, err := json.Marshal(value)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	id := fmt.Sprintf("%s/%020d", key, version+1)
	request := eventstore.CommitRequest{ExpectedVersion: version, State: eventstore.AggregateState{AggregateID: key, Version: version + 1, Data: wire, UpdatedAt: now}, Events: []eventstore.DomainEvent{{EventID: id, AggregateType: "edge_goal", AggregateID: key, AggregateVersion: version + 1, EventType: kind, CorrelationID: taskID, IdempotencyKey: id, Payload: payload, OccurredAt: now}}}
	if checkpoint {
		request.Checkpoint = &eventstore.Checkpoint{AggregateID: key, Version: version + 1, Data: wire, EventCursor: id, CreatedAt: now}
	}
	return store.Commit(ctx, request)
}

// childEvents is the durable source of receipts, independent of cloud delivery.
// A child plan gets its own immutable identity; replanning never reuses its keys.
type childEvents struct {
	store    eventstore.Store
	parent   capabilityEvents
	parentID string
	// writer belongs to one serialized Host.Run. Get never returns this object.
	// The executor ignores AppendEvent's optional snapshot (as for cloudEvents),
	// so the cached write path returns nil instead of exposing mutable aliases.
	writer *childWriter
}

type childWriter struct {
	id      string
	version uint64
	task    *tasks.Task
}
type capabilityEvents interface {
	Get(context.Context, string) (*tasks.Task, error)
	AppendEvent(context.Context, string, tasks.TaskEvent) (*tasks.Task, error)
}

// Parent audit delivery does not consume the task snapshot. Use an append-only
// port when available, without weakening the child's durable journal boundary.
func recordParentEvent(ctx context.Context, parent capabilityEvents, taskID string, event tasks.TaskEvent) error {
	if recorder, ok := parent.(interface {
		RecordEvent(context.Context, string, tasks.TaskEvent) (tasks.TaskEvent, error)
	}); ok {
		_, err := recorder.RecordEvent(ctx, taskID, event)
		return err
	}
	_, err := parent.AppendEvent(ctx, taskID, event)
	return err
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
	key := "edge-child/" + id
	var task *tasks.Task
	var version uint64
	if e.writer != nil && e.writer.id == id {
		task, version = e.writer.task, e.writer.version
	}
	if task == nil {
		s, found, err := e.store.LoadState(ctx, key)
		if err != nil || !found {
			return nil, fmt.Errorf("child journal unavailable: %v", err)
		}
		task = &tasks.Task{}
		decoder := json.NewDecoder(bytes.NewReader(s.Data))
		decoder.UseNumber()
		if err = decoder.Decode(task); err != nil {
			return nil, err
		}
		version = s.Version
	}
	event.Sequence = uint64(len(task.Events) + 1)
	event.OccurredAt = time.Now().UTC()
	// Callers retain ownership of their payload maps. The private writer must
	// not accept later mutations to an already committed receipt as new facts.
	wire, err := json.Marshal(event)
	if err != nil {
		return nil, err
	}
	var storedEvent tasks.TaskEvent
	decoder := json.NewDecoder(bytes.NewReader(wire))
	decoder.UseNumber()
	if err = decoder.Decode(&storedEvent); err != nil {
		return nil, err
	}
	task.Events = append(task.Events, storedEvent)
	// TOOL_ACTIVITY is a derived display event. Its full fact remains in the
	// atomically committed state and domain log, but need not rewrite a second
	// complete snapshot. All capability/receipt/verification boundaries still
	// checkpoint; recovery always loads the current aggregate, not this hint.
	if err = commitSnapshot(ctx, e.store, key, version, task, event.Type, e.parentID, storedEvent.Payload, event.Type != "TOOL_ACTIVITY"); err != nil {
		if e.writer != nil {
			*e.writer = childWriter{}
		}
		return nil, err
	}
	if e.writer != nil {
		*e.writer = childWriter{id: id, version: version + 1, task: task}
	}
	// Advisory parent progress never supplies the child's durable receipt. The
	// final proof is mandatory and retransmitted before reporting completion.
	if e.parent != nil {
		_ = recordParentEvent(ctx, e.parent, e.parentID, tasks.TaskEvent{Type: "EDGE_CHILD_EVENT", Message: event.Type, Payload: map[string]any{"role": "task", "childId": id, "childStepId": event.StepID, "kind": event.Type, "eventDigest": capability.Fingerprint(event), "detail": event.Payload}})
		if event.Type == "CAPABILITY_STOP_CONFIRMED" {
			parent, err := e.parent.Get(ctx, e.parentID)
			if err != nil {
				return nil, err
			}
			err = recordParentEvent(ctx, e.parent, e.parentID, tasks.TaskEvent{Type: "CAPABILITY_STOP_CONFIRMED", StepID: fmt.Sprintf("rev-%d-cap-%02d", parent.CurrentRevision, 1), Payload: map[string]any{"tool": "edge.goal", "childId": id, "childStepId": event.StepID, "stopEvidence": event.Payload}})
			if err != nil {
				return nil, err
			}
		}
	}
	if e.writer != nil {
		return nil, nil
	}
	return task, nil
}
