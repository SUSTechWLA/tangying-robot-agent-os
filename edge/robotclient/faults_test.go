package robotclient

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

func faultObservation(t *testing.T, faults any) *robotv1.Observation {
	t.Helper()
	state, err := structpb.NewStruct(map[string]any{"faults": faults})
	if err != nil {
		t.Fatal(err)
	}
	return &robotv1.Observation{RobotState: state}
}

func latchedStopFaults() map[string]any {
	return map[string]any{
		"schemaVersion": "robot.faults.v1", "severity": "safety", "count": 1,
		"faults": []any{map[string]any{
			"moduleId": "estop", "kind": "custom", "code": "EMERGENCY_STOP_LATCHED",
			"severity": "safety", "detail": "急停已锁存，软件不会自动复位", "occurrences": 1,
			"remedy": "operator_assist", "userInstruction": "机器人处于急停：排除危险后现场复位急停，再继续任务。",
			"detectedAtUnixMs": 1_700_000_000_000, "lastSeenUnixMs": 1_700_000_010_000,
			"ageMs": 10_000, "sinceLastSeenMs": 0, "evidence": map[string]any{},
		}},
		"unavailableCapabilities": []any{"manipulation.pick", "observe_scene"},
	}
}

func TestAPublishedFaultReportTravelsWithTheObservation(t *testing.T) {
	report, err := acceptFaults(faultObservation(t, latchedStopFaults()))
	if err != nil {
		t.Fatal(err)
	}
	if report == nil || !report.SafetyStopped() {
		t.Fatalf("the latched stop must arrive as a safety fault: %#v", report)
	}
	if got := report.Faults[0].UserInstruction; !strings.Contains(got, "现场复位急停") {
		t.Fatalf("the operator instruction must survive the protobuf boundary, got %q", got)
	}
}

func TestAnOlderRuntimeWithoutFaultsIsUnknownNotHealthy(t *testing.T) {
	for name, observation := range map[string]*robotv1.Observation{
		"no robot state at all":  {},
		"robot state without it": {RobotState: mustStruct(t, map[string]any{"held": ""})},
	} {
		report, err := acceptFaults(observation)
		if err != nil || report != nil {
			t.Fatalf("%s must read as unknown, got %#v / %v", name, report, err)
		}
	}
}

func TestAMalformedFaultReportIsRefusedRatherThanPassedOn(t *testing.T) {
	for name, faults := range map[string]any{
		"not an object":     "no faults",
		"missing severity":  map[string]any{"schemaVersion": "robot.faults.v1", "count": 0, "faults": []any{}},
		"count disagrees":   map[string]any{"schemaVersion": "robot.faults.v1", "severity": "info", "count": 3, "faults": []any{}},
		"unknown severity":  map[string]any{"schemaVersion": "robot.faults.v1", "severity": "bad", "count": 0, "faults": []any{}},
		"a later schema":    map[string]any{"schemaVersion": "robot.faults.v9", "severity": "info", "count": 0, "faults": []any{}},
		"null inside entry": map[string]any{"schemaVersion": "robot.faults.v1", "severity": "info", "count": 0, "faults": []any{}, "operatorActions": nil},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := acceptFaults(faultObservation(t, faults)); err == nil {
				t.Fatalf("%s must be refused: a fault list gates capabilities", name)
			}
		})
	}
}

func mustStruct(t *testing.T, values map[string]any) *structpb.Struct {
	t.Helper()
	state, err := structpb.NewStruct(values)
	if err != nil {
		t.Fatal(err)
	}
	return state
}

// The robot's self-repair record travels with the observation.
//
// `remedyOutcomes` is the robot's account of what its recovery engine did about
// each current fault: what it tried, for how long, and whether the fault left the
// ledger afterwards. It sits beside the fault report rather than inside it,
// because `robot.faults.v1` is a strict contract and the agent's decoder refuses
// unknown fields — putting it inside made the robot's entire fault report
// rejected, which the contract test caught.
//
// Nothing interprets it yet. It is asserted here so that when something does, the
// transport is already guaranteed rather than incidental: `RobotState` is copied
// whole, so any key the robot publishes survives, and this test is what keeps that
// true if the projection is ever narrowed.
func TestTheRobotsSelfRepairRecordTravelsWithTheObservation(t *testing.T) {
	state, err := structpb.NewStruct(map[string]any{
		"faults": latchedStopFaults(),
		"remedyOutcomes": []any{map[string]any{
			"faultKey": "estop:EMERGENCY_STOP_LATCHED",
			"resolved": false, "escalated": true,
			"instruction": "机器人处于急停：排除危险后现场复位急停，再继续任务。",
			"reason":      "故障声明为 operator_assist，需要人处理，自动恢复不介入",
			"attempts":    []any{},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	snapshot := observationToTelemetry(runtime.Snapshot{}, &robotv1.Observation{RobotState: state}, "task-1")

	raw, published := snapshot.RobotState["remedyOutcomes"]
	if !published {
		t.Fatal("the self-repair record did not survive the projection")
	}
	outcomes, ok := raw.([]any)
	if !ok || len(outcomes) != 1 {
		t.Fatalf("remedyOutcomes = %#v, want one entry", raw)
	}
	entry, _ := outcomes[0].(map[string]any)
	if entry["faultKey"] != "estop:EMERGENCY_STOP_LATCHED" {
		t.Fatalf("faultKey = %v", entry["faultKey"])
	}
	// The operator-facing sentence has to survive verbatim: it is the same one the
	// fault report carries, and a second wording would be a second answer.
	if entry["instruction"] != "机器人处于急停：排除危险后现场复位急停，再继续任务。" {
		t.Fatalf("instruction = %v", entry["instruction"])
	}

	// And the fault report is still read as the typed document, not as a map key.
	report, err := acceptFaults(&robotv1.Observation{RobotState: state})
	if err != nil || report == nil || !report.SafetyStopped() {
		t.Fatalf("the fault report stopped being readable beside it: %v %#v", err, report)
	}
}
