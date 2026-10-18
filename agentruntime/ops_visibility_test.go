package agentruntime_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestOpsHealthRejectsUnusableSensorTime(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	for name, snapshot := range map[string]telemetry.Snapshot{
		"missing": {RobotID: "r"},
		"stale":   {RobotID: "r", ObservedAt: now.Add(-time.Minute)},
		"future":  {RobotID: "r", ObservedAt: now.Add(time.Minute)},
		"sensor_stale_behind_fresh_envelope": {RobotID: "r", ObservedAt: now,
			RobotProfile:   &robotcontract.Profile{Sensors: []robotcontract.Sensor{{SourceID: "lidar", MaxAgeMS: 100}}},
			Reconstruction: &robotcontract.Reconstruction{SourceID: "lidar", ObservedAtUnixMS: now.Add(-time.Second).UnixMilli()}},
	} {
		t.Run(name, func(t *testing.T) {
			a := agentruntime.NewOpsAgent()
			a.Now = func() time.Time { return now }
			a.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) { return snapshot, nil }
			if health := a.Health(context.Background()); health.Status != agentcontract.HealthDegraded {
				t.Fatalf("unusable source reported healthy: %#v", health)
			}
			if _, ok := findByCode(a.Observe(context.Background()), agentruntime.AnomalyTelemetryStale); !ok {
				t.Fatal("missing sensor freshness anomaly")
			}
		})
	}
}

func TestOpsDoesNotClearSafetyWhenTelemetryDisappears(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	a := agentruntime.NewOpsAgent()
	a.Now = func() time.Time { return now }
	fail, stopped := false, true
	a.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		if fail {
			return telemetry.Snapshot{}, errors.New("link lost")
		}
		return telemetry.Snapshot{RobotID: "r", ObservedAt: now, EmergencyStopped: stopped}, nil
	}
	var cleared []agentcontract.Event
	a.Publish = func(_ context.Context, e agentcontract.Event) {
		if e.Topic == agentcontract.TopicOpsAnomalyCleared && e.Payload["code"] == agentruntime.AnomalySafetyStop {
			cleared = append(cleared, e)
		}
	}
	a.Observe(context.Background())
	fail = true
	a.Observe(context.Background())
	if len(cleared) != 0 {
		t.Fatal("lost telemetry was mistaken for cleared emergency stop")
	}
	fail, stopped = false, false
	a.Observe(context.Background())
	if len(cleared) != 1 {
		t.Fatalf("fresh evidence should close exactly once: %d", len(cleared))
	}
}

type retryHistory struct {
	fail       bool
	eventsFail bool
}

func (h *retryHistory) TaskIDs(context.Context) ([]string, error) {
	if h.fail {
		return nil, errors.New("database unavailable")
	}
	return []string{"previous-task"}, nil
}
func (h *retryHistory) Abnormal(context.Context) ([]string, error) { return nil, nil }
func (h *retryHistory) Events(context.Context, string) ([]agentcontract.TaskEvent, error) {
	if h.eventsFail {
		return nil, errors.New("ledger unavailable")
	}
	return []agentcontract.TaskEvent{{Type: "action.executed", Payload: map[string]any{"toolName": "navigation.navigate", "activityStatus": "FAILED", "stepId": "s", "error": "GRASP_NOT_REACHED"}}}, nil
}

func TestOpsRetriesIncompleteStartupHistory(t *testing.T) {
	for _, failEvents := range []bool{false, true} {
		h := &retryHistory{fail: !failEvents, eventsFail: failEvents}
		a := agentruntime.NewOpsAgent()
		a.Telemetry, a.History = quietTelemetry(), h
		a.Observe(context.Background())
		if a.Health(context.Background()).Status != agentcontract.HealthDegraded {
			t.Fatal("failed history read reported healthy")
		}
		h.fail, h.eventsFail = false, false
		finding, ok := findByCode(a.Observe(context.Background()), agentruntime.AnomalyActionFailed)
		if !ok || finding.TaskID != "previous-task" {
			t.Fatalf("startup never retried: %#v", finding)
		}
		if _, ok := findByCode(a.Observe(context.Background()), agentruntime.AnomalyActionFailed); ok {
			t.Fatal("completed history was counted again")
		}
	}
}

type visibilityExecution struct{ fail, uncertain bool }

func (s *visibilityExecution) Steps(context.Context, string) ([]agentcontract.StepRecord, error) {
	return nil, nil
}
func (s *visibilityExecution) Uncertain(_ context.Context, id string) ([]agentcontract.StepRecord, error) {
	if s.fail {
		return nil, errors.New("execution store unavailable")
	}
	if s.uncertain {
		return []agentcontract.StepRecord{{TaskID: id, StepID: "move", Status: "STARTED"}}, nil
	}
	return nil, nil
}

