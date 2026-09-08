// Package robotcontract validates the versioned, vendor-neutral adapter boundary.
// A profile is a declaration, never evidence of hardware commissioning.
package robotcontract

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

type Joint struct {
	Name  string  `json:"name"`
	Kind  string  `json:"kind"`
	Unit  string  `json:"unit"`
	Lower float64 `json:"lower"`
	Upper float64 `json:"upper"`
}
type EndEffector struct {
	ID         string   `json:"id"`
	Kind       string   `json:"kind"`
	JointNames []string `json:"jointNames"`
}
type Sensor struct {
	SourceID          string `json:"sourceId"`
	SourceType        string `json:"sourceType"`
	FrameID           string `json:"frameId"`
	TransformRevision string `json:"transformRevision"`
	MaxAgeMS          int64  `json:"maxAgeMs"`
}
type ActionLimit struct {
	Min  float64 `json:"min"`
	Max  float64 `json:"max"`
	Unit string  `json:"unit"`
}
type Profile struct {
	SchemaVersion  string                 `json:"schemaVersion"`
	RobotID        string                 `json:"robotId"`
	AdapterID      string                 `json:"adapterId"`
	AdapterVersion string                 `json:"adapterVersion"`
	ModelID        string                 `json:"modelId"`
	Embodiment     string                 `json:"embodiment"`
	Joints         []Joint                `json:"joints"`
	EndEffectors   []EndEffector          `json:"endEffectors"`
	Sensors        []Sensor               `json:"sensors"`
	ActionLimits   map[string]ActionLimit `json:"actionLimits"`
	Tools          []string               `json:"tools"`
}
type Entity struct {
	EntityID   string            `json:"entityId"`
	Category   string            `json:"category"`
	Attributes map[string]string `json:"attributes"`
	Pose       []float64         `json:"pose"`
	Confidence float64           `json:"confidence"`
	Relation   string            `json:"relation"`
}
type Reconstruction struct {
	SchemaVersion     string      `json:"schemaVersion"`
	RobotID           string      `json:"robotId"`
	ObservationID     string      `json:"observationId"`
	SourceID          string      `json:"sourceId"`
	SourceType        string      `json:"sourceType"`
	SourceFrameID     string      `json:"sourceFrameId"`
	FrameID           string      `json:"frameId"`
	TransformRevision string      `json:"transformRevision"`
	ObservedAtUnixMS  int64       `json:"observedAtUnixMs"`
	Sequence          uint64      `json:"sequence"`
	Units             string      `json:"units"`
	Entities          []Entity    `json:"entities"`
	Points            [][]float64 `json:"points"`
	PointColors       [][]int     `json:"pointColors,omitempty"`
}

// Preserve the distinction between omitted colors and explicit JSON null even
// when a caller decodes a historical snapshot directly rather than using the
// protobuf map boundary. encoding/json otherwise turns null channels into 0.
func (r *Reconstruction) UnmarshalJSON(data []byte) error {
	type plain Reconstruction
	var decoded plain
	value := struct {
		*plain
		PointColors json.RawMessage `json:"pointColors"`
	}{plain: &decoded}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&value); err != nil {
		return err
	}
	if value.PointColors != nil {
		var colors any
		if err := json.Unmarshal(value.PointColors, &colors); err != nil {
			return err
		}
		if err := rejectNulls(colors); err != nil {
			return fmt.Errorf("invalid pointColors: %w", err)
		}
		if err := json.Unmarshal(value.PointColors, &decoded.PointColors); err != nil {
			return err
		}
	}
	*r = Reconstruction(decoded)
	return nil
}

var canonicalTools = map[string]bool{
	"observe_scene": true, "resolve_targets": true, "plan_grasp": true,
	"manipulation.pick": true, "manipulation.place": true, "verify_grasp": true,
	"verify_placement": true, "recover_to_safe_pose": true, "emergency_stop": true,
	"navigation.navigate": true, "arm.move": true,
}

func PhysicalTool(name string) bool {
	return name == "manipulation.pick" || name == "manipulation.place" || name == "recover_to_safe_pose" || name == "arm.move" || name == "navigation.navigate"
}
func validText(s string) bool {
	return strings.IndexFunc(s, unicode.IsSpace) == -1 && s != "" && utf8.RuneCountInString(s) <= 256
}
func finite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
func oneOf(v string, options ...string) bool {
	for _, s := range options {
		if v == s {
			return true
		}
	}
	return false
}
func validSource(v string) bool {
	return oneOf(v, "sim_ground_truth", "robot_proprioception", "rgbd_camera", "lidar", "slam_pose", "tool_result_evidence", "operator_annotation", "sensor_fusion", "stereo_camera", "monocular_camera")
}

