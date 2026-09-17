package tasks_test

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// An observation with no adapter cannot be filed, and the hub must say so.
//
// The counter exists because the silence was expensive: a robot whose every
// observation arrived without an adapter looked exactly like a robot that was not
// sending any. The console reported "还没有可用的机器人观测", readiness raised
// ANOMALY_TELEMETRY_STALE, task grounding failed with objects=0 — and nothing
// anywhere said the observations were arriving and being discarded.
func TestASnapshotWithNoAdapterIsCountedRatherThanSilentlyDropped(t *testing.T) {
	hub := tasks.NewTelemetryHub()
	if count, _ := hub.Unfiled(); count != 0 {
		t.Fatalf("a fresh hub already reports %d unfiled observations", count)
	}

	hub.Publish(telemetry.Snapshot{RobotState: map[string]any{"objects": []any{}}})

	count, at := hub.Unfiled()
	if count != 1 {
		t.Fatalf("unfiled = %d, want 1: the observation vanished without a trace", count)
	}
	if at.IsZero() {
		t.Fatal("no time was recorded for the unfiled observation")
	}
	if _, ok := hub.Latest(""); ok {
		t.Fatal("an adapter-less observation was filed under the empty name")
	}
}

// Naming an adapter files it, and does not count as unfiled.
func TestANamedSnapshotIsFiledAndNotCounted(t *testing.T) {
	hub := tasks.NewTelemetryHub()
	hub.Publish(telemetry.Snapshot{Adapter: "mujoco"})

	if count, _ := hub.Unfiled(); count != 0 {
		t.Fatalf("unfiled = %d, want 0", count)
	}
	if _, ok := hub.Latest("mujoco"); !ok {
		t.Fatal("a named observation was not filed")
	}
}
