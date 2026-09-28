package console

import (
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

// Ordinary state reads expose the understanding layer. Geometry is available
// only through an explicit diagnostic view, and raw sensor values embedded in
// a provider's arbitrary robot_state never enter either response.
func semanticTelemetry(snapshot telemetry.Snapshot, geometry bool) telemetry.Snapshot {
	if snapshot.Reconstruction != nil && !geometry {
		copy := *snapshot.Reconstruction
		copy.Points = nil
		copy.PointColors = nil
		snapshot.Reconstruction = &copy
	}
	if snapshot.RobotState != nil {
		clean, _ := semanticStateValue(snapshot.RobotState, 0).(map[string]any)
		snapshot.RobotState = clean
	}
	return snapshot
}

func semanticStateValue(value any, depth int) any {
	if depth > 8 {
		return nil
	}
	switch typed := value.(type) {
	case map[string]any:
		if len(typed) > 256 {
			return nil
		}
		clean := make(map[string]any, len(typed))
		for key, item := range typed {
			lower := strings.ToLower(key)
			raw := lower == "points" || lower == "buffer" || lower == "payload" || lower == "bytes" || lower == "data" || lower == "imu"
			for _, marker := range []string{"image", "rgb", "depth", "imusample", "imudata", "imuraw", "imu_", "gyro", "accelerometer", "pointcloud", "pointcolors", "rawsensor", "pixels", "samples", "cells", "scans"} {
				if strings.Contains(lower, marker) {
					raw = true
					break
				}
			}
			if !raw {
				if projected := semanticStateValue(item, depth+1); projected != nil {
					clean[key] = projected
				}
			}
		}
		return clean
	case []any:
		if len(typed) > 256 {
			return nil
		}
		clean := make([]any, 0, len(typed))
		for _, item := range typed {
			if projected := semanticStateValue(item, depth+1); projected != nil {
				clean = append(clean, projected)
			}
		}
		return clean
	case string:
		if len(typed) > 2048 {
			return nil
		}
	case []byte:
		return nil
	case []float64:
		if len(typed) > 256 {
			return nil
		}
	case []int:
		if len(typed) > 256 {
			return nil
		}
	}
	return value
}
