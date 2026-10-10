package coordinator

import (
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// ExecutionDigest includes only the approved execution specification. Events,
// lifecycle timestamps and pending revision proposals do not rewrite it.
func ExecutionDigest(task *tasks.Task) (string, error) {
	if task == nil {
		return "", fmt.Errorf("%w: task missing", contextcontract.ErrInvalid)
	}
	if err := contextcontract.ValidatePortableArguments(task.Intent); err != nil {
		return "", err
	}
	if err := contextcontract.ValidatePortableArguments(task.Plan); err != nil {
		return "", err
	}
	return contextcontract.Fingerprint(struct {
		ID       string `json:"id"`
		Revision uint64 `json:"revision"`
		Request  string `json:"request"`
		Adapter  string `json:"adapter"`
		Intent   any    `json:"intent"`
		Plan     any    `json:"plan"`
	}{task.ID, task.CurrentRevision, task.Request, tasks.NormalizeAdapter(task.Adapter), task.Intent, task.Plan})
}

// NewContextBasis is also used by transport integration fixtures. Production
// callers persist this value atomically with INTENT_CLAIMED before returning it.
func NewContextBasis(task *tasks.Task, node *IntentNode, claimVersion uint64, worldID string) (*contextcontract.Basis, error) {
	digest, err := ExecutionDigest(task)
	if err != nil {
		return nil, err
	}
	world := contextcontract.WorldBasis{Mode: "edge_local"}
	if worldID != "" {
		world = contextcontract.WorldBasis{Mode: "coordinator_world", WorldID: worldID, Revision: node.WorldRevision,
			EntitySourceID: node.EntitySourceID, EntitySequence: node.EntitySequence, EntityCount: node.EntityCount,
			RobotSourceID: node.RobotSourceID, RobotSequence: node.RobotSequence}
	}
	basis, err := (contextcontract.Basis{SchemaVersion: contextcontract.SchemaVersion, Scope: contextcontract.IntentScope,
		TaskID: task.ID, TaskRevision: node.TaskRevision, AggregateVersion: node.AggregateVersion,
		ClaimVersion: claimVersion, IntentIndex: node.Index, StepID: node.StepID, CommandID: node.CommandID,
		RobotID: node.Claimed, Adapter: tasks.NormalizeAdapter(task.Adapter), ExecutionDigest: digest,
		ResourceID: node.ResourceID, FencingToken: node.FencingToken, CatalogRevision: node.CatalogRevision, World: world}).Seal()
	if err != nil {
		return nil, err
	}
	return &basis, nil
}

// ValidateContextBasis rejects a task fetched from one revision paired with a
// claim from another. A newer aggregate may add a pending proposal while the
// active immutable execution remains identical; an older aggregate cannot.
func ValidateContextBasis(task *tasks.Task, node *IntentNode) error {
	if task == nil || node == nil || node.ContextBasis == nil {
		return fmt.Errorf("%w: handoff missing", contextcontract.ErrInvalid)
	}
	b := node.ContextBasis
	if err := b.Validate(); err != nil {
		return err
	}
	if task.CurrentRevision != b.TaskRevision {
		return ErrStaleTaskRevision
	}
	if task.ID != b.TaskID || task.AggregateVersion < b.AggregateVersion || node.TaskRevision != b.TaskRevision ||
		node.AggregateVersion != b.AggregateVersion || node.Index != b.IntentIndex || node.StepID != b.StepID ||
		node.CommandID != b.CommandID || node.Claimed != b.RobotID || (node.RobotID != "" && node.RobotID != b.RobotID) ||
		node.Status != StatusRunning || node.ResourceID != b.ResourceID || node.FencingToken != b.FencingToken ||
		node.CatalogRevision != b.CatalogRevision || node.WorldRevision != b.World.Revision ||
		node.EntitySourceID != b.World.EntitySourceID || node.EntitySequence != b.World.EntitySequence || node.EntityCount != b.World.EntityCount ||
		node.RobotSourceID != b.World.RobotSourceID || node.RobotSequence != b.World.RobotSequence {
		return fmt.Errorf("%w: claim/task/world identity changed", contextcontract.ErrInvalid)
	}
	digest, err := ExecutionDigest(task)
	if err != nil {
		return err
	}
	if digest != b.ExecutionDigest {
		return fmt.Errorf("%w: approved execution changed", contextcontract.ErrInvalid)
	}
	return nil
}
