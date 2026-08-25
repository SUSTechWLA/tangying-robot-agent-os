package worker

import (
	"context"
	"errors"
	"log"
	"math"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
)

// Raster cells and size: a 15x15 grid at 0.1 m covers ±0.75 m around the
// robot, which encloses the tabletop objects of the MuJoCo scene.
const (
	rasterCells   = 15
	rasterCellM   = 0.1
	trajectoryCap = 600
)

// telemetryLoop reports one sample per interval. The mTLS gRPC Link channel
// is the primary transport; when it is down the worker falls back to the
// HTTP data plane so observability survives control-channel loss.
func (w *Worker) telemetryLoop(ctx context.Context) {
	ticker := time.NewTicker(w.config.TelemetryInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		sample, err := w.buildSample(ctx)
		if err != nil {
			logTelemetryFailure(w.config.RobotID, err)
			continue
		}
		if w.config.Link != nil && w.config.Link.Connected() {
			observationsAccepted := true
			for _, envelope := range w.observationsFromSample(sample) {
				wire, convertErr := observationToProto(envelope)
				if convertErr != nil || w.config.Link.SendObservation(ctx, wire) != nil {
					observationsAccepted = false
					break
				}
			}
			if !observationsAccepted {
				logTelemetryFailure(w.config.RobotID, errors.New("world observation stream unavailable"))
			}
			if err := w.config.Link.SendTelemetry(ctx, sampleToProto(sample)); err == nil {
				continue
			}
		}
		if err := w.config.Cloud.ReportTelemetry(ctx, sample); err != nil {
			logTelemetryFailure(w.config.RobotID, err)
		}
	}
}

// buildSample produces one fleet telemetry sample from the Robot Runtime:
// world-frame pose, entities and a rasterized local occupancy grid.
func (w *Worker) buildSample(ctx context.Context) (fleettelemetry.Sample, error) {
	observer := w.config.Observer
	if observer == nil {
		observer = w.config.Runtime
	}
	if observer == nil {
		return fleettelemetry.Sample{}, errors.New("robot observation provider is required")
	}
	snapshot, err := observer.Telemetry(ctx, "")
	if err != nil {
		return fleettelemetry.Sample{}, err
	}
	sample := w.sampleFromTelemetry(snapshot)
	w.recordCapture(sample.Capture)
	return sample, nil
}

// sampleFromTelemetry is the shared sim/real adapter boundary used by Fleet
// workers and the offline Local Brain. Transport-specific observation bytes
// are normalized before they enter the versioned WorldSnapshot projector.
func (w *Worker) sampleFromTelemetry(snapshot telemetry.Snapshot) fleettelemetry.Sample {
	pose := poseFromRobotState(snapshot.RobotState)
	pose = addWorldOffset(pose, w.config.WorldPose)
	entities := transformEntitiesToWorld(snapshot.Entities, w.config.WorldPose)
	activity := snapshot.Activity
	// The Runtime remains authoritative for physical state and safety. The Edge
	// worker additionally knows when a command is in flight, so expose that
	// orchestration fact while preserving an emergency-stop state reported by
	// the Runtime. This keeps sim and real adapters observable even when their
	// low-level controller only reports a coarse IDLE/STOPPED activity enum.
	if w.commandRunning() && !snapshot.EmergencyStopped && activity != "EMERGENCY_STOPPED" {
		activity = "EXECUTING"
	}
	anomalies := append([]string(nil), snapshot.Anomalies...)
	capture := validatedCapture(snapshot.Capture, &anomalies)
	sample := fleettelemetry.Sample{
		RobotID:          w.config.RobotID,
		Adapter:          w.config.Adapter,
		ObservedAt:       snapshot.ObservedAt,
		Pose:             pose,
		Activity:         activity,
		EmergencyStopped: snapshot.EmergencyStopped,
		Anomalies:        anomalies,
		Entities:         convertEntities(entities),
		State:            numericRobotState(snapshot.RobotState, w.config.RobotID),
		Held:             stringValue(snapshot.RobotState, "held"),
		Placements:       stringMapValue(snapshot.RobotState, "placements"),
		Frame:            append([]byte(nil), snapshot.Frame...),
		FrameMediaType:   snapshot.FrameMediaType,
		Capture:          capture,
	}
	if sample.ObservedAt.IsZero() {
		sample.ObservedAt = time.Now().UTC()
	}
	sample.Occupancy = Rasterize(entities, pose, rasterCells, rasterCellM)
	return sample
}

