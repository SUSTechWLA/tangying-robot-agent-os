package agentruntime

import (
	"bytes"
	"encoding/json"
	"reflect"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

type eventIdentity struct {
	topic, task, step, robot, command string
	revision                          uint64
	payload                           map[string]any
}

var mirrorDescriptionKeys = []string{"safetyLevel", "receiptObservationId", "evidenceSource"}

func captureEventIdentity(event agentcontract.Event) (eventIdentity, error) {
	identity := eventIdentity{topic: event.Topic, task: event.TaskID, step: event.StepID, robot: event.RobotID, command: event.CommandID, revision: event.TaskRevision}
	wire, err := json.Marshal(event.Payload)
	if err != nil {
		return identity, err
	}
	decoder := json.NewDecoder(bytes.NewReader(wire))
	decoder.UseNumber()
	err = decoder.Decode(&identity.payload)
	if identity.payload == nil {
		identity.payload = map[string]any{}
	}
	return identity, err
}

func (a eventIdentity) matches(b eventIdentity) bool {
	if a.topic != b.topic || a.task != b.task || a.step != b.step || a.robot != b.robot || a.command != b.command || a.revision != b.revision {
		return false
	}
	left, right := clonePayload(a.payload), clonePayload(b.payload)
	for _, key := range []string{"agent", "agentVersion", "causationId", "correlationId"} {
		delete(left, key)
		delete(right, key)
	}
	if a.topic == agentcontract.TopicActionExecuted {
		// Known runner/raw mirrors add these optional descriptive fields. Both
		// declared but different remains a conflict; command identity never is.
		for _, key := range mirrorDescriptionKeys {
			_, l := left[key]
			_, r := right[key]
			if !l || !r {
				delete(left, key)
				delete(right, key)
			}
		}
	}
	return reflect.DeepEqual(left, right)
}

func (a eventIdentity) mergeMirrorDescription(b eventIdentity) {
	if a.topic != agentcontract.TopicActionExecuted {
		return
	}
	for _, key := range mirrorDescriptionKeys {
		if _, exists := a.payload[key]; !exists {
			if value, supplied := b.payload[key]; supplied {
				a.payload[key] = value
			}
		}
	}
}