func TestOpsStoreReadFailureKeepsUnknownActionOpen(t *testing.T) {
	a := agentruntime.NewOpsAgent()
	a.Telemetry = quietTelemetry()
	s := &visibilityExecution{uncertain: true}
	a.Memory.Execution = s
	_ = a.OnEvent(context.Background(), agentcontract.Event{Topic: agentcontract.TopicTaskStarted, TaskID: "t"})
	cleared := 0
	a.Publish = func(_ context.Context, e agentcontract.Event) {
		if e.Topic == agentcontract.TopicOpsAnomalyCleared && e.Payload["code"] == agentruntime.AnomalyUnverifiedMutation {
			cleared++
		}
	}
	a.Observe(context.Background())
	s.fail = true
	f, ok := findByCode(a.Observe(context.Background()), agentruntime.AnomalyUnverifiedMutation)
	if !ok || !f.AutomaticRetryForbidden || cleared != 0 {
		t.Fatalf("lost pending action during database outage: %#v", f)
	}
	if a.Health(context.Background()).Status != agentcontract.HealthDegraded {
		t.Fatal("unreadable store reported healthy")
	}
	s.fail, s.uncertain = false, false
	a.Observe(context.Background())
	if cleared != 1 {
		t.Fatal("readable reconciled record did not close once")
	}
}

func TestOpsConsumesPerSensorHarnessDiagnostics(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	a := agentruntime.NewOpsAgent()
	a.Now = func() time.Time { return now }
	h := map[string]any{"schemaVersion": "ros2.harness.health.v1", "robotId": "r", "observedAtUnixMs": now.UnixMilli(), "uncertainCommandId": "command-3",
		"driver":  map[string]any{"fresh": true, "healthy": true, "watchdogActive": true, "error": "", "faults": []any{}},
		"sensors": []any{map[string]any{"sourceId": "lidar", "state": "expired"}}}
	a.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{RobotID: "r", ObservedAt: now, RobotState: map[string]any{"harnessHealth": h}}, nil
	}
	findings := a.Observe(context.Background())
	unknown, sensor := false, false
	for _, f := range findings {
		if f.Component == "ros2_harness.execution" {
			unknown = f.AutomaticRetryForbidden && f.Binding.CommandID == "command-3"
		}
		if f.Component == "ros2_harness.sensor.lidar" {
			sensor = f.Facts["sensorState"] == "expired"
		}
	}
	if !unknown || !sensor {
		t.Fatalf("harness diagnostics were not consumed: %#v", findings)
	}
	for _, replacement := range []map[string]any{
		{"schemaVersion": "bad"},
		{"schemaVersion": "ros2.harness.health.v1", "robotId": "r", "observedAtUnixMs": now.Add(-time.Minute).UnixMilli(), "uncertainCommandId": "", "driver": h["driver"], "sensors": []any{}},
		{"schemaVersion": "ros2.harness.health.v1", "robotId": "r", "observedAtUnixMs": now.UnixMilli(), "driver": h["driver"], "sensors": []any{}},
	} {
		original := h
		h = replacement
		retained := a.Observe(context.Background())
		unknown, sensor = false, false
		for _, f := range retained {
			unknown = unknown || f.Component == "ros2_harness.execution" && f.AutomaticRetryForbidden
			sensor = sensor || f.Component == "ros2_harness.sensor.lidar"
		}
		if !unknown || !sensor {
			t.Fatalf("incomplete/stale harness erased unresolved facts: %#v", retained)
		}
		h = original
	}
	h["uncertainCommandId"] = ""
	h["lastExecution"] = map[string]any{"state": "succeeded", "commandId": "command-3", "code": "OK", "completedAtUnixMs": now.UnixMilli()}
	h["sensors"] = []any{map[string]any{"sourceId": "lidar", "state": "fresh"}}
	if len(a.Observe(context.Background())) != 0 {
		t.Fatal("fresh healthy harness retained stale anomaly")
	}
	h["driver"].(map[string]any)["faults"] = []any{"drive_fault"}
	if _, ok := findByCode(a.Observe(context.Background()), agentruntime.AnomalyHarnessHealth); !ok {
		t.Fatal("explicit controller fault was hidden by healthy=true")
	}
}