// stringValue extracts a string field from the runtime robot_state.
func stringValue(state map[string]any, key string) string {
	if value, ok := state[key].(string); ok {
		return value
	}
	return ""
}

// stringMapValue extracts a map[string]string field (e.g. placements).
func stringMapValue(state map[string]any, key string) map[string]string {
	raw, ok := state[key].(map[string]any)
	if !ok {
		return nil
	}
	result := map[string]string{}
	for objectID, destination := range raw {
		if text, ok := destination.(string); ok {
			result[objectID] = text
		}
	}
	if len(result) == 0 {
		return nil
	}
	return result
}

// convertEntities copies core telemetry entities into the fleet telemetry
// entity shape (same fields, distinct packages).
func convertEntities(entities []telemetry.Entity) []fleettelemetry.Entity {
	if len(entities) == 0 {
		return nil
	}
	result := make([]fleettelemetry.Entity, 0, len(entities))
	for _, entity := range entities {
		result = append(result, fleettelemetry.Entity{
			EntityID:   entity.EntityID,
			Category:   entity.Category,
			Attributes: entity.Attributes,
			Pose:       append([]float64(nil), entity.Pose...),
			Confidence: entity.Confidence,
			Relation:   entity.Relation,
		})
	}
	return result
}

// poseFromRobotState extracts [x, y, z, yaw] from the runtime robot_state
// base_pose [x, y, z, qw, qx, qy, qz]. Unknown/malformed state yields the
// zero pose; the caller adds the world offset.
func poseFromRobotState(state map[string]any) []float64 {
	raw, ok := state["base_pose"].([]any)
	if !ok || len(raw) < 3 {
		return []float64{0, 0, 0, 0}
	}
	values := make([]float64, 0, len(raw))
	for _, item := range raw {
		value, ok := toFloat(item)
		if !ok {
			return []float64{0, 0, 0, 0}
		}
		values = append(values, value)
	}
	yaw := 0.0
	if len(values) >= 7 {
		qw, qx, qy, qz := values[3], values[4], values[5], values[6]
		yaw = math.Atan2(2*(qw*qz+qx*qy), 1-2*(qy*qy+qz*qz))
	}
	return []float64{values[0], values[1], values[2], yaw}
}

func addWorldOffset(pose, offset []float64) []float64 {
	if len(offset) == 0 {
		return pose
	}
	result := []float64{pose[0], pose[1], pose[2], pose[3]}
	yawOffset := 0.0
	if len(offset) >= 4 {
		yawOffset = offset[3]
	}
	cosYaw, sinYaw := math.Cos(yawOffset), math.Sin(yawOffset)
	x, y := result[0], result[1]
	result[0] = cosYaw*x - sinYaw*y
	result[1] = sinYaw*x + cosYaw*y
	if len(offset) >= 1 {
		result[0] += offset[0]
	}
	if len(offset) >= 2 {
		result[1] += offset[1]
	}
	if len(offset) >= 3 {
		result[2] += offset[2]
	}
	if len(offset) >= 4 {
		result[3] += offset[3]
	}
	return result
}

func transformEntitiesToWorld(entities []telemetry.Entity, offset []float64) []telemetry.Entity {
	result := make([]telemetry.Entity, 0, len(entities))
	for _, entity := range entities {
		entity.Pose = transformEntityPose(entity.Pose, offset)
		result = append(result, entity)
	}
	return result
}

func transformEntityPose(pose, offset []float64) []float64 {
	result := append([]float64(nil), pose...)
	if len(result) < 2 || len(offset) == 0 {
		return result
	}
	yaw := 0.0
	if len(offset) >= 4 {
		yaw = offset[3]
	}
	cosYaw, sinYaw := math.Cos(yaw), math.Sin(yaw)
	x, y := result[0], result[1]
	result[0] = cosYaw*x - sinYaw*y
	result[1] = sinYaw*x + cosYaw*y
	if len(offset) >= 1 {
		result[0] += offset[0]
	}
	if len(offset) >= 2 {
		result[1] += offset[1]
	}
	if len(result) >= 3 && len(offset) >= 3 {
		result[2] += offset[2]
	}
	if len(result) >= 7 && yaw != 0 {
		cw, sw := math.Cos(yaw/2), math.Sin(yaw/2)
		qw, qx, qy, qz := result[3], result[4], result[5], result[6]
		result[3] = cw*qw - sw*qz
		result[4] = cw*qx - sw*qy
		result[5] = cw*qy + sw*qx
		result[6] = cw*qz + sw*qw
	}
	return result
}

