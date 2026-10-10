package agentruntime

import (
	"context"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// flushClosingEdges runs only inside the serialized observation pass. The
// journal commit precedes Publish, so a high-priority reopening may overtake or
// evict its low-priority clear without losing the durable episode boundary.
func (a *OpsAgent) flushClosingEdges(ctx context.Context) {
	if a.Publish == nil {
		return
	}
	a.mu.Lock()
	pending := make(map[openCondition]agentcontract.Event, len(a.pendingClears))
	for condition, event := range a.pendingClears {
		pending[condition] = event
	}
	a.mu.Unlock()
	for condition, original := range pending {
		// This edge was authored locally with the fixed Ops identity. Normalize
		// before durability so its checkpoint and eventual bus event share ID.
		event, err := agentcontract.NormalizeEvent(original)
		if err != nil {
			continue
		}
		if a.PersistClear != nil {
			if err := a.PersistClear(ctx, event); err != nil {
				continue // Remains pending; Health reports OPS_CLEAR_NOT_PERSISTED.
			}
		}
		a.mu.Lock()
		delete(a.pendingClears, condition)
		// All count variants of this exact issue can report immediately on a
		// real reopening. Other tasks, commands and components keep cooldown.
		for report := range a.findings {
			if report.condition == condition {
				delete(a.findings, report)
			}
		}
		a.mu.Unlock()
		a.Publish(ctx, event)
	}
}
