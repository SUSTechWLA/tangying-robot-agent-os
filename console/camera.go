package console

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"image/png"
	"net/http"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// A selected camera is returned atomically, so two image requests cannot mix
// different source frames with each other or with the displayed point cloud.
func (s *Server) cameraObservation(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	adapter, source := r.URL.Query().Get("adapter"), r.URL.Query().Get("sourceId")
	if !tasks.ValidEvidenceKey(adapter) || !tasks.ValidEvidenceKey(source) {
		writeError(w, http.StatusBadRequest, "CAMERA_SOURCE_REQUIRED", "请选择已登记的机器人相机")
		return
	}
	if s.camera == nil {
		writeError(w, http.StatusServiceUnavailable, "CAMERA_UNAVAILABLE", "当前适配器尚未提供相机选择")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	snapshot, err := s.camera.TelemetrySource(ctx, "", source)
	if err == nil {
		err = validateCameraCapture(snapshot, adapter, source)
	}
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "CAMERA_CAPTURE_UNAVAILABLE", "所选相机未返回新鲜、完整且来源一致的RGB-D采集")
		return
	}
	snapshot.TaskID, snapshot.StepID, snapshot.TaskRevision = "", "", 0
	snapshot.ColorFrameAvailable, snapshot.DepthFrameAvailable = true, true
	w.Header().Set("X-Observed-At", snapshot.ObservedAt.UTC().Format(time.RFC3339Nano))
	writeJSON(w, http.StatusOK, map[string]any{
		"snapshot":     snapshot,
		"rgbDataUrl":   "data:image/png;base64," + base64.StdEncoding.EncodeToString(snapshot.Frame),
		"depthDataUrl": "data:image/png;base64," + base64.StdEncoding.EncodeToString(snapshot.DepthFrame),
	})
}

func validateCameraCapture(snapshot telemetry.Snapshot, adapter, source string) error {
	r, p := snapshot.Reconstruction, snapshot.RobotProfile
	if r == nil || p == nil || snapshot.SchemaVersion != "telemetry.v1" || snapshot.Adapter != adapter || snapshot.RobotID != r.RobotID || r.SourceID != source || r.SourceType != "rgbd_camera" || snapshot.ObservedAt.UnixMilli() != r.ObservedAtUnixMS {
		return errors.New("camera capture identity mismatch")
	}
	if err := p.Validate(); err != nil {
		return err
	}
	if err := r.Validate(*p, time.Now()); err != nil {
		return err
	}
	if len(snapshot.Frame)+len(snapshot.DepthFrame) > tasks.MaxEvidenceImageBytes {
		return errors.New("camera images exceed bounded payload")
	}
	var width, height int
	for _, media := range []struct {
		data []byte
		kind string
	}{{snapshot.Frame, snapshot.FrameMediaType}, {snapshot.DepthFrame, snapshot.DepthFrameMediaType}} {
		if media.kind != "image/png" {
			return errors.New("camera preview must be PNG")
		}
		cfg, err := png.DecodeConfig(bytes.NewReader(media.data))
		if err != nil || cfg.Width < 1 || cfg.Width > 3840 || cfg.Height < 1 || cfg.Height > 2160 {
			return errors.New("invalid camera PNG dimensions")
		}
		if width != 0 && (cfg.Width != width || cfg.Height != height) {
			return errors.New("camera images are not aligned")
		}
		width, height = cfg.Width, cfg.Height
		if _, err := png.Decode(bytes.NewReader(media.data)); err != nil {
			return err
		}
	}
	return nil
}
