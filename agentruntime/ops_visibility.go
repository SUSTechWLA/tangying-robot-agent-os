package agentruntime

import (
	"encoding/json"
	"math"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

// Observation arrival is not a sensor timestamp. Both the envelope and its
// source must remain usable; a fresh poll cannot refresh cached sensor data.
func telemetryVisibility(snapshot *telemetry.Snapshot, now time.Time, maxAge time.Duration) string {
	if snapshot == nil {
		return "NO_OBSERVATION"
	}
	if snapshot.ObservedAt.IsZero() {
		return "TIMESTAMP_MISSING"
	}
	if snapshot.ObservedAt.After(now.Add(telemetry.ClockSkewAllowance)) {
		return "TIMESTAMP_FUTURE"
	}
	if maxAge <= 0 {
		maxAge = DefaultTelemetryMaxAge
	}
	if now.Sub(snapshot.ObservedAt) > maxAge {
		return "OBSERVATION_STALE"
	}
	if snapshot.Reconstruction != nil && snapshot.Reconstruction.ObservedAtUnixMS <= 0 {
		return "SENSOR_TIMESTAMP_MISSING"
	}
	switch snapshot.EvidenceFreshness(now) {
	case worldmodel.Stale:
		return "SENSOR_STALE"
	case worldmodel.Unknown:
		return "SENSOR_TIME_UNKNOWN"
	}
	return ""
}

const AnomalyObservabilityGap = "ANOMALY_OBSERVABILITY_GAP"

const AnomalyHarnessHealth = "ANOMALY_HARNESS_HEALTH"

// The ROS bridge owns this diagnostic contract. It reports independently aged
// sources; no field here authorizes restart, motion, or release of a safety latch.
func harnessFindings(input ObservationInput, now time.Time) []Finding {
	if input.Snapshot == nil || input.Snapshot.RobotState["harnessHealth"] == nil {
		return nil
	}
	h, ok := validHarness(input.Snapshot, now, input.TelemetryMaxAge)
	makeFinding := func(component, reason string) Finding {
		return Finding{Code: AnomalyHarnessHealth, Component: component, Severity: SeverityWarning,
			Message: "ROS 2 harness 需要检查：" + reason, Confidence: 1,
			Facts:    map[string]any{"reason": reason, "robotId": input.Snapshot.RobotID},
			Evidence: evidenceRefs(input.Snapshot), RecommendedActions: []string{"检查设备侧 harnessHealth、传感器时间与控制器故障；保留命令记录"}}
	}
	if !ok {
		return []Finding{makeFinding("ros2_harness", "CONTRACT_MISMATCH")}
	}
	if telemetryVisibility(input.Snapshot, now, input.TelemetryMaxAge) != "" {
		return nil
	}
	findings := []Finding{}
	if command, ok := h["uncertainCommandId"].(string); ok && command != "" {
		f := makeFinding("ros2_harness.execution", "COMMAND_OUTCOME_UNKNOWN")
		f.Severity, f.AutomaticRetryForbidden = SeverityCritical, true
		f.Facts["commandId"] = command
		f.Binding.RobotID, f.Binding.CommandID = input.Snapshot.RobotID, command
		findings = append(findings, f)
	}
	driver, valid := h["driver"].(map[string]any)
	if !valid || driver["fresh"] != true || driver["healthy"] != true || driver["watchdogActive"] != true || driver["error"] != "" || len(driver["faults"].([]any)) > 0 {
		findings = append(findings, makeFinding("ros2_harness.driver", "DRIVER_NOT_OBSERVABLE_OR_UNHEALTHY"))
	}
	sensors, valid := h["sensors"].([]any)
	if !valid || len(sensors) > 128 {
		return append(findings, makeFinding("ros2_harness.sensors", "SENSOR_CONTRACT_INVALID"))
	}
	for _, value := range sensors {
		sensor, valid := value.(map[string]any)
		id, identified := sensor["sourceId"].(string)
		if !valid || !identified || id == "" {
			findings = append(findings, makeFinding("ros2_harness.sensors", "SENSOR_CONTRACT_INVALID"))
			continue
		}
		if sensor["state"] != "fresh" {
			f := makeFinding("ros2_harness.sensor."+id, "SENSOR_NOT_FRESH")
			f.Facts["sourceId"], f.Facts["sensorState"] = id, sensor["state"]
			findings = append(findings, f)
		}
	}
	return findings
}

// Failed reads are not empty results. Keep prior conditions open until their
// own authoritative source can establish that they have cleared.
func (a *OpsAgent) retainUnobserved(findings []Finding, taskID string, input ObservationInput, readable map[string]bool) []Finding {
	current := map[openCondition]bool{}
	for _, f := range findings {
		id := f.TaskID
		if id == "" {
			id = taskID
		}
		current[openCondition{taskID: id, identity: agentcontract.AnomalyIdentity(f.Code, f.Component), source: bindingKey(f.Binding)}] = true
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	for condition, previous := range a.conditions {
		canRead, limited := readable[previous.Code]
		if previous.Code == AnomalyHarnessHealth {
			canRead = harnessCanClear(previous, input)
		}
		if priorRobot, ok := previous.Facts["robotId"].(string); ok && input.Snapshot != nil && priorRobot != input.Snapshot.RobotID {
			canRead = false
		}
		if !limited || canRead || current[condition] {
			continue
		}
		retained := previous
		retained.TaskID = condition.taskID
		retained.Facts = clonePayload(previous.Facts)
		if retained.Facts == nil {
			retained.Facts = map[string]any{}
		}
		retained.Facts["observationUnavailable"] = true
		retained.MissingEvidence = []string{"当前权威来源不可读或已过期；保留历史异常，未确认恢复"}
		findings = append(findings, retained)
	}
	sortFindings(findings)
	return findings
}

// Validate the diagnostic fields used to establish negative evidence. A fresh
// outer telemetry envelope cannot renew a stale or incomplete harness snapshot.
func validHarness(snapshot *telemetry.Snapshot, now time.Time, maxAge time.Duration) (map[string]any, bool) {
	if telemetryVisibility(snapshot, now, maxAge) != "" {
		return nil, false
	}
	raw, err := json.Marshal(snapshot.RobotState["harnessHealth"])
	if err != nil {
		return nil, false
	}
	var h map[string]any
	if json.Unmarshal(raw, &h) != nil || h["schemaVersion"] != "ros2.harness.health.v1" || h["robotId"] != snapshot.RobotID {
		return nil, false
	}
	stamp, ok := h["observedAtUnixMs"].(float64)
	if !ok || stamp <= 0 || stamp > float64(now.Add(telemetry.ClockSkewAllowance).UnixMilli()) {
		return nil, false
	}
	if maxAge <= 0 {
		maxAge = DefaultTelemetryMaxAge
	}
	if float64(now.UnixMilli())-stamp > float64(maxAge.Milliseconds()) {
		return nil, false
	}
	if _, ok := h["uncertainCommandId"].(string); !ok {
		return nil, false
	}
	driver, ok := h["driver"].(map[string]any)
	if !ok {
		return nil, false
	}
	fresh, ok := driver["fresh"].(bool)
	if !ok {
		return nil, false
	}
	for _, key := range []string{"healthy", "watchdogActive"} {
		value, present := driver[key]
		// The bridge explicitly reports null when driver state is unavailable.
		// Unknown health is a driver finding, not a malformed whole harness;
		// rejecting it would hide an independent unknown-command latch.
		if !present {
			return nil, false
		}
		if _, ok := value.(bool); !ok && (fresh || value != nil) {
			return nil, false
		}
	}
	if _, ok := driver["error"].(string); !ok {
		return nil, false
	}
	faults, ok := driver["faults"].([]any)
	if !ok || len(faults) > 128 {
		return nil, false
	}
	sensors, ok := h["sensors"].([]any)
	if !ok || len(sensors) > 128 {
		return nil, false
	}
	ids := map[string]bool{}
	for _, raw := range sensors {
		sensor, ok := raw.(map[string]any)
		if !ok {
			return nil, false
		}
		id, ok := sensor["sourceId"].(string)
		if !ok || id == "" || ids[id] {
			return nil, false
		}
		ids[id] = true
		state, ok := sensor["state"].(string)
		if !ok || (state != "fresh" && state != "expired" && state != "never_received") {
			return nil, false
		}
	}
	return h, true
}

func harnessCanClear(previous Finding, input ObservationInput) bool {
	h, ok := validHarness(input.Snapshot, input.Now, input.TelemetryMaxAge)
	if !ok {
		return false
	}
	switch previous.Component {
	case "ros2_harness.execution":
		// A new bridge process starts with an empty uncertain-command latch.
		// Absence alone therefore cannot reconcile a previously dispatched
		// command. Only its own known terminal record can close this finding;
		// a stop ACK, another command or fresh driver state cannot do so.
		if h["uncertainCommandId"] != "" || previous.Binding.CommandID == "" {
			return false
		}
		result, ok := h["lastExecution"].(map[string]any)
		if !ok || result["commandId"] != previous.Binding.CommandID || (result["state"] != "succeeded" && result["state"] != "failed") {
			return false
		}
		code, ok := result["code"].(string)
		if !ok || strings.TrimSpace(code) == "" || code == "EXECUTION_OUTCOME_UNKNOWN" {
			return false
		}
		completed, ok := result["completedAtUnixMs"].(float64)
		return ok && completed > 0 && math.Trunc(completed) == completed && completed <= h["observedAtUnixMs"].(float64)
	case "ros2_harness.driver":
		d := h["driver"].(map[string]any)
		return d["fresh"] == true && d["healthy"] == true && d["watchdogActive"] == true && d["error"] == "" && len(d["faults"].([]any)) == 0
	}
	if id, sensor := strings.CutPrefix(previous.Component, "ros2_harness.sensor."); sensor {
		for _, value := range h["sensors"].([]any) {
			source := value.(map[string]any)
			if source["sourceId"] == id {
				return source["state"] == "fresh"
			}
		}
		return false
	}
	return true
}
