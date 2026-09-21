package tasks

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestGroundedWorldSurvivesServiceRestartAndFiltersAdapter(t *testing.T) {
	t.Setenv("TANGYING_GVF_ENABLED", "1")
	store := NewMemoryStore()
	for _, adapter := range []string{"gazebo", "mujoco"} {
		task := &Task{ID: adapter, Adapter: adapter}
		for i := 0; i < 4; i++ {
			raw, _ := json.Marshal(map[string]any{"schema_version": "state-report.v1", "report_id": adapter + time.Duration(i).String(), "action_id": "action", "action_name": "navigation.navigate", "task_id": adapter, "edge_boot_id": "boot", "edge_monotonic_ts_ns": 123, "logical_clock": i + 1, "verifier_version": "gvf-1.0.0", "verdict": "UNKNOWN", "failure_type": "EVIDENCE_INSUFFICIENT"})
			task.Events = append(task.Events, TaskEvent{Type: "STATE_REPORT", OccurredAt: time.Unix(int64(i), 0), Payload: map[string]any{"state_report_json": string(raw)}})
		}
		task.Events = append(task.Events, TaskEvent{Type: "TELEMETRY", Payload: map[string]any{"state_report_json": "legacy raw fact"}})
		if err := store.Create(context.Background(), task); err != nil {
			t.Fatal(err)
		}
	}
	world := NewService(store, nil).worldFor("gazebo")
	if !world.GroundedOnly || len(world.StateReports) != 3 {
		t.Fatalf("bad report projection: %+v", world)
	}
	world.RobotRoom = "legacy-secret-room"
	description := world.Describe()
	if strings.Contains(description, "legacy") || strings.Contains(description, "mujoco") || !strings.Contains(description, "UNKNOWN") {
		t.Fatal(description)
	}
}
