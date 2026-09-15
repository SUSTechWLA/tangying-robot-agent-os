package robotclient

import (
	"context"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

const (
	maxSemanticGoals    = 256
	maxSemanticAliases  = 512
	maxSemanticObjects  = 2048
	maxObjectAttributes = 32
)

func (c *Client) routeObservation(ctx context.Context, info runtime.Snapshot) (map[string]any, error) {
	stream, err := c.robot.Observe(ctx, &robotv1.ObserveRequest{Streams: []string{"robot_state", "reconstruction"}, MaxRateHz: 1})
	if err != nil {
		return nil, err
	}
	observation, err := stream.Recv()
	if err != nil {
		return nil, err
	}
	age := time.Now().UnixMilli() - observation.WallTimeUnixMs
	if observation.ObservationId == "" || age < -250 || age > 2000 {
		return nil, errors.New("semantic navigation needs a fresh robot observation")
	}
	if _, err := c.acceptReconstruction(info, observation); err != nil {
		return nil, err
	}
	if observation.RobotState == nil {
		return nil, errors.New("semantic navigation observation has no robot state")
	}
	return observation.RobotState.AsMap(), nil
}

func semanticNavigation(info runtime.Snapshot, state map[string]any) (map[string]any, error) {
	semantic, ok := state["semantic_navigation"].(map[string]any)
	if !ok || len(semantic) == 0 {
		return nil, errors.New("semantic navigation contract is missing")
	}
	if semantic["schemaVersion"] != "semantic.navigation.v1" {
		return nil, errors.New("semantic navigation schema is unsupported")
	}
	if semantic["frameId"] != "world" {
		return nil, errors.New("semantic navigation frame must be world")
	}
	if robotID, ok := semantic["robotId"].(string); !ok || robotID == "" || robotID != info.RobotID {
		return nil, errors.New("semantic navigation robot does not match runtime")
	}
	active, ok := state["active_map"].(map[string]any)
	if !ok || len(active) == 0 {
		return nil, errors.New("semantic navigation active map contract is missing")
	}
	for _, field := range []string{"mapId", "mapRevision", "calibrationRevision"} {
		value, ok := semantic[field].(string)
		activeValue, activeOK := active[field].(string)
		if !ok || !activeOK || strings.TrimSpace(value) == "" || value != activeValue {
			return nil, fmt.Errorf("semantic navigation %s does not match active map", field)
		}
	}
	goals, ok := semantic["goals"].(map[string]any)
	if !ok || len(goals) == 0 || len(goals) > maxSemanticGoals {
		return nil, errors.New("semantic navigation goals are missing or exceed limits")
	}
	aliases, ok := semantic["aliases"].(map[string]any)
	if !ok || len(aliases) > maxSemanticAliases {
		return nil, errors.New("semantic navigation aliases are invalid or exceed limits")
	}
	return semantic, nil
}

func semanticRoute(info runtime.Snapshot, state map[string]any, requested []string) ([]string, [][]float64, error) {
	semantic, err := semanticNavigation(info, state)
	if err != nil {
		return nil, nil, err
	}
	if len(requested) == 0 || len(requested) > maxSemanticGoals {
		return nil, nil, errors.New("semantic route is empty or exceeds limits")
	}
	if info.RobotProfile == nil {
		return nil, nil, errors.New("semantic navigation requires robot workspace limits")
	}
	locations := semantic["goals"].(map[string]any)
	aliases := semantic["aliases"].(map[string]any)
	names := make([]string, 0, len(requested))
	goals := make([][]float64, 0, len(requested))
	for _, requestedName := range requested {
		name := strings.TrimSpace(requestedName)
		if name == "" || len(name) > 256 {
			return nil, nil, errors.New("semantic location name is invalid")
		}
		if alias, exists := aliases[name]; exists {
			canonical, ok := alias.(string)
			if !ok || strings.TrimSpace(canonical) == "" {
				return nil, nil, fmt.Errorf("semantic alias %q is invalid", name)
			}
			name = canonical
		}
		pose, err := semanticPose(locations[name])
		if err != nil {
			return nil, nil, fmt.Errorf("semantic location %q is not commissioned: %w", name, err)
		}
		if err := withinWorkspace(info, pose); err != nil {
			return nil, nil, fmt.Errorf("semantic goal %q: %w", name, err)
		}
		names = append(names, name)
		goals = append(goals, pose)
	}
	return names, goals, nil
}

func semanticRouteGoals(info runtime.Snapshot, state map[string]any, rooms []string) ([][]float64, error) {
	_, goals, err := semanticRoute(info, state, rooms)
	return goals, err
}

func routeContains(route []string, wanted string) bool {
	for _, location := range route {
		if location == wanted {
			return true
		}
	}
	return false
}

// Keep the measured return pose exactly, coalescing only numerical round-off
// when the user already ended the route at that same position and heading.
func withObservedReturn(rooms []string, goals [][]float64, start []float64) ([]string, [][]float64) {
	if len(goals) > 0 && len(goals[len(goals)-1]) == 7 && len(start) == 7 {
		last := goals[len(goals)-1]
		same := true
		for i := 0; i < 3; i++ {
			same = same && math.Abs(last[i]-start[i]) <= 1e-6
		}
		direct, opposite := 0.0, 0.0
		for i := 3; i < 7; i++ {
			direct += (last[i] - start[i]) * (last[i] - start[i])
			opposite += (last[i] + start[i]) * (last[i] + start[i])
		}
		if same && math.Min(direct, opposite) <= 1e-12 {
			goals[len(goals)-1] = append([]float64(nil), start...)
			return rooms, goals
		}
	}
	return append(rooms, "return_to_start"), append(goals, append([]float64(nil), start...))
}

// withinWorkspace refuses a pose outside the robot's declared navigation limits.
// It is shared by commissioned goals and remembered positions: a recalled pose
// is not exempt from the profile just because it was measured.
func withinWorkspace(info runtime.Snapshot, pose []float64) error {
	if info.RobotProfile == nil {
		return errors.New("navigation requires robot workspace limits")
	}
	for index, key := range []string{"navigation.x", "navigation.y", "navigation.z"} {
		limit, exists := info.RobotProfile.ActionLimits[key]
		if !exists || pose[index] < limit.Min || pose[index] > limit.Max {
			return fmt.Errorf("goal exceeds robot workspace on %s", key)
		}
	}
	return nil
}

func semanticPose(raw any) ([]float64, error) {
	values, ok := raw.([]any)
	if !ok || len(values) != 7 {
		return nil, errors.New("goal pose must contain seven numbers")
	}
	pose := make([]float64, len(values))
	for index, rawValue := range values {
		value, ok := rawValue.(float64)
		if !ok {
			return nil, errors.New("goal pose contains a non-number")
		}
		pose[index] = value
	}
	if !robotcontract.ValidPose(pose) {
		return nil, errors.New("goal pose is invalid")
	}
	return pose, nil
}

func semanticObjectRef(state map[string]any, selector manipulation.EntitySelector) (manipulation.SceneRef, error) {
	if strings.TrimSpace(selector.Category) == "" || len(selector.Attributes) > maxObjectAttributes {
		return manipulation.SceneRef{}, errors.New("semantic object selector is invalid")
	}
	for key, value := range selector.Attributes {
		if strings.TrimSpace(key) == "" || strings.TrimSpace(value) == "" {
			return manipulation.SceneRef{}, errors.New("semantic object selector has an empty attribute")
		}
	}
	objects, ok := state["semantic_objects"].([]any)
	if !ok || len(objects) == 0 {
		return manipulation.SceneRef{}, errors.New("semantic object contract is missing")
	}
	if len(objects) > maxSemanticObjects {
		return manipulation.SceneRef{}, errors.New("semantic object contract exceeds limits")
	}
	matches := make([]manipulation.SceneRef, 0, 1)
	ids := make(map[string]bool, len(objects))
	for _, raw := range objects {
		object, ok := raw.(map[string]any)
		if !ok {
			return manipulation.SceneRef{}, errors.New("semantic object contract contains an invalid entry")
		}
		id, idOK := object["id"].(string)
		category, categoryOK := object["category"].(string)
		workArea, workAreaOK := object["workArea"].(string)
		confidence, confidenceOK := object["confidence"].(float64)
		attributes, attributesOK := object["attributes"].(map[string]any)
		if !idOK || !categoryOK || !workAreaOK || !confidenceOK || !attributesOK ||
			strings.TrimSpace(id) == "" || strings.TrimSpace(category) == "" || strings.TrimSpace(workArea) == "" ||
			math.IsNaN(confidence) || math.IsInf(confidence, 0) || confidence < 0 || confidence > 1 ||
			len(attributes) > maxObjectAttributes || ids[id] {
			return manipulation.SceneRef{}, errors.New("semantic object contract contains an invalid entry")
		}
		ids[id] = true
		validAttributes := true
		for key, rawValue := range attributes {
			value, ok := rawValue.(string)
			if !ok || strings.TrimSpace(key) == "" || strings.TrimSpace(value) == "" {
				return manipulation.SceneRef{}, errors.New("semantic object contract contains invalid attributes")
			}
			if requested, exists := selector.Attributes[key]; exists && requested != value {
				validAttributes = false
			}
		}
		for key, requested := range selector.Attributes {
			value, exists := attributes[key].(string)
			if !exists || value != requested {
				validAttributes = false
			}
		}
		relation := ""
		if rawRelation, exists := object["relation"]; exists {
			var relationOK bool
			relation, relationOK = rawRelation.(string)
			if !relationOK {
				return manipulation.SceneRef{}, errors.New("semantic object contract contains an invalid relation")
			}
		}
		if category == selector.Category && validAttributes &&
			(selector.Relation == "" || relation == selector.Relation) {
			matches = append(matches, manipulation.SceneRef{ID: id, Confidence: confidence, WorkArea: workArea})
		}
	}
	if len(matches) == 0 {
		return manipulation.SceneRef{}, errors.New("semantic object grounding absent: selector has no registered match")
	}
	if len(matches) != 1 {
		return manipulation.SceneRef{}, fmt.Errorf("semantic object grounding ambiguous: selector has %d registered matches", len(matches))
	}
	return matches[0], nil
}

func routeStartPose(state map[string]any) ([]float64, error) {
	if state["base_pose_frame"] != "world" {
		return nil, errors.New("return to start requires a fresh world-frame base pose")
	}
	pose, err := semanticPose(state["base_pose"])
	if err != nil {
		return nil, errors.New("return to start requires a valid observed pose")
	}
	return pose, nil
}
