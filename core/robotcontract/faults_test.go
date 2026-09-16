package robotcontract

import (
	"strings"
	"testing"
)

// cleanFaults is what a healthy robot publishes: an empty report. It must decode,
// so "no faults" is a value the world model can store rather than an absent key.
func cleanFaults() map[string]any {
	return map[string]any{
		"schemaVersion": "robot.faults.v1", "severity": "info", "count": float64(0),
		"faults": []any{}, "operatorActions": []any{},
	}
}

func safetyStoppedFaults() map[string]any {
	return map[string]any{
		"schemaVersion": "robot.faults.v1", "severity": "safety", "count": float64(1),
		"faults": []any{map[string]any{
			"moduleId": "estop", "kind": "custom", "code": "EMERGENCY_STOP_LATCHED",
			"severity": "safety", "detail": "急停已锁存，软件不会自动复位",
			"occurrences": float64(1), "remedy": "operator_assist",
			"userInstruction":  "机器人处于急停：排除危险后现场复位急停，再继续任务。",
			"detectedAtUnixMs": float64(1_700_000_000_000), "lastSeenUnixMs": float64(1_700_000_010_000),
			"ageMs": float64(10_000), "sinceLastSeenMs": float64(0),
			"evidence": map[string]any{},
		}},
		"operatorActions":         []any{"机器人处于急停：排除危险后现场复位急停，再继续任务。"},
		"unavailableCapabilities": []any{"manipulation.pick", "navigation.navigate", "observe_scene"},
	}
}

func TestFaultContractRoundTripsWhatTheRobotPublishes(t *testing.T) {
	report, err := DecodeFaults(safetyStoppedFaults())
	if err != nil {
		t.Fatal(err)
	}
	if report.Count != 1 || !report.SafetyStopped() {
		t.Fatalf("a latched stop must read as a safety stop: %+v", report)
	}
	if keys := report.Keys(); len(keys) != 1 || keys[0] != "estop:EMERGENCY_STOP_LATCHED" {
		t.Fatalf("unexpected keys %v", keys)
	}
	if got := report.Faults[0].AgeMS; got == nil || *got != 10_000 {
		t.Fatalf("ageMs must survive decoding, got %v", got)
	}
	if got := report.Faults[0].UserInstruction; !strings.Contains(got, "现场复位急停") {
		t.Fatalf("the operator instruction must reach the world model verbatim, got %q", got)
	}
	if !strings.Contains(strings.Join(report.UnavailableCapabilities, ","), "navigation.navigate") {
		t.Fatalf("capabilities the faults took away must be published, got %v", report.UnavailableCapabilities)
	}
}

func TestACleanRobotDecodesAsNoFaults(t *testing.T) {
	report, err := DecodeFaults(cleanFaults())
	if err != nil {
		t.Fatal(err)
	}
	if report.Count != 0 || report.Severity != "info" || report.SafetyStopped() || len(report.Blocking()) != 0 {
		t.Fatalf("a clean robot must decode to no faults: %+v", report)
	}
}

func TestInformationalFaultsAreNewsNotCapabilityLoss(t *testing.T) {
	values := safetyStoppedFaults()
	values["severity"] = "info"
	fault := values["faults"].([]any)[0].(map[string]any)
	fault["severity"], fault["moduleId"], fault["code"] = "info", "arm-left", "ARM_THERMAL_HIGH"
	fault["remedy"] = "self_recover"
	report, err := DecodeFaults(values)
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Blocking()) != 0 {
		t.Fatalf("info must not remove a capability: %+v", report.Blocking())
	}
}

func TestFaultsThatDoNotAddUpAreRefused(t *testing.T) {
	for name, mutate := range map[string]func(map[string]any){
		"count disagrees with the entries": func(v map[string]any) { v["count"] = float64(2) },
		"headline is not the worst fault":  func(v map[string]any) { v["severity"] = "info" },
		"unknown severity": func(v map[string]any) {
			v["severity"] = "catastrophic"
			v["faults"].([]any)[0].(map[string]any)["severity"] = "catastrophic"
		},
		"no remedy class": func(v map[string]any) {
			v["faults"].([]any)[0].(map[string]any)["remedy"] = "pray"
		},
		"no code": func(v map[string]any) { v["faults"].([]any)[0].(map[string]any)["code"] = "" },
		"no detection time": func(v map[string]any) {
			v["faults"].([]any)[0].(map[string]any)["detectedAtUnixMs"] = float64(0)
		},
		"zero occurrences": func(v map[string]any) {
			v["faults"].([]any)[0].(map[string]any)["occurrences"] = float64(0)
		},
		"a null age":       func(v map[string]any) { v["faults"].([]any)[0].(map[string]any)["ageMs"] = nil },
		"an unknown field": func(v map[string]any) { v["rootCause"] = "guessed" },
		"another schema":   func(v map[string]any) { v["schemaVersion"] = "robot.faults.v2" },
	} {
		t.Run(name, func(t *testing.T) {
			values := safetyStoppedFaults()
			mutate(values)
			if _, err := DecodeFaults(values); err == nil {
				t.Fatalf("%s must be refused", name)
			}
		})
	}
}

func TestTheTwoPublishedViewsOfWhatIsGoneMustAgree(t *testing.T) {
	values := safetyStoppedFaults()
	values["capabilityBlockers"] = map[string]any{
		"observe_scene":       []any{"estop:EMERGENCY_STOP_LATCHED"},
		"navigation.navigate": []any{"estop:EMERGENCY_STOP_LATCHED"},
		"manipulation.pick":   []any{"estop:EMERGENCY_STOP_LATCHED"},
		"manipulation.place":  []any{"estop:EMERGENCY_STOP_LATCHED"},
		"plan_grasp":          []any{"estop:EMERGENCY_STOP_LATCHED"},
	}
	values["unavailableCapabilities"] = []any{"manipulation.pick", "manipulation.place",
		"navigation.navigate", "observe_scene", "plan_grasp"}
	report, err := DecodeFaults(values)
	if err != nil {
		t.Fatal(err)
	}
	if len(report.CapabilityBlockers) != 5 {
		t.Fatalf("the blockers are what explains a refusal on the console: %#v", report.CapabilityBlockers)
	}

	// A capability listed as blocked, but absent from the headline list (or the
	// other way round) is a publisher that lost track of what it removed.
	values["unavailableCapabilities"] = []any{"manipulation.pick"}
	if _, err := DecodeFaults(values); err == nil {
		t.Fatal("two views of the removed capabilities disagreeing must be refused")
	}
	values["unavailableCapabilities"] = report.UnavailableCapabilities
	values["capabilityBlockers"] = map[string]any{"observe_scene": []any{"chassis:NO_SUCH_FAULT"}}
	if _, err := DecodeFaults(values); err == nil {
		t.Fatal("a capability blocked by a fault that is not in the report must be refused")
	}
}

func TestTheSameFaultTwiceIsAmbiguousAndRefused(t *testing.T) {
	values := safetyStoppedFaults()
	fault := values["faults"].([]any)[0]
	values["faults"] = []any{fault, fault}
	values["count"] = float64(2)
	if _, err := DecodeFaults(values); err == nil {
		t.Fatal("two entries for one module and code must be refused: which one is current is unknowable")
	}
}
