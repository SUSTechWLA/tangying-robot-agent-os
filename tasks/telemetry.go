package tasks

import (
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

const telemetryHistoryLimit = 100

// MaxSceneFrameBytes bounds compressed observer frames before the Local Agent
// takes ownership. Semantic telemetry still publishes when a frame is dropped.
const MaxSceneFrameBytes = 8 << 20

type SceneFrame struct {
	Data       []byte
	MediaType  string
	ObservedAt time.Time
}

// TelemetryHub keeps the latest low-rate Robot Runtime snapshot and a bounded
// history per adapter. Telemetry is operational data, not audit data, so it is
// intentionally in-memory because high-rate samples do not belong in the durable task log.
type TelemetryHub struct {
	mu      sync.RWMutex
	latest  map[string]telemetry.Snapshot
	history map[string][]telemetry.Snapshot
	frames  map[string]SceneFrame
}

func NewTelemetryHub() *TelemetryHub {
	return &TelemetryHub{
		latest:  map[string]telemetry.Snapshot{},
		history: map[string][]telemetry.Snapshot{},
		frames:  map[string]SceneFrame{},
	}
}

func (h *TelemetryHub) Publish(snapshot telemetry.Snapshot) {
	if snapshot.Adapter == "" {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	frame := snapshot.Frame
	frameMediaType := snapshot.FrameMediaType
	snapshot.Frame = nil
	snapshot.FrameMediaType = ""
	h.latest[snapshot.Adapter] = snapshot
	history := append([]telemetry.Snapshot(nil), h.history[snapshot.Adapter]...)
	history = append(history, snapshot)
	if len(history) > telemetryHistoryLimit {
		history = history[len(history)-telemetryHistoryLimit:]
	}
	h.history[snapshot.Adapter] = history
	if len(frame) > 0 && len(frame) <= MaxSceneFrameBytes && frameMediaType != "" {
		h.frames[snapshot.Adapter] = SceneFrame{
			Data:       append([]byte(nil), frame...),
			MediaType:  frameMediaType,
			ObservedAt: snapshot.ObservedAt,
		}
	}
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

func (h *TelemetryHub) LatestFrame(adapter string) (SceneFrame, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	frame, ok := h.frames[adapter]
	frame.Data = append([]byte(nil), frame.Data...)
	return frame, ok
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
