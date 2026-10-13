package capabilityagent

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func compositeInputSchema() map[string]any {
	text := map[string]any{"type": "string", "minLength": 1, "maxLength": 128}
	revision := map[string]any{"type": "string", "minLength": 64, "maxLength": 64}
	target := map[string]any{"type": "object", "additionalProperties": false,
		"properties": map[string]any{"annotationId": text, "name": text,
			"maxDistanceM":     map[string]any{"type": "number", "minimum": .5, "maximum": 5},
			"allowStaleSearch": map[string]any{"type": "boolean"},
			"verificationMode": map[string]any{"type": "string", "enum": []any{"arrival", "object"}}},
		"required": []any{"annotationId", "name", "maxDistanceM", "allowStaleSearch", "verificationMode"}}
	route := map[string]any{"type": "object", "additionalProperties": false,
		"properties": map[string]any{"mapId": text, "mapRevision": revision, "calibrationRevision": revision,
			"cloudSha256": revision, "semanticRevision": revision,
			"targets": map[string]any{"type": "array", "minItems": 1, "maxItems": 16, "items": target}},
		"required": []any{"mapId", "mapRevision", "calibrationRevision", "cloudSha256", "semanticRevision", "targets"}}
	return map[string]any{"type": "object", "oneOf": []any{
		map[string]any{"type": "object", "properties": map[string]any{"request": map[string]any{"type": "string", "minLength": 1, "maxLength": 2000}}, "required": []any{"request"}, "additionalProperties": false},
		map[string]any{"type": "object", "properties": map[string]any{"semanticRoute": route}, "required": []any{"semanticRoute"}, "additionalProperties": false},
	}}
}

type proposedSemanticRoute struct {
	MapID               string `json:"mapId"`
	MapRevision         string `json:"mapRevision"`
	CalibrationRevision string `json:"calibrationRevision"`
	CloudSHA256         string `json:"cloudSha256"`
	SemanticRevision    string `json:"semanticRevision"`
	Targets             []struct {
		AnnotationID     string  `json:"annotationId"`
		Name             string  `json:"name"`
		MaxDistanceM     float64 `json:"maxDistanceM"`
		AllowStaleSearch bool    `json:"allowStaleSearch"`
		VerificationMode string  `json:"verificationMode"`
	} `json:"targets"`
}