// Ros2ProfileBridge emits null healthy/watchdogActive values when no usable
// driver state exists. Those unknown fields must not hide the independently
// retained command latch or per-sensor diagnostics on the first Ops read.
func TestOpsRetainsHarnessUnknownWhenDriverStateDisappears(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	h := map[string]any{"schemaVersion": "ros2.harness.health.v1", "robotId": "r", "observedAtUnixMs": now.UnixMilli(), "uncertainCommandId": "command-lost",
		"driver":  map[string]any{"fresh": false, "healthy": nil, "watchdogActive": nil, "error": "NO_DRIVER_STATE", "faults": []any{}},
		"sensors": []any{map[string]any{"sourceId": "lidar", "state": "expired"}}}
	a := agentruntime.NewOpsAgent()
	a.Now = func() time.Time { return now }
	a.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{RobotID: "r", ObservedAt: now, RobotState: map[string]any{"harnessHealth": h}}, nil
	}
	findings := a.Observe(context.Background())
	unknown, driver, sensor := false, false, false
	for _, f := range findings {
		unknown = unknown || f.Component == "ros2_harness.execution" && f.Severity == agentruntime.SeverityCritical && f.AutomaticRetryForbidden && f.Binding.CommandID == "command-lost"
		driver = driver || f.Component == "ros2_harness.driver"
		sensor = sensor || f.Component == "ros2_harness.sensor.lidar"
	}
	if !unknown || !driver || !sensor {
		t.Fatalf("legitimate missing driver state hid independent harness findings: %#v", findings)
	}
	// A controller cannot assert freshness while withholding health/watchdog
	// evidence; incomplete fields also cannot confirm recovery.
	h["driver"].(map[string]any)["fresh"] = true
	h["uncertainCommandId"] = ""
	h["sensors"] = []any{map[string]any{"sourceId": "lidar", "state": "fresh"}}
	findings = a.Observe(context.Background())
	unknown = false
	for _, f := range findings {
		unknown = unknown || f.Component == "ros2_harness.execution" && f.AutomaticRetryForbidden
	}
	if !unknown {
		t.Fatal("malformed healthy driver state cleared unresolved command")
	}
}

func TestOpsHarnessUnknownRequiresMatchingKnownTerminalEvidence(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	terminal := func(command, state, code string, completed any) map[string]any {
		return map[string]any{"commandId": command, "state": state, "code": code, "completedAtUnixMs": completed}
	}
	for name, next := range map[string]any{
		"restart_idle":        map[string]any{"state": "idle", "commandId": ""},
		"missing":             nil,
		"different_command":   terminal("other", "succeeded", "OK", now.UnixMilli()),
		"not_dispatched":      terminal("lost", "not_dispatched", "REJECTED", now.UnixMilli()),
		"procedure_entered":   terminal("lost", "procedure_entered", "OK", now.UnixMilli()),
		"unknown_code":        terminal("lost", "failed", "EXECUTION_OUTCOME_UNKNOWN", now.UnixMilli()),
		"missing_code":        terminal("lost", "succeeded", "", now.UnixMilli()),
		"missing_completion":  terminal("lost", "succeeded", "OK", nil),
		"future_completion":   terminal("lost", "succeeded", "OK", now.Add(time.Minute).UnixMilli()),
		"fractional_time":     terminal("lost", "succeeded", "OK", float64(now.UnixMilli())+.5),
		"negative_completion": terminal("lost", "succeeded", "OK", -1),
	} {
		t.Run(name, func(t *testing.T) {
			h := map[string]any{"schemaVersion": "ros2.harness.health.v1", "robotId": "r", "observedAtUnixMs": now.UnixMilli(), "uncertainCommandId": "lost",
				"driver":  map[string]any{"fresh": true, "healthy": true, "watchdogActive": true, "error": "", "faults": []any{}},
				"sensors": []any{}}
			a := agentruntime.NewOpsAgent()
			a.Now = func() time.Time { return now }
			a.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
				return telemetry.Snapshot{RobotID: "r", ObservedAt: now, RobotState: map[string]any{"harnessHealth": h}}, nil
			}
			cleared := 0
			a.Publish = func(_ context.Context, e agentcontract.Event) {
				if e.Topic == agentcontract.TopicOpsAnomalyCleared && e.Payload["component"] == "ros2_harness.execution" {
					cleared++
				}
			}
			a.Observe(context.Background())
			h["uncertainCommandId"], h["lastExecution"] = "", next
			f, ok := findByCode(a.Observe(context.Background()), agentruntime.AnomalyHarnessHealth)
			if !ok || f.Binding.CommandID != "lost" || !f.AutomaticRetryForbidden || cleared != 0 {
				t.Fatalf("unknown cleared without matching terminal evidence: findings=%#v cleared=%d", f, cleared)
			}
			// This is a diagnostic-consumer contract test. It does not add a
			// bridge reset/reconciliation API or authorize physical execution.
			h["lastExecution"] = terminal("lost", "failed", "CONTROLLER_REJECTED", now.UnixMilli())
			if f, ok := findByCode(a.Observe(context.Background()), agentruntime.AnomalyHarnessHealth); ok || cleared != 1 {
				t.Fatalf("matching known result did not clear exactly once: finding=%#v cleared=%d", f, cleared)
			}
		})
	}
}
