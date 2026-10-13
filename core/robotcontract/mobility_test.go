package robotcontract

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func mobilityExample(t *testing.T, name string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "examples", "robots", name+".commissioning.profile.json"))
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func TestSharedMobilityExamplesDeclareGroundAirAndLeggedControllers(t *testing.T) {
	for _, name := range []string{"ugv", "uav", "legged"} {
		t.Run(name, func(t *testing.T) {
			value, err := DecodeProfile(mobilityExample(t, name))
			if err != nil {
				t.Fatal(err)
			}
			if value.Mobility == nil || !value.Mobility.SafeStop.ControllerOwned {
				t.Fatal("explicit local controller contract lost")
			}
			raw, err := json.Marshal(value)
			if err != nil {
				t.Fatal(err)
			}
			var roundtrip map[string]any
			if err := json.Unmarshal(raw, &roundtrip); err != nil {
				t.Fatal(err)
			}
			if _, err := DecodeProfile(roundtrip); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestMobilityRejectsUnsafeOrIncompatibleDeclarations(t *testing.T) {
	cases := []struct {
		name    string
		example string
		mutate  func(map[string]any, map[string]any, map[string]any)
	}{
		{"air omitted", "uav", func(p, m, s map[string]any) { delete(p, "mobility") }},
		{"legged omitted", "legged", func(p, m, s map[string]any) { delete(p, "mobility") }},
		{"null", "ugv", func(p, m, s map[string]any) { p["mobility"] = nil }},
		{"wrong frame", "ugv", func(p, m, s map[string]any) { m["commandFrame"] = "ned" }},
		{"missing preparation", "ugv", func(p, m, s map[string]any) { delete(m, "navigationPreparation") }},
		{"undeclared preparation", "ugv", func(p, m, s map[string]any) { m["navigationPreparation"] = "agent_pre_position" }},
		{"air preparation", "uav", func(p, m, s map[string]any) { m["navigationPreparation"] = "agent_pre_position" }},
		{"down axis", "ugv", func(p, m, s map[string]any) { m["worldConvention"] = "ned" }},
		{"zero speed", "ugv", func(p, m, s map[string]any) { m["maxLinearSpeedMps"] = 0 }},
		{"negative speed", "ugv", func(p, m, s map[string]any) { m["maxAngularSpeedRadps"] = -1 }},
		{"cloud controller", "ugv", func(p, m, s map[string]any) { s["controllerOwned"] = false }},
		{"boolean loss timeout", "ugv", func(p, m, s map[string]any) { s["commandLossTimeoutMs"] = true }},
		{"missing loss timeout", "ugv", func(p, m, s map[string]any) { delete(s, "commandLossTimeoutMs") }},
		{"oversized loss timeout", "ugv", func(p, m, s map[string]any) { s["commandLossTimeoutMs"] = 60001 }},
		{"air motor cut", "uav", func(p, m, s map[string]any) { s["strategy"] = "motor_cut" }},
		{"air brake", "uav", func(p, m, s map[string]any) { s["strategy"] = "brake" }},
		{"air hold without contingency", "uav", func(p, m, s map[string]any) { s["strategy"] = "hold_position" }},
		{"air planar", "uav", func(p, m, s map[string]any) { m["poseModel"] = "se2" }},
		{"air preposition", "uav", func(p, m, s map[string]any) { p["tools"] = append(p["tools"].([]any), "navigation.pre_position") }},
		{"legged brake", "legged", func(p, m, s map[string]any) { s["strategy"] = "brake" }},
		{"legged different embodiment", "legged", func(p, m, s map[string]any) { p["embodiment"] = "mobile_base" }},
		{"ground landing", "ugv", func(p, m, s map[string]any) { s["strategy"] = "controlled_land" }},
		{"wheeled se3", "ugv", func(p, m, s map[string]any) { m["poseModel"] = "se3" }},
		{"unknown field", "ugv", func(p, m, s map[string]any) { m["allowUnboundedMotion"] = true }},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			value := mobilityExample(t, test.example)
			mobility := value["mobility"].(map[string]any)
			stop := mobility["safeStop"].(map[string]any)
			test.mutate(value, mobility, stop)
			if _, err := DecodeProfile(value); err == nil {
				t.Fatal("invalid mobility accepted")
			}
		})
	}
}
