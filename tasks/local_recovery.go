package tasks

import "github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"

// LocalRecovery is a read-only view of execution receipts, not authorization to
// replay a tool. The Local App rechecks durable history on every resume.
type LocalRecovery struct {
	TaskID                 string              `json:"taskId"`
	State                  taskgraph.TaskState `json:"state"`
	CanPause               bool                `json:"canPause"`
	CanResume              bool                `json:"canResume"`
	PauseRequested         bool                `json:"pauseRequested"`
	RequiresReconciliation bool                `json:"requiresReconciliation"`
	ReasonCode             string              `json:"reasonCode"`
	Reason                 string              `json:"reason"`
	CompletedStepIDs       []string            `json:"completedStepIds"`
	UncertainStepIDs       []string            `json:"uncertainStepIds"`
}