var canonicalJointNames = map[string]string{
	"Rotation_L": "joint.left.rotation", "Pitch_L": "joint.left.pitch",
	"Elbow_L": "joint.left.elbow", "Wrist_Pitch_L": "joint.left.wrist_pitch",
	"Wrist_Roll_L": "joint.left.wrist_roll", "Jaw_L": "joint.left.jaw",
	"Rotation_R": "joint.right.rotation", "Pitch_R": "joint.right.pitch",
	"Elbow_R": "joint.right.elbow", "Wrist_Pitch_R": "joint.right.wrist_pitch",
	"Wrist_Roll_R": "joint.right.wrist_roll", "Jaw_R": "joint.right.jaw",
	"head_pan_joint": "joint.head.pan", "head_tilt_joint": "joint.head.tilt",
}

// canonicalJointState converts Runtime joint observations into the stable
// adapter-neutral state keys consumed by WorldSnapshot clients.
func canonicalJointState(state map[string]any, robotID string) map[string]float64 {
	positions, ok := state["joint_positions"]
	if !ok {
		return nil
	}
	result := map[string]float64{}
	appendJoint := func(name string, raw any) {
		if robotID != "" {
			name = strings.TrimPrefix(name, robotID+"__")
		}
		canonical, ok := canonicalJointNames[name]
		if !ok {
			return
		}
		value, ok := toFloat(raw)
		if !ok || math.IsNaN(value) || math.IsInf(value, 0) {
			return
		}
		result[canonical] = value
	}
	switch typed := positions.(type) {
	case map[string]any:
		for name, value := range typed {
			appendJoint(name, value)
		}
	case map[string]float64:
		for name, value := range typed {
			appendJoint(name, value)
		}
	}
	if len(result) == 0 {
		return nil
	}
	return result
}

// numericRobotState extracts scalar telemetry plus canonical articulated joint
// values for the fleet console. Only finite numbers are kept.
func numericRobotState(state map[string]any, robotID string) map[string]float64 {
	if len(state) == 0 {
		return nil
	}
	keys := []string{"reward", "pick_count", "step_count", "verification_confidence"}
	result := map[string]float64{}
	for _, key := range keys {
		value, ok := toFloat(state[key])
		if ok && !math.IsNaN(value) && !math.IsInf(value, 0) {
			result[key] = value
		}
	}
	for name, value := range canonicalJointState(state, robotID) {
		result[name] = value
	}
	if len(result) == 0 {
		return nil
	}
	return result
}

func toFloat(value any) (float64, bool) {
	switch typed := value.(type) {
	case float64:
		return typed, true
	case float32:
		return float64(typed), true
	case int:
		return float64(typed), true
	case int64:
		return float64(typed), true
	default:
		return 0, false
	}
}

// rasterCategories are the entities that occupy space; the robot itself,
// the table and the floor are structural and excluded from the grid.
var rasterCategories = map[string]bool{
	"cup": true, "bottle": true, "block": true,
	"storage_bin": true, "delivery_tray": true,
}

// Rasterize builds a robot-local occupancy grid from world-frame entities
// and the robot world pose. Each occupying entity marks a 2x2 blob of cells;
// the result is a deterministic pure function.
func Rasterize(entities []telemetry.Entity, robotPose []float64, cells int, cellSize float64) *fleettelemetry.OccupancyGrid {
	if cells <= 0 || cellSize <= 0 {
		cells, cellSize = rasterCells, rasterCellM
	}
	grid := &fleettelemetry.OccupancyGrid{
		Width: cells, Height: cells, CellSize: cellSize,
		OriginX: -float64(cells) * cellSize / 2,
		OriginY: -float64(cells) * cellSize / 2,
		Cells:   make([]byte, cells*cells),
	}
	yaw := 0.0
	if len(robotPose) >= 4 {
		yaw = robotPose[3]
	}
	cosYaw, sinYaw := math.Cos(yaw), math.Sin(yaw)
	half := float64(cells) * cellSize / 2
	for _, entity := range entities {
		if !rasterCategories[entity.Category] || len(entity.Pose) < 2 {
			continue
		}
		// Rotate the entity offset into the robot frame.
		dx := entity.Pose[0] - robotPose[0]
		dy := entity.Pose[1] - robotPose[1]
		localX := dx*cosYaw + dy*sinYaw
		localY := -dx*sinYaw + dy*cosYaw
		ix := int(math.Floor((localX + half) / cellSize))
		iy := int(math.Floor((localY + half) / cellSize))
		markBlob(grid, ix, iy)
	}
	return grid
}