func (p Profile) Validate() error {
	if p.SchemaVersion != "robot.profile.v1" || !validText(p.RobotID) || !validText(p.AdapterID) || !validText(p.AdapterVersion) || !validText(p.ModelID) || !oneOf(p.Embodiment, "arm", "dual_arm", "mobile_manipulator", "mobile_base", "sensor_rig", "custom") {
		return errors.New("invalid robot profile identity or embodiment")
	}
	if len(p.Sensors) == 0 || len(p.Sensors) > 64 || len(p.Tools) == 0 || len(p.Tools) > 11 || len(p.Joints) > 128 || len(p.EndEffectors) > 32 || len(p.ActionLimits) > 256 {
		return errors.New("invalid profile collection size")
	}
	joints := map[string]bool{}
	for _, j := range p.Joints {
		if !validText(j.Name) || joints[j.Name] || !oneOf(j.Kind, "revolute", "prismatic", "continuous", "fixed") || !oneOf(j.Unit, "rad", "m") || !finite(j.Lower) || !finite(j.Upper) || j.Lower > j.Upper || (j.Kind == "prismatic" && j.Unit != "m") || (oneOf(j.Kind, "revolute", "continuous") && j.Unit != "rad") {
			return errors.New("invalid or duplicate joint")
		}
		joints[j.Name] = true
	}
	effectors := map[string]bool{}
	for _, e := range p.EndEffectors {
		if !validText(e.ID) || effectors[e.ID] || !oneOf(e.Kind, "gripper", "suction", "tool", "sensor", "custom") {
			return errors.New("invalid end effector")
		}
		effectors[e.ID] = true
		seen := map[string]bool{}
		for _, j := range e.JointNames {
			if !joints[j] || seen[j] {
				return errors.New("unknown or duplicate effector joint")
			}
			seen[j] = true
		}
	}
	sensors := map[string]bool{}
	for _, s := range p.Sensors {
		if !validText(s.SourceID) || sensors[s.SourceID] || !validSource(s.SourceType) || !validText(s.FrameID) || !validText(s.TransformRevision) || s.MaxAgeMS <= 0 || s.MaxAgeMS > 60000 {
			return errors.New("invalid or duplicate sensor")
		}
		sensors[s.SourceID] = true
	}
	tools := map[string]bool{}
	for _, tool := range p.Tools {
		if !canonicalTools[tool] || tools[tool] {
			return fmt.Errorf("unknown or duplicate canonical tool %q", tool)
		}
		tools[tool] = true
	}
	if !tools["observe_scene"] || !tools["emergency_stop"] {
		return errors.New("profile requires observe_scene and emergency_stop")
	}
	if tools["navigation.navigate"] {
		for _, key := range []string{"navigation.x", "navigation.y", "navigation.z"} {
			bound, exists := p.ActionLimits[key]
			if !exists || bound.Unit != "m" {
				return errors.New("navigation requires explicit world workspace bounds in metres")
			}
		}
	}
	for _, name := range p.Tools {
		if PhysicalTool(name) && name != "navigation.navigate" && len(p.ActionLimits) == 0 {
			return errors.New("action tools require explicit actuator limits")
		}
	}
	for key, b := range p.ActionLimits {
		if !validText(key) || !finite(b.Min) || !finite(b.Max) || b.Min > b.Max || !oneOf(b.Unit, "rad", "m", "rad/s", "m/s", "normalized", "N") {
			return errors.New("invalid action limit")
		}
	}
	return nil
}
func (p Profile) Sensor(source string) (Sensor, bool) {
	for _, s := range p.Sensors {
		if s.SourceID == source {
			return s, true
		}
	}
	return Sensor{}, false
}
func ValidPose(pose []float64) bool {
	if len(pose) != 7 {
		return false
	}
	sum := 0.0
	for i, v := range pose {
		if !finite(v) {
			return false
		}
		if i >= 3 {
			sum += v * v
		}
	}
	return math.Abs(sum-1) <= 0.001
}
func (r Reconstruction) Validate(p Profile, now time.Time) error {
	if err := p.Validate(); err != nil {
		return err
	}
	if r.SchemaVersion != "scene.reconstruction.v1" || r.RobotID != p.RobotID || !validText(r.ObservationID) || r.FrameID != "world" || r.Units != "m" || r.Sequence == 0 || r.Sequence > 9007199254740991 || r.ObservedAtUnixMS <= 0 {
		return errors.New("invalid reconstruction identity, coordinates or sequence")
	}
	s, ok := p.Sensor(r.SourceID)
	if !ok || s.SourceType != r.SourceType || s.FrameID != r.SourceFrameID || s.TransformRevision != r.TransformRevision {
		return errors.New("reconstruction source or transform mismatch")
	}
	age := now.UnixMilli() - r.ObservedAtUnixMS
	if age > s.MaxAgeMS || age < -250 {
		return errors.New("reconstruction is stale or future dated")
	}
	if len(r.Entities) > 2048 || len(r.Points) > 4096 {
		return errors.New("reconstruction exceeds bounded payload limits")
	}
	if len(r.PointColors) > 0 && len(r.PointColors) != len(r.Points) {
		return errors.New("pointColors must contain one RGB triple for every point")
	}
	for _, color := range r.PointColors {
		if len(color) != 3 {
			return errors.New("invalid reconstruction point color")
		}
		for _, channel := range color {
			if channel < 0 || channel > 255 {
				return errors.New("invalid reconstruction point color channel")
			}
		}
	}
	seen := map[string]bool{}
	for _, e := range r.Entities {
		if !validText(e.EntityID) || !validText(e.Category) || seen[e.EntityID] || !ValidPose(e.Pose) || !finite(e.Confidence) || e.Confidence < 0 || e.Confidence > 1 || len(e.Attributes) > 64 || utf8.RuneCountInString(e.Relation) > 512 {
			return errors.New("invalid or duplicate reconstruction entity")
		}
		seen[e.EntityID] = true
		for k, v := range e.Attributes {
			if !validText(k) || utf8.RuneCountInString(v) > 1024 {
				return errors.New("invalid entity attribute")
			}
		}
	}
	for _, point := range r.Points {
		if len(point) != 3 || !finite(point[0]) || !finite(point[1]) || !finite(point[2]) {
			return errors.New("invalid reconstruction point")
		}
	}
	return nil
}
func decode(values map[string]any, into any) error {
	if err := rejectNulls(values); err != nil {
		return err
	}
	wire, err := json.Marshal(values)
	if err != nil {
		return err
	}
	if len(wire) > 2*1024*1024 {
		return errors.New("robot contract exceeds 2 MiB")
	}
	decoder := json.NewDecoder(bytes.NewReader(wire))
	decoder.DisallowUnknownFields()
	return decoder.Decode(into)
}

