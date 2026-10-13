package coordinator

import (
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration/handoff"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// ExecutionDigest preserves the existing cloud API over the shared handoff policy.
func ExecutionDigest(task *tasks.Task) (string, error) { return handoff.ExecutionDigest(task) }

// NewContextBasis returns a handoff which callers must persist with the claim.
func NewContextBasis(task *tasks.Task, node *IntentNode, claimVersion uint64, worldID string) (*contextcontract.Basis, error) {
	return handoff.NewContextBasis(task, node, claimVersion, worldID)
}

// ValidateContextBasis preserves the existing API and exact validation behavior.
func ValidateContextBasis(task *tasks.Task, node *IntentNode) error {
	return handoff.ValidateContextBasis(task, node)
}
