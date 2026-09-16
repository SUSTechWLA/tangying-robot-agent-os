package worker

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

func stoppedRobotFaults() *robotcontract.FaultReport {
	age := int64(12_000)
	return &robotcontract.FaultReport{
		SchemaVersion: "robot.faults.v1", Severity: "safety", Count: 1,
		Faults: []robotcontract.Fault{
			{ModuleID: "estop", Kind: "custom", Code: "EMERGENCY_STOP_LATCHED", Severity: "safety",
				Detail: "急停已锁存，软件不会自动复位", Occurrences: 1, Remedy: "operator_assist",
				UserInstruction:  "机器人处于急停：排除危险后现场复位急停，再继续任务。",
				DetectedAtUnixMS: 1_780_000_000_000, LastSeenUnixMS: 1_780_000_000_000, AgeMS: &age,
				Evidence: map[string]string{"latch": "hardware"}},
		},
		OperatorActions:         []string{"机器人处于急停：排除危险后现场复位急停，再继续任务。"},
		UnavailableCapabilities: []string{"manipulation.pick", "navigation.navigate", "observe_scene"},
	}
}

func faultSnapshot() telemetry.Snapshot {
	snapshot := contractSnapshot()
	snapshot.Faults = stoppedRobotFaults()
	return snapshot
}

func TestFaultsReachTheWorldFactAndTheFleetTelemetry(t *testing.T) {
	w := New(Config{RobotID: "mobile-7", Adapter: "mujoco", AdapterVersion: "1", TransformRevision: "base-cal-2"})
	sample := w.sampleFromTelemetry(faultSnapshot())
	if sample.Faults == nil || !sample.Faults.SafetyStopped() {
		t.Fatalf("the sample must carry the robot's own fault view: %#v", sample.Faults)
	}
	// A fleet operator reads telemetry, not the local world model, so faults are
	// deliberately part of the small public JSON as well.
	wire, err := json.Marshal(sample)
	if err != nil {
		t.Fatal(err)
	}
	var public map[string]any
	if err := json.Unmarshal(wire, &public); err != nil {
		t.Fatal(err)
	}
	faults, ok := public["faults"].(map[string]any)
	if !ok || faults["severity"] != "safety" {
		t.Fatalf("faults must survive low-rate fleet JSON, got %v", public["faults"])
	}
	if !strings.Contains(wire2String(t, public), "现场复位急停") {
		t.Fatal("the operator instruction must reach the telemetry reader")
	}

	event := w.observationsFromSample(sample)[0]
	payload, ok := event.Payload.(observation.RobotPayload)
	if !ok || payload.Faults == nil || payload.Faults.Count != 1 {
		t.Fatalf("the robot-state world fact must carry the faults: %#v", event.Payload)
	}
	if err := event.Validate(); err != nil {
		t.Fatalf("a robot fact with a valid fault report must validate: %v", err)
	}
}

func TestAWorldFactWithAnInconsistentFaultListIsRefused(t *testing.T) {
	w := New(Config{RobotID: "mobile-7", Adapter: "mujoco", AdapterVersion: "1"})
	event := w.observationsFromSample(w.sampleFromTelemetry(faultSnapshot()))[0]
	payload := event.Payload.(observation.RobotPayload)
	payload.Faults = &robotcontract.FaultReport{SchemaVersion: "robot.faults.v1", Severity: "info", Count: 4}
	event.Payload = payload
	if err := event.Validate(); err == nil {
		t.Fatal("a fault list that disagrees with itself must not enter the world model")
	}
}

func TestTheWorldSnapshotCarriesFaultsPerRobot(t *testing.T) {
	w := New(Config{RobotID: "mobile-7", Adapter: "mujoco", AdapterVersion: "1", WorldID: "world-1", TransformRevision: "base-cal-2"})
	events := w.observationsFromSample(w.sampleFromTelemetry(faultSnapshot()))
	projector := worldmodel.NewProjector("world-1", 30*time.Second)
	for _, event := range events {
		if _, _, err := projector.Apply(event); err != nil {
			t.Fatal(err)
		}
	}
	snapshot := projector.Snapshot()
	robot, found := snapshot.Robots["mobile-7"]
	if !found || robot.Faults == nil {
		t.Fatalf("the world snapshot must tell an Agent which module is broken: %#v", snapshot.Robots)
	}
	if !robot.Faults.SafetyStopped() || robot.Faults.Faults[0].ModuleID != "estop" {
		t.Fatalf("unexpected fault state in the world snapshot: %#v", robot.Faults)
	}
	if got := robot.Faults.Faults[0].Evidence["latch"]; got != "hardware" {
		t.Fatalf("evidence must be copied into the world snapshot, got %q", got)
	}
	// The snapshot is handed to readers: mutating it must not reach the projector.
	robot.Faults.Faults[0].Evidence["latch"] = "tampered"
	robot.Faults.Faults[0].Code = "TAMPERED"
	again := projector.Snapshot()
	if again.Robots["mobile-7"].Faults.Faults[0].Code == "TAMPERED" {
		t.Fatal("a reader must not be able to rewrite the projected fault state")
	}
}

func wire2String(t *testing.T, values map[string]any) string {
	t.Helper()
	wire, err := json.Marshal(values)
	if err != nil {
		t.Fatal(err)
	}
	return string(wire)
}