// JSON null must not silently decode to numeric zero (for example quaternion
// components or action limits). Neither versioned schema allows null values.
func rejectNulls(value any) error {
	switch item := value.(type) {
	case nil:
		return errors.New("robot contract cannot contain null")
	case map[string]any:
		for _, child := range item {
			if err := rejectNulls(child); err != nil {
				return err
			}
		}
	case []any:
		for _, child := range item {
			if err := rejectNulls(child); err != nil {
				return err
			}
		}
	}
	return nil
}

func required(values map[string]any, fields ...string) error {
	for _, field := range fields {
		if value, ok := values[field]; !ok || value == nil {
			return fmt.Errorf("required contract field %q is missing or null", field)
		}
	}
	return nil
}

func requiredItems(values map[string]any, key string, fields ...string) error {
	if raw, exists := values[key]; exists {
		items, ok := raw.([]any)
		if !ok {
			return fmt.Errorf("contract %q must be an array", key)
		}
		for _, item := range items {
			object, ok := item.(map[string]any)
			if !ok {
				return fmt.Errorf("contract %q item must be an object", key)
			}
			if err := required(object, fields...); err != nil {
				return err
			}
		}
	}
	return nil
}

func DecodeProfile(values map[string]any) (*Profile, error) {
	if err := required(values, "schemaVersion", "robotId", "adapterId", "adapterVersion", "modelId", "embodiment", "joints", "endEffectors", "sensors", "actionLimits", "tools"); err != nil {
		return nil, err
	}
	for key, fields := range map[string][]string{
		"joints":       {"name", "kind", "unit", "lower", "upper"},
		"endEffectors": {"id", "kind"},
		"sensors":      {"sourceId", "sourceType", "frameId", "transformRevision", "maxAgeMs"},
	} {
		if err := requiredItems(values, key, fields...); err != nil {
			return nil, err
		}
	}
	limits, ok := values["actionLimits"].(map[string]any)
	if !ok {
		return nil, errors.New("actionLimits must be an object")
	}
	for _, value := range limits {
		bound, ok := value.(map[string]any)
		if !ok {
			return nil, errors.New("action bound must be an object")
		}
		if err := required(bound, "min", "max", "unit"); err != nil {
			return nil, err
		}
	}
	var p Profile
	if err := decode(values, &p); err != nil {
		return nil, err
	}
	if err := p.Validate(); err != nil {
		return nil, err
	}
	return &p, nil
}
func DecodeReconstruction(values map[string]any) (*Reconstruction, error) {
	if err := required(values, "schemaVersion", "robotId", "observationId", "sourceId", "sourceType", "sourceFrameId", "frameId", "transformRevision", "observedAtUnixMs", "sequence", "units"); err != nil {
		return nil, err
	}
	if err := requiredItems(values, "entities", "entityId", "category", "pose", "confidence"); err != nil {
		return nil, err
	}
	var r Reconstruction
	if err := decode(values, &r); err != nil {
		return nil, err
	}
	return &r, nil
}
