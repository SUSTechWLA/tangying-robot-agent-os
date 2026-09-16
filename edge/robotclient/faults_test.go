package robotclient

import (
	"strings"
	"testing"

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
