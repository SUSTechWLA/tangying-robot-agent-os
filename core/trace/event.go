package trace

import "time"

type Event struct {
	TaskID         string         `json:"taskId"`
	Sequence       uint64         `json:"sequence"`
	Type           string         `json:"type"`
	StepID         string         `json:"stepId,omitempty"`
	OccurredAt     time.Time      `json:"occurredAt"`
	MonotonicNanos int64          `json:"monotonicNanos,omitempty"`
	Payload        map[string]any `json:"payload,omitempty"`
}