func markBlob(grid *fleettelemetry.OccupancyGrid, ix, iy int) {
	for dy := -1; dy <= 1; dy++ {
		for dx := -1; dx <= 1; dx++ {
			gx, gy := ix+dx, iy+dy
			if gx < 0 || gy < 0 || gx >= grid.Width || gy >= grid.Height {
				continue
			}
			grid.Cells[gy*grid.Width+gx] = 100
		}
	}
}

// sampleToProto converts a fleet telemetry sample into the gRPC Link wire
// message.
func sampleToProto(sample fleettelemetry.Sample) *fleetv1.TelemetrySample {
	proto := &fleetv1.TelemetrySample{
		RobotId:          sample.RobotID,
		ObservedUnixMs:   sample.ObservedAt.UnixMilli(),
		PoseXyzYaw:       append([]float64(nil), sample.Pose...),
		Activity:         sample.Activity,
		EmergencyStopped: sample.EmergencyStopped,
		Anomalies:        append([]string(nil), sample.Anomalies...),
		State:            map[string]float64{},
		Placements:       map[string]string{},
	}
	for key, value := range sample.State {
		proto.State[key] = value
	}
	for _, entity := range sample.Entities {
		proto.Entities = append(proto.Entities, &fleetv1.SceneEntity{
			EntityId:    entity.EntityID,
			Category:    entity.Category,
			Attributes:  entity.Attributes,
			PoseXyzQuat: append([]float64(nil), entity.Pose...),
			Confidence:  entity.Confidence,
			Relation:    entity.Relation,
		})
	}
	if grid := sample.Occupancy; grid != nil {
		proto.Occupancy = &fleetv1.OccupancyGrid{
			Width: int32(grid.Width), Height: int32(grid.Height), CellSizeM: grid.CellSize,
			OriginX: grid.OriginX, OriginY: grid.OriginY, Cells: append([]byte(nil), grid.Cells...),
		}
	}
	proto.Frame = append([]byte(nil), sample.Frame...)
	proto.FrameMediaType = sample.FrameMediaType
	proto.Held = sample.Held
	for objectID, destination := range sample.Placements {
		proto.Placements[objectID] = destination
	}
	if sample.Capture != nil {
		if err := sample.Capture.Validate(); err != nil {
			proto.Anomalies = appendUniqueString(proto.Anomalies, sensors.AnomalyInvalidCapture)
		} else {
			proto.Capture = sensorCaptureToProto(sample.Capture)
		}
	}
	return proto
}

func validatedCapture(capture *sensors.Capture, anomalies *[]string) *sensors.Capture {
	if capture == nil {
		return nil
	}
	if err := capture.Validate(); err != nil {
		*anomalies = appendUniqueString(*anomalies, sensors.AnomalyInvalidCapture)
		return nil
	}
	return capture.Clone()
}

func sensorCaptureToProto(capture *sensors.Capture) *fleetv1.SensorCapture {
	wire := &fleetv1.SensorCapture{
		SchemaVersion: capture.SchemaVersion, CaptureId: capture.CaptureID, RobotId: capture.RobotID, EpisodeId: capture.EpisodeID,
		SimulationStep: capture.SimulationStep, SourceSequence: capture.SourceSequence,
		CapturedUnixMs: capture.CapturedAt.UnixMilli(), FrameId: capture.FrameID,
		TransformRevision: capture.TransformRevision, WorldRevision: capture.WorldRevision,
		Frames: make([]*fleetv1.SensorFrame, 0, len(capture.Frames)),
	}
	for _, frame := range capture.Frames {
		wire.Frames = append(wire.Frames, &fleetv1.SensorFrame{
			SensorId: frame.SensorID, Modality: frame.Modality, MediaType: frame.MediaType,
			Width: uint32(frame.Width), Height: uint32(frame.Height), Sha256: frame.SHA256, Uri: frame.URI,
			DepthScaleM: frame.DepthScaleM, MinRangeM: frame.MinRangeM, MaxRangeM: frame.MaxRangeM,
			Intrinsics:    append([]float64(nil), frame.Intrinsics...),
			CameraToWorld: append([]float64(nil), frame.CameraToWorld...), Data: append([]byte(nil), frame.Data...),
		})
	}
	return wire
}

func appendUniqueString(values []string, value string) []string {
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}

func logTelemetryFailure(robotID string, err error) {
	// Telemetry failures are non-fatal for task execution; log them so the
	// operator can see an uplink problem without any task being affected.
	log.Printf("edge-worker %s: telemetry: %v", robotID, err)
}
