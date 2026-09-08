package robotcontract

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
	"time"
)

func testProfile() Profile {
	return Profile{SchemaVersion: "robot.profile.v1", RobotID: "arm-1", AdapterID: "generic_arm", AdapterVersion: "1", ModelID: "arm6", Embodiment: "arm", Joints: []Joint{{Name: "elbow", Kind: "revolute", Unit: "rad", Lower: -2, Upper: 2}}, EndEffectors: []EndEffector{{ID: "hand", Kind: "gripper", JointNames: []string{"elbow"}}}, Sensors: []Sensor{{SourceID: "depth", SourceType: "rgbd_camera", FrameID: "camera", TransformRevision: "cal-1", MaxAgeMS: 1000}}, ActionLimits: map[string]ActionLimit{"elbow.pos": {Min: -2, Max: 2, Unit: "rad"}}, Tools: []string{"observe_scene", "arm.move", "emergency_stop"}}
}
func testScene(now time.Time) Reconstruction {
	return Reconstruction{SchemaVersion: "scene.reconstruction.v1", RobotID: "arm-1", ObservationID: "capture-1", SourceID: "depth", SourceType: "rgbd_camera", SourceFrameID: "camera", FrameID: "world", TransformRevision: "cal-1", ObservedAtUnixMS: now.UnixMilli(), Sequence: 1, Units: "m", Entities: []Entity{{EntityID: "block", Category: "block", Pose: []float64{0, 0, 0, 1, 0, 0, 0}, Confidence: 0.9}}, Points: [][]float64{{0, 1, 2}}}
}
func TestStrictContractAcceptsDifferentEmbodiments(t *testing.T) {
	for _, kind := range []string{"arm", "dual_arm", "mobile_manipulator", "mobile_base", "sensor_rig", "custom"} {
		p := testProfile()
		p.Embodiment = kind
		if err := p.Validate(); err != nil {
			t.Fatal(err)
		}
		r := testScene(time.Now())
		if err := r.Validate(p, time.Now()); err != nil {
			t.Fatal(err)
		}
	}
}
func TestProfileRejectsInvalidDeclarations(t *testing.T) {
	cases := map[string]func(*Profile){"unknown schema": func(p *Profile) { p.SchemaVersion = "robot.profile.v9" }, "duplicate sensors": func(p *Profile) { p.Sensors = append(p.Sensors, p.Sensors[0]) }, "unknown tool": func(p *Profile) { p.Tools = append(p.Tools, "unsafe.whatever") }, "invalid joint": func(p *Profile) { p.Joints[0].Unit = "degrees" }, "invalid effector": func(p *Profile) { p.EndEffectors[0].JointNames = []string{"missing"} }, "infinite limit": func(p *Profile) { p.ActionLimits["elbow.pos"] = ActionLimit{Min: 0, Max: math.Inf(1), Unit: "rad"} }}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			p := testProfile()
			mutate(&p)
			if p.Validate() == nil {
				t.Fatal("invalid profile accepted")
			}
		})
	}
}
func TestReconstructionRejectsUntrustedSensorData(t *testing.T) {
	now := time.Now()
	cases := map[string]func(*Reconstruction){"stale": func(r *Reconstruction) { r.ObservedAtUnixMS = now.Add(-2 * time.Second).UnixMilli() }, "future": func(r *Reconstruction) { r.ObservedAtUnixMS = now.Add(time.Second).UnixMilli() }, "unknown source": func(r *Reconstruction) { r.SourceID = "other" }, "spoofed type": func(r *Reconstruction) { r.SourceType = "sim_ground_truth" }, "wrong transform": func(r *Reconstruction) { r.TransformRevision = "other" }, "camera coords": func(r *Reconstruction) { r.FrameID = "camera" }, "millimeters": func(r *Reconstruction) { r.Units = "mm" }, "bad quaternion": func(r *Reconstruction) { r.Entities[0].Pose[3] = 2 }, "nan": func(r *Reconstruction) { r.Points[0][0] = math.NaN() }, "duplicate id": func(r *Reconstruction) { r.Entities = append(r.Entities, r.Entities[0]) }, "short pose": func(r *Reconstruction) { r.Entities[0].Pose = []float64{0, 0, 0} }, "sequence zero": func(r *Reconstruction) { r.Sequence = 0 }}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			r := testScene(now)
			mutate(&r)
			if r.Validate(testProfile(), now) == nil {
				t.Fatal("invalid reconstruction accepted")
			}
		})
	}
}
func TestDecodeRejectsUnknownFieldsAndFractionalSequence(t *testing.T) {
	p := testProfile()
	data, _ := json.Marshal(p)
	var values map[string]any
	_ = json.Unmarshal(data, &values)
	values["trustMe"] = true
	if _, err := DecodeProfile(values); err == nil {
		t.Fatal("unknown profile field")
	}
	r := testScene(time.Now())
	data, _ = json.Marshal(r)
	values = nil
	_ = json.Unmarshal(data, &values)
	values["entities"].([]any)[0].(map[string]any)["attributes"] = map[string]any{}
	if _, err := DecodeReconstruction(values); err != nil {
		t.Fatalf("valid baseline: %v", err)
	}
	values["sequence"] = 1.2
	if _, err := DecodeReconstruction(values); err == nil {
		t.Fatal("fractional sequence")
	}
}

