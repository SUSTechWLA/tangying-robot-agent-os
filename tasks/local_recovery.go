package tasks

import (
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"time"
)

// LocalReconciliation preserves the operator's conclusion separately from the
// original execution status. It is neither completion proof nor replay consent.
type LocalReconciliation struct {
	StepID         string    `json:"stepId"`
	Capability     string    `json:"capability"`
	OriginalStatus string    `json:"originalStatus"`
	Outcome        string    `json:"outcome"`
	Actor          string    `json:"actor"`
	Note           string    `json:"note"`
	RecordedAt     time.Time `json:"recordedAt"`
}

// LocalRecovery is a read-only view of execution receipts, not authorization to
// replay a tool. The Local App rechecks durable history on every resume.
type LocalRecovery struct {
	TaskID                 string                `json:"taskId"`
	State                  taskgraph.TaskState   `json:"state"`
	CanPause               bool                  `json:"canPause"`
	CanResume              bool                  `json:"canResume"`
	PauseRequested         bool                  `json:"pauseRequested"`
	RequiresReconciliation bool                  `json:"requiresReconciliation"`
	ReasonCode             string                `json:"reasonCode"`
	Reason                 string                `json:"reason"`
	CompletedStepIDs       []string              `json:"completedStepIds"`
	UncertainStepIDs       []string              `json:"uncertainStepIds"`
	Reconciliations        []LocalReconciliation `json:"reconciliations,omitempty"`
}
