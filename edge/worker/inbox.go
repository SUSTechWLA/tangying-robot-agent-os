package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
)

// NotificationAcknowledger is optional for transports that can defer delivery
// acknowledgement until the edge journal has durably accepted a notification.
type NotificationAcknowledger interface {
	AckNotification(context.Context, string) error
}

type notificationCheckpoint struct {
	SchemaVersion string `json:"schemaVersion"`
	RobotID       string `json:"robotId"`
	TaskID        string `json:"taskId"`
}

func (w *Worker) notificationKey() string {
	digest, _ := contextcontract.Fingerprint([]string{w.config.RobotID})
	return "edge-notification/" + digest
}

func (w *Worker) loadNotification(ctx context.Context) (eventstore.AggregateState, notificationCheckpoint, error) {
	store, err := w.contextStore()
	if err != nil {
		return eventstore.AggregateState{}, notificationCheckpoint{}, err
	}
	state, found, err := store.LoadState(ctx, w.notificationKey())
	if err != nil || !found {
		return state, notificationCheckpoint{}, err
	}
	var saved notificationCheckpoint
	if err := json.Unmarshal(state.Data, &saved); err != nil {
		return state, saved, err
	}
	if saved.SchemaVersion != "edge.notification.v1" || saved.RobotID != w.config.RobotID {
		return state, saved, errors.New("invalid durable task notification")
	}
	return state, saved, nil
}

func (w *Worker) persistNotification(ctx context.Context, state eventstore.AggregateState, taskID string) error {
	store, err := w.contextStore()
	if err != nil {
		return err
	}
	wire, err := json.Marshal(notificationCheckpoint{"edge.notification.v1", w.config.RobotID, taskID})
	if err != nil {
		return err
	}
	now, key := time.Now().UTC(), w.notificationKey()
	eventID := fmt.Sprintf("%s/%020d", key, state.Version+1)
	eventType := "TASK_NOTIFICATION_ACCEPTED"
	eventTaskID := taskID
	if taskID == "" {
		eventType = "TASK_NOTIFICATION_FINISHED"
		var previous notificationCheckpoint
		if err := json.Unmarshal(state.Data, &previous); err != nil {
			return err
		}
		eventTaskID = previous.TaskID
	}
	return store.Commit(ctx, eventstore.CommitRequest{ExpectedVersion: state.Version,
		State: eventstore.AggregateState{AggregateID: key, Version: state.Version + 1, Data: wire, UpdatedAt: now},
		Events: []eventstore.DomainEvent{{EventID: eventID, AggregateID: key, AggregateVersion: state.Version + 1,
			AggregateType: "edge_notification", EventType: eventType, IdempotencyKey: eventID, Actor: w.config.RobotID,
			CorrelationID: eventTaskID, OccurredAt: now, Payload: map[string]any{"taskId": eventTaskID}}},
		Checkpoint: &eventstore.Checkpoint{AggregateID: key, Version: state.Version + 1, EventCursor: eventID, Data: wire, CreatedAt: now}})
}

func (w *Worker) nextDurableTask(ctx context.Context) (string, error) {
	state, saved, err := w.loadNotification(ctx)
	if err != nil {
		return "", err
	}
	if saved.TaskID != "" {
		return saved.TaskID, nil
	}
	taskID, err := w.config.Source.Next(ctx)
	if err != nil || taskID == "" {
		return taskID, err
	}
	if err := w.persistNotification(ctx, state, taskID); err != nil {
		return "", err
	}
	if source, ok := w.config.Source.(NotificationAcknowledger); ok {
		// On an acknowledgement failure the inbox still owns the task. A later
		// delivery is harmless: cloud claims and execution checkpoints gate motion.
		if err := source.AckNotification(ctx, taskID); err != nil {
			return "", err
		}
	}
	return taskID, nil
}

func (w *Worker) finishDurableTask(ctx context.Context, taskID string) error {
	state, saved, err := w.loadNotification(ctx)
	if err != nil {
		return err
	}
	if saved.TaskID != taskID || taskID == "" {
		return errors.New("durable task notification ownership changed")
	}
	return w.persistNotification(ctx, state, "")
}