func TestDecodeRejectsNullQuaternionComponents(t *testing.T) {
	data, _ := json.Marshal(testScene(time.Now()))
	var values map[string]any
	_ = json.Unmarshal(data, &values)
	entity := values["entities"].([]any)[0].(map[string]any)
	entity["attributes"] = map[string]any{}
	entity["pose"].([]any)[4] = nil
	if _, err := DecodeReconstruction(values); err == nil {
		t.Fatal("null quaternion silently converted to zero")
	}
}

func TestReconstructionTextLimitsCountUnicodeCharacters(t *testing.T) {
	now := time.Now()
	r := testScene(now)
	r.Entities[0].Relation = strings.Repeat("区", 512)
	r.Entities[0].Attributes = map[string]string{"名称": strings.Repeat("块", 1024)}
	if err := r.Validate(testProfile(), now); err != nil {
		t.Fatalf("valid Unicode lengths disagree with Python contract: %v", err)
	}
	r.Entities[0].Relation += "域"
	if r.Validate(testProfile(), now) == nil {
		t.Fatal("overlong Unicode relation accepted")
	}
}

func TestReconstructionPointColorsDecodeAsBoundedRGBTriples(t *testing.T) {
	now := time.Now()
	for _, colors := range []any{[]any{}, []any{[]any{255, 0, 128}}} {
		values := pointColorScene(t, now)
		values["pointColors"] = colors
		scene, err := DecodeReconstruction(values)
		if err != nil {
			t.Fatalf("valid point colors rejected: %v", err)
		}
		if err := scene.Validate(testProfile(), now); err != nil {
			t.Fatal(err)
		}
		encoded, err := json.Marshal(scene)
		if err != nil {
			t.Fatal(err)
		}
		if len(colors.([]any)) > 0 && !strings.Contains(string(encoded), `"pointColors":[[255,0,128]]`) {
			t.Fatalf("RGB colors were lost or base64 encoded: %s", encoded)
		}
	}
}

func pointColorScene(t *testing.T, now time.Time) map[string]any {
	t.Helper()
	scene := testScene(now)
	scene.Entities[0].Attributes = map[string]string{}
	data, err := json.Marshal(scene)
	if err != nil {
		t.Fatal(err)
	}
	var values map[string]any
	if err := json.Unmarshal(data, &values); err != nil {
		t.Fatal(err)
	}
	return values
}

func TestReconstructionRejectsMalformedOrMisalignedPointColors(t *testing.T) {
	now := time.Now()
	for name, colors := range map[string]any{
		"null": nil, "missing channel": []any{[]any{0, 0}},
		"extra channel": []any{[]any{0, 0, 0, 0}},
		"negative":      []any{[]any{-1, 0, 0}}, "overflow": []any{[]any{256, 0, 0}},
		"boolean": []any{[]any{true, 0, 0}}, "fraction": []any{[]any{1.5, 0, 0}},
		"string": []any{[]any{"1", 0, 0}}, "null channel": []any{[]any{nil, 0, 0}},
		"misaligned": []any{[]any{0, 0, 0}, []any{0, 0, 0}},
	} {
		t.Run(name, func(t *testing.T) {
			values := pointColorScene(t, now)
			values["pointColors"] = colors
			scene, err := DecodeReconstruction(values)
			if err == nil && scene.Validate(testProfile(), now) == nil {
				t.Fatal("invalid point colors accepted")
			}
		})
	}
}

func TestDirectJSONReconstructionCannotTurnNullPointColorsIntoMissingOrBlack(t *testing.T) {
	now := time.Now()
	for _, rawColors := range []string{"null", "[null]", "[[null,0,0]]", "[[0,null,0]]", "[[0,0,null]]"} {
		t.Run(rawColors, func(t *testing.T) {
			values := pointColorScene(t, now)
			values["pointColors"] = json.RawMessage(rawColors)
			wire, err := json.Marshal(values)
			if err != nil {
				t.Fatal(err)
			}
			var scene Reconstruction
			if err := json.Unmarshal(wire, &scene); err == nil {
				t.Fatalf("JSON null was silently normalized: colors=%s, decoded=%v", rawColors, scene.PointColors)
			}
		})
	}
}

func TestDirectJSONPointColorsAcceptsMissingEmptyAndRGBWithoutWeakeningUnknownFieldChecks(t *testing.T) {
	now := time.Now()
	for _, rawColors := range []string{"", "[]", "[[255,0,128]]"} {
		values := pointColorScene(t, now)
		if rawColors != "" {
			values["pointColors"] = json.RawMessage(rawColors)
		}
		wire, err := json.Marshal(values)
		if err != nil {
			t.Fatal(err)
		}
		var scene Reconstruction
		if err := json.Unmarshal(wire, &scene); err != nil {
			t.Fatal(err)
		}
		if err := scene.Validate(testProfile(), now); err != nil {
			t.Fatal(err)
		}
		if rawColors == "[[255,0,128]]" && (len(scene.PointColors) != 1 || scene.PointColors[0][0] != 255 || scene.PointColors[0][2] != 128) {
			t.Fatal("direct JSON parsing lost point colors")
		}
	}
	values := pointColorScene(t, now)
	values["pointColors"], values["unknownField"] = []any{[]any{255, 0, 128}}, true
	if _, err := DecodeReconstruction(values); err == nil {
		t.Fatal("custom point color parsing weakened unknown-field rejection")
	}
}
