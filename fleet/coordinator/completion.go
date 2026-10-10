package coordinator

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
)

// completionReceipt is written in the same transaction as the successful
// intent checkpoint. Keep its typed JSON inside the generic event payload:
// production fencing tokens must not make a round trip through float64.
type completionReceipt struct {
	SchemaVersion     string                `json:"schemaVersion"`
	Basis             contextcontract.Basis `json:"contextBasis"`
	VerificationBasis string                `json:"verificationBasis"`
	CompletedAt       time.Time             `json:"completedAt"`
	Effects           *completionEffects    `json:"effects,omitempty"`
}

type completionEffects struct {
	RobotRelease      *lease.Grant `json:"robotRelease,omitempty"`
	Transition        *lease.Grant `json:"transition,omitempty"`
	ReleaseTransition bool         `json:"releaseTransition,omitempty"`
	LastIntent        bool         `json:"lastIntent"`
}

func completionReceiptJSON(node *IntentNode) (string, error) {
	if node.ContextBasis == nil {
		// A legacy claim has no evidence from which to invent a context receipt.
		return "", nil
	}
	if err := node.ContextBasis.Validate(); err != nil {
		return "", err
	}
	wire, err := json.Marshal(completionReceipt{SchemaVersion: "context.completion.v1", Basis: *node.ContextBasis, VerificationBasis: node.VerificationBasis, CompletedAt: node.Finished})
	return string(wire), err
}

// completedContext reads immutable completion evidence, never the current
// revision's projected task events. A receipt only acknowledges an already
// committed outcome; it cannot advance a node in the active graph.
func (c *Coordinator) completedContext(ctx context.Context, basis *contextcontract.Basis) (bool, error) {
	receipt, err := c.completionRecord(ctx, basis)
	return receipt != nil, err
}

func (c *Coordinator) completionRecord(ctx context.Context, basis *contextcontract.Basis) (*completionReceipt, error) {
	cursor := basis.ClaimVersion
	for {
		events, err := c.store.ListEvents(ctx, "fleet_task", basis.TaskID, cursor, 100)
		if err != nil {
			return nil, err
		}
		for _, event := range events {
			if event.AggregateID != basis.TaskID || event.AggregateType != "fleet_task" || event.AggregateVersion <= cursor {
				return nil, fmt.Errorf("%w: invalid completion event cursor", ErrIntentIdentityConflict)
			}
			cursor = event.AggregateVersion
			if event.IdempotencyKey != basis.CommandID+"/succeeded" {
				continue
			}
			switch event.EventType {
			case "INTENT_SUCCEEDED", "BLOCK_AVAILABLE", "BLOCK_DELIVERED":
			default:
				return nil, fmt.Errorf("%w: invalid completion event type", ErrIntentIdentityConflict)
			}
			wire, ok := event.Payload["completionReceiptJSON"].(string)
			if !ok || wire == "" {
				// Before this protocol version, no immutable context receipt was
				// recorded. The ordinary active-node validation remains available.
				return nil, nil
			}
			var receipt completionReceipt
			if err := json.Unmarshal([]byte(wire), &receipt); err != nil {
				return nil, fmt.Errorf("%w: invalid completion receipt", ErrIntentIdentityConflict)
			}
			if (receipt.SchemaVersion != "context.completion.v1" && receipt.SchemaVersion != "context.completion.v2") || receipt.Basis.Validate() != nil ||
				receipt.Basis.Digest != basis.Digest || receipt.VerificationBasis == "" || receipt.CompletedAt.IsZero() {
				return nil, fmt.Errorf("%w: completion receipt context mismatch", ErrIntentIdentityConflict)
			}
			if receipt.SchemaVersion == "context.completion.v2" && receipt.Effects == nil {
				return nil, fmt.Errorf("%w: missing completion effects", ErrIntentIdentityConflict)
			}
			return &receipt, nil
		}
		if len(events) < 100 {
			return nil, nil
		}
	}
}
