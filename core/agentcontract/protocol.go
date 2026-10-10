package agentcontract

import (
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/google/uuid"
)

const EventProtocolV1 = "agent.event.v1"

// NewEventID survives process restarts. A task sequence still identifies a
// projected ledger fact; independently authored agent facts need their own ID.
func NewEventID() string { return "evt-" + uuid.NewString() }

// RecoveryBinding names the failed execution a diagnostic may answer. Empty
// fields mean unknown: no consumer may fill them from the latest task snapshot.
type RecoveryBinding struct {
	TaskID         string `json:"taskId,omitempty"`
	TaskRevision   uint64 `json:"taskRevision,omitempty"`
	RobotID        string `json:"robotId,omitempty"`
	StepID         string `json:"stepId,omitempty"`
	CommandID      string `json:"commandId,omitempty"`
	SourceEventID  string `json:"sourceEventId,omitempty"`
	AnomalyEventID string `json:"anomalyEventId,omitempty"`
	PlanEventID    string `json:"planEventId,omitempty"`
}

func (b RecoveryBinding) Complete() bool {
	return b.TaskID != "" && b.TaskRevision != 0 && b.RobotID != "" && b.StepID != "" && b.CommandID != "" && b.SourceEventID != ""
}

func (b RecoveryBinding) Encode() map[string]any {
	p := map[string]any{}
	putString(p, "taskId", b.TaskID)
	if b.TaskRevision != 0 {
		p["taskRevision"] = b.TaskRevision
	}
	putString(p, "robotId", b.RobotID)
	putString(p, "stepId", b.StepID)
	putString(p, "commandId", b.CommandID)
	putString(p, "sourceEventId", b.SourceEventID)
	putString(p, "anomalyEventId", b.AnomalyEventID)
	putString(p, "planEventId", b.PlanEventID)
	return p
}

// DecodeRecoveryBinding rejects malformed numeric identities, including a
// fractional revision. It accepts map values after a durable JSON round trip.
func DecodeRecoveryBinding(value any) (RecoveryBinding, error) {
	wire, err := json.Marshal(value)
	if err != nil {
		return RecoveryBinding{}, err
	}
	var binding RecoveryBinding
	err = json.Unmarshal(wire, &binding)
	return binding, err
}

// NormalizeEvent binds the envelope to any identities already declared in its
// body. Contradictions and unsupported versions are refused, never repaired by
// copying a current task revision or robot identity over the source.
func NormalizeEvent(event Event) (Event, error) {
	if !KnownTopic(event.Topic) {
		return event, fmt.Errorf("unknown agent topic %q", event.Topic)
	}
	if event.ProtocolVersion == "" {
		event.ProtocolVersion = EventProtocolV1
	}
	if event.ProtocolVersion != EventProtocolV1 {
		return event, fmt.Errorf("unsupported agent protocol %q", event.ProtocolVersion)
	}
	if version, exists := event.Payload["protocolVersion"]; exists && version != event.ProtocolVersion {
		return event, fmt.Errorf("conflicting event protocolVersion")
	}
	if task, exists := event.Payload["taskId"]; exists && task != event.TaskID {
		return event, fmt.Errorf("conflicting event taskId")
	}
	for key, field := range map[string]*string{"eventId": &event.ID, "stepId": &event.StepID, "robotId": &event.RobotID, "commandId": &event.CommandID} {
		value, exists := event.Payload[key]
		if !exists {
			continue
		}
		text, ok := value.(string)
		if !ok {
			return event, fmt.Errorf("invalid event %s", key)
		}
		if text != "" && *field != "" && text != *field {
			return event, fmt.Errorf("conflicting event %s", key)
		}
		if *field == "" {
			*field = text
		}
	}
	if value, exists := event.Payload["taskRevision"]; exists {
		wire, err := json.Marshal(value)
		if err != nil {
			return event, err
		}
		revision, err := strconv.ParseUint(string(wire), 10, 64)
		if err != nil {
			return event, fmt.Errorf("invalid event taskRevision")
		}
		if revision != 0 && event.TaskRevision != 0 && revision != event.TaskRevision {
			return event, fmt.Errorf("conflicting event taskRevision")
		}
		if event.TaskRevision == 0 {
			event.TaskRevision = revision
		}
	}
	if event.ID == "" {
		event.ID = NewEventID()
	}
	if event.CorrelationID == "" {
		event.CorrelationID = event.TaskID
	}
	return event, nil
}