// Only a provider read from this very planning invocation can certify a name
// and immutable target ID. Model-supplied labels or coordinates are not evidence.
func freezeSemanticRoute(arguments map[string]any, goal, robot string, trace []capability.PlanningStep) (manipulation.Intent, []string, error) {
	fail := func(message string) (manipulation.Intent, []string, error) {
		return manipulation.Intent{}, nil, fmt.Errorf("%w: semantic route: %s", intent.ErrClarificationRequired, message)
	}
	for _, phrase := range []string{"回到起点", "返回起点", "回到原地", "返回原地", "回原地", "回起点", "return to start", "return to the start"} {
		if strings.Contains(strings.ToLower(goal), phrase) {
			return fail("点云路线尚不支持返回未持久化的任务起点，请显式指定最后一个标注目标")
		}
	}
	var route proposedSemanticRoute
	wire, err := json.Marshal(arguments["semanticRoute"])
	if err != nil || json.Unmarshal(wire, &route) != nil {
		return fail("invalid route")
	}
	for _, revision := range []string{route.MapRevision, route.CalibrationRevision, route.CloudSHA256, route.SemanticRevision} {
		if decoded, err := hex.DecodeString(revision); err != nil || len(decoded) != 32 {
			return fail("invalid content revision")
		}
	}
	identity := manipulation.SemanticIdentity{RobotID: robot, MapID: route.MapID, MapRevision: route.MapRevision,
		CalibrationRevision: route.CalibrationRevision, CloudSHA256: route.CloudSHA256, FrameID: "map"}
	identityWire, _ := json.Marshal(identity)
	var identityMap map[string]any
	_ = json.Unmarshal(identityWire, &identityMap)
	type certifiedLabel struct {
		id     string
		record map[string]any
	}
	labels := map[string]certifiedLabel{}
	for _, read := range trace {
		if read.Tool != "semantic.query" || read.Verdict != "READ_OK" || read.Result["semanticRevision"] != route.SemanticRevision {
			continue
		}
		if capability.Fingerprint(read.Result["identity"]) != capability.Fingerprint(identityMap) {
			continue
		}
		rows, _ := read.Result["results"].([]any)
		for _, raw := range rows {
			row, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			id, _ := row["id"].(string)
			name, _ := row["name"].(string)
			if id == "" || name == "" {
				continue
			}
			labels[name] = certifiedLabel{id, row}
			// query.name is an exact normalized name/alias lookup on the provider.
			// Only its unique result can certify the supplied spelling as an alias.
			if alias, ok := read.Arguments["name"].(string); ok && len(rows) == 1 && read.Result["total"] == float64(1) {
				labels[alias] = certifiedLabel{id, row}
			}
		}
	}
	result := manipulation.Intent{Action: manipulation.ActionHomeRoute, RobotID: robot}
	var names []string
	for _, target := range route.Targets {
		certified, ok := labels[target.Name]
		if !ok || certified.id != target.AnnotationID {
			return fail("请先用 semantic.query 获取同一版本中每个目标的名称与 ID")
		}
		if !strings.Contains(goal, target.Name) {
			return fail("每个目标必须由用户原句明确命名；不能添加未请求的点云目标")
		}
		if certified.record["ambiguous"] == true {
			return fail("目标存在冲突，需先复核标注")
		}
		confidence, ok := certified.record["confidence"].(float64)
		if !ok || confidence < .5 || confidence > 1 {
			return fail("目标置信度不足")
		}
		if certified.record["stale"] != false && !target.AllowStaleSearch {
			return fail("目标已过期；需重新观察，或明确使用搜索模式")
		}
		if target.VerificationMode == "object" && certified.record["kind"] != "object" {
			return fail("区域不支持对象识别验证，请选择 arrival")
		}
		binding := &manipulation.SemanticTarget{Identity: identity, SemanticRevision: route.SemanticRevision,
			AnnotationID: target.AnnotationID, Name: target.Name, MaxDistanceM: target.MaxDistanceM,
			AllowStaleSearch: target.AllowStaleSearch, VerificationMode: target.VerificationMode}
		result.Sequence = append(result.Sequence, manipulation.Intent{Action: manipulation.ActionHomeRoute, RobotID: robot,
			RouteRooms: []string{target.Name}, SemanticTarget: binding})
		names = append(names, target.Name)
	}
	// The first version accepts explicitly named routes. Preserve every named
	// visit (including repeated visits), rather than validating an unordered set.
	// Longest-match prevents a label such as "北区" from consuming "北区检测台".
	spellings := make([]string, 0, len(labels))
	for label := range labels {
		spellings = append(spellings, label)
	}
	sort.Slice(spellings, func(i, j int) bool {
		if len(spellings[i]) == len(spellings[j]) {
			return spellings[i] < spellings[j]
		}
		return len(spellings[i]) > len(spellings[j])
	})
	var mentioned []string
	for cursor := 0; cursor < len(goal); {
		matched := ""
		for _, label := range spellings {
			if strings.HasPrefix(goal[cursor:], label) {
				matched = label
				break
			}
		}
		if matched != "" {
			mentioned = append(mentioned, labels[matched].id)
			cursor += len(matched)
		} else {
			cursor++
		}
	}
	if len(mentioned) != len(route.Targets) {
		return fail("目标访问次数与原句不一致；重复访问必须保留为独立检查点")
	}
	for index, id := range mentioned {
		if route.Targets[index].AnnotationID != id {
			return fail("目标访问顺序与原句不一致")
		}
	}
	if len(result.Sequence) == 0 {
		return fail("route must contain targets")
	}
	return result, names, nil
}
