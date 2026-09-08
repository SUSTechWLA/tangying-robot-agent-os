package tasks

import (
	"bytes"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	_ "golang.org/x/image/webp"
)

const telemetryHistoryLimit = 100

// MaxSceneFrameBytes bounds compressed observer frames before the Local Agent
// takes ownership. Semantic telemetry still publishes when a frame is dropped.
const MaxSceneFrameBytes = 8 << 20

// MaxSceneFrameAge is a display freshness ceiling. A declared sensor may
// require a tighter budget; no frame obtains a new timestamp when cached.
const MaxSceneFrameAge = 5 * time.Second

const (
	maxSceneFrameDimension = 4096
	maxSceneFramePixels    = 16 * 1024 * 1024
)

type SceneFrameIssue string

const (
	SceneFrameUnsupported SceneFrameIssue = "unsupported"
	SceneFrameInvalid     SceneFrameIssue = "invalid"
)

type SceneFrame struct {
	Data       []byte
	MediaType  string
	ObservedAt time.Time
	MaxAge     time.Duration
}

func (frame SceneFrame) Fresh(now time.Time) bool {
	age := now.Sub(frame.ObservedAt)
	maxAge := frame.MaxAge
	if maxAge <= 0 || maxAge > MaxSceneFrameAge {
		maxAge = MaxSceneFrameAge
	}
	return len(frame.Data) > 0 && frame.MediaType != "" && !frame.ObservedAt.IsZero() && age >= -250*time.Millisecond && age <= maxAge
}

// TelemetryHub keeps the latest low-rate Robot Runtime snapshot and a bounded
// history per adapter. Telemetry is operational data, not audit data, so it is
// intentionally in-memory because high-rate samples do not belong in the durable task log.
type TelemetryHub struct {
	mu          sync.RWMutex
	latest      map[string]telemetry.Snapshot
	history     map[string][]telemetry.Snapshot
	frames      map[string]SceneFrame
	issues      map[string]SceneFrameIssue
	depthFrames map[string]SceneFrame
	depthIssues map[string]SceneFrameIssue
}

func NewTelemetryHub() *TelemetryHub {
	return &TelemetryHub{
		latest:      map[string]telemetry.Snapshot{},
		history:     map[string][]telemetry.Snapshot{},
		frames:      map[string]SceneFrame{},
		issues:      map[string]SceneFrameIssue{},
		depthFrames: map[string]SceneFrame{},
		depthIssues: map[string]SceneFrameIssue{},
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
	depthFrame, depthMediaType := snapshot.DepthFrame, snapshot.DepthFrameMediaType
	snapshot.Frame = nil
	snapshot.FrameMediaType = ""
	snapshot.DepthFrame = nil
	snapshot.DepthFrameMediaType = ""
	snapshot.ColorFrameAvailable = false
	snapshot.DepthFrameAvailable = false
	snapshot = cloneTelemetrySnapshot(snapshot)
	h.latest[snapshot.Adapter] = snapshot
	history := append([]telemetry.Snapshot(nil), h.history[snapshot.Adapter]...)
	history = append(history, snapshot)
	if len(history) > telemetryHistoryLimit {
		history = history[len(history)-telemetryHistoryLimit:]
	}
	h.history[snapshot.Adapter] = history
	maxAge := sceneFrameMaxAge(snapshot)
	cacheSceneFrame(h.frames, h.issues, snapshot, frame, frameMediaType, maxAge, false)
	cacheSceneFrame(h.depthFrames, h.depthIssues, snapshot, depthFrame, depthMediaType, maxAge, true)
}

func sceneFrameMaxAge(snapshot telemetry.Snapshot) time.Duration {
	maxAge := MaxSceneFrameAge
	if snapshot.RobotProfile != nil && snapshot.Reconstruction != nil {
		for _, sensor := range snapshot.RobotProfile.Sensors {
			if sensor.SourceID == snapshot.Reconstruction.SourceID && sensor.MaxAgeMS > 0 {
				budget := time.Duration(sensor.MaxAgeMS) * time.Millisecond
				if budget < maxAge {
					maxAge = budget
				}
			}
		}
	}
	return maxAge
}

func cacheSceneFrame(frames map[string]SceneFrame, issues map[string]SceneFrameIssue, snapshot telemetry.Snapshot, frame []byte, frameMediaType string, maxAge time.Duration, depth bool) {
	delete(frames, snapshot.Adapter)
	delete(issues, snapshot.Adapter)
	if len(frame) == 0 || len(frame) > MaxSceneFrameBytes || strings.TrimSpace(frameMediaType) == "" {
		return
	}
	if depth && strings.ToLower(strings.TrimSpace(frameMediaType)) != "image/png" {
		issues[snapshot.Adapter] = SceneFrameUnsupported
		return
	}
	mediaType, issue := verifySceneFrame(frame, frameMediaType)
	if issue != "" {
		issues[snapshot.Adapter] = issue
		return
	}
	frames[snapshot.Adapter] = SceneFrame{
		Data:       append([]byte(nil), frame...),
		MediaType:  mediaType,
		ObservedAt: snapshot.ObservedAt,
		MaxAge:     maxAge,
	}
}

func verifySceneFrame(data []byte, declaredMediaType string) (string, SceneFrameIssue) {
	mediaType := strings.ToLower(strings.TrimSpace(declaredMediaType))
	wantedFormat, supported := map[string]string{
		"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp",
	}[mediaType]
	if !supported {
		return "", SceneFrameUnsupported
	}
	configuration, detectedFormat, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || detectedFormat != wantedFormat {
		return "", SceneFrameInvalid
	}
	if configuration.Width <= 0 || configuration.Height <= 0 ||
		configuration.Width > maxSceneFrameDimension || configuration.Height > maxSceneFrameDimension ||
		configuration.Width*configuration.Height > maxSceneFramePixels {
		return "", SceneFrameInvalid
	}
	_, decodedFormat, err := image.Decode(bytes.NewReader(data))
	if err != nil || decodedFormat != wantedFormat {
		return "", SceneFrameInvalid
	}
	return mediaType, ""
}

func (h *TelemetryHub) Latest(adapter string) (telemetry.Snapshot, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	snapshot, ok := h.latest[adapter]
	snapshot.ColorFrameAvailable = h.frames[adapter].Fresh(time.Now())
	snapshot.DepthFrameAvailable = h.depthFrames[adapter].Fresh(time.Now())
	return cloneTelemetrySnapshot(snapshot), ok
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
	for index := range history {
		history[index] = cloneTelemetrySnapshot(history[index])
	}
	return history
}

func (h *TelemetryHub) LatestDepthFrame(adapter string) (SceneFrame, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	frame, ok := h.depthFrames[adapter]
	frame.Data = append([]byte(nil), frame.Data...)
	return frame, ok
}

func (h *TelemetryHub) LatestDepthFrameIssue(adapter string) (SceneFrameIssue, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	issue, ok := h.depthIssues[adapter]
	return issue, ok
}

func (h *TelemetryHub) LatestFrame(adapter string) (SceneFrame, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	frame, ok := h.frames[adapter]
	frame.Data = append([]byte(nil), frame.Data...)
	return frame, ok
}

func (h *TelemetryHub) LatestFrameIssue(adapter string) (SceneFrameIssue, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	issue, ok := h.issues[adapter]
	return issue, ok
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
