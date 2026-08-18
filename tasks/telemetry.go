package tasks

import (
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

const telemetryHistoryLimit = 100

// TelemetryHub keeps the latest low-rate Robot Runtime snapshot and a bounded
// history per adapter. Telemetry is operational data, not audit data, so it is
// intentionally in-memory because high-rate samples do not belong in the durable task log.
type TelemetryHub struct {
	mu      sync.RWMutex
	latest  map[string]telemetry.Snapshot
	history map[string][]telemetry.Snapshot
}

func NewTelemetryHub() *TelemetryHub {
	return &TelemetryHub{
		latest:  map[string]telemetry.Snapshot{},
		history: map[string][]telemetry.Snapshot{},
	}
}

func (h *TelemetryHub) Publish(snapshot telemetry.Snapshot) {
	if snapshot.Adapter == "" {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	h.latest[snapshot.Adapter] = snapshot
	history := append([]telemetry.Snapshot(nil), h.history[snapshot.Adapter]...)
	history = append(history, snapshot)
	if len(history) > telemetryHistoryLimit {
		history = history[len(history)-telemetryHistoryLimit:]
	}
	h.history[snapshot.Adapter] = history
}

func (h *TelemetryHub) Latest(adapter string) (telemetry.Snapshot, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	snapshot, ok := h.latest[adapter]
	return snapshot, ok
}

func (h *TelemetryHub) History(adapter string, limit int) []telemetry.Snapshot {
	h.mu.RLock()
	defer h.mu.RUnlock()
	if limit <= 0 || limit > telemetryHistoryLimit {
		limit = telemetryHistoryLimit
	}
	history := append([]telemetry.Snapshot(nil), h.history[adapter]...)
	if len(history) > limit {
		history = history[len(history)-limit:]
	}
	return history
}

func (h *TelemetryHub) Adapters() []string {
	h.mu.RLock()
	defer h.mu.RUnlock()
	adapters := make([]string, 0, len(h.latest))
	for adapter := range h.latest {
		adapters = append(adapters, adapter)
	}
	return adapters
}
