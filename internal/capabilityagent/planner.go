// Package capabilityagent connects registered capabilities to the existing task
// authority. Providers own contracts; models can propose calls but cannot grant
// permissions, replace execution identities or manufacture completion evidence.
package capabilityagent

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"regexp"
	"strconv"
	"strings"
)

type Provider interface {
	ListServices(context.Context) (*robotv1.ServiceCatalog, error)
	CallService(context.Context, *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error)
}
type Planner struct {
	Provider    Provider
	Decider     actionloop.Decider
	ParseLegacy func(string) (json.RawMessage, error)
}

func Catalogue(ctx context.Context, provider Provider) (string, map[string]capability.Manifest, error) {
	catalog, err := provider.ListServices(ctx)
	if err != nil {
		return "", nil, err
	}
	if catalog == nil || catalog.RobotId == "" {
		return "", nil, fmt.Errorf("capability catalog has no robot identity")
	}
	if len(catalog.Services) > 64 {
		return "", nil, fmt.Errorf("service catalog exceeds 64 entries")
	}
	raw := map[string]*robotv1.ServiceDefinition{}
	entries := map[string]capability.Manifest{}
	for _, item := range catalog.Services {
		if item == nil {
			return "", nil, fmt.Errorf("empty service descriptor")
		}
		if item.Name == "robot.task" {
			return "", nil, fmt.Errorf("provider uses reserved composite capability name")
		}
		if _, exists := raw[item.Name]; exists {
			return "", nil, fmt.Errorf("duplicate service descriptor %s", item.Name)
		}
		raw[item.Name] = item
		if !item.Available {
			continue
		}
		m := capability.Manifest{Name: item.Name, Description: item.Description, InputSchema: item.InputSchema.AsMap(), MutatesWorld: item.MutatesWorld}
		wire, err := json.Marshal(item.Contract.AsMap())
		if err != nil {
			return "", nil, err
		}
		decoder := json.NewDecoder(bytes.NewReader(wire))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&m.Contract); err != nil {
			continue // an unsupported contract cannot authorize execution
		}
		if err := m.Validate(); err != nil {
			continue
		} // unsupported writers are not offered
		if _, duplicate := entries[m.Name]; duplicate {
			return "", nil, fmt.Errorf("duplicate capability %s", m.Name)
		}
		entries[m.Name] = m
	}
	for name, m := range entries {
		validRead := func(name string) bool {
			r, ok := entries[name]
			return ok && !r.MutatesWorld && capability.Validate(map[string]any{}, r.InputSchema) == nil
		}
		valid := true
		if v := m.Contract.Verification; v != nil {
			valid = validRead(v.Service)
		}
		if op := m.Contract.Operation; op != nil {
			cancel := raw[op.CancelService]
			valid = valid && validRead(op.StatusService) && cancel != nil && cancel.Available && cancel.MutatesWorld && capability.Validate(map[string]any{}, cancel.InputSchema.AsMap()) == nil
		}
		for _, resource := range m.Contract.Resources {
			if resource != "robot" {
				valid = false
			}
		}
		if !valid {
			delete(entries, name)
		}
	}
	entries["robot.task"] = capability.Manifest{Name: "robot.task", Description: "通过已有闭环执行器执行导航、取物、抓放自然语言子任务；每次调用只写本步动作，不重复前一步的到达条件，步骤先后由 calls 顺序保证；不得包含标定或建图，也不得嵌套通用目标。",
		InputSchema:  map[string]any{"type": "object", "properties": map[string]any{"request": map[string]any{"type": "string", "minLength": 1, "maxLength": 2000}}, "required": []string{"request"}, "additionalProperties": false},
		MutatesWorld: true, Contract: capability.Contract{Version: "1", Effects: []string{"PHYSICAL_MOTION"}, Resources: []string{"robot"}}}
	return catalog.RobotId, entries, nil
}
func (p *Planner) PlanGoal(ctx context.Context, request string) (orchestration.Bundle, bool, error) {
	if strings.TrimSpace(request) == "" || len(request) > 8000 {
		return orchestration.Bundle{}, true, fmt.Errorf("invalid goal request")
	}
	calls, handled := literalCalls(request)
	if !handled && p.Decider == nil {
		for _, marker := range []string{"标定", "建图", "地图", "探索"} {
			if strings.Contains(request, marker) {
				return orchestration.Bundle{}, true, fmt.Errorf("%w: 离线目标语法不能完整表达这句话，请配置 GOAL 模型或使用明确步骤", intent.ErrClarificationRequired)
			}
		}
		return orchestration.Bundle{}, false, nil
	}
	robot, entries, err := Catalogue(ctx, p.Provider)
	if err != nil {
		return orchestration.Bundle{}, true, err
	}
	// Exact whole-clause requests need no inference. Other natural language uses
	// the configured GOAL model, with the same provider schema checks.
	useModel := p.Decider != nil && !handled
	if useModel {
		wire, _ := json.Marshal(entries)
		schema := map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{
			"calls": map[string]any{"type": "array", "minItems": 1, "maxItems": 16, "items": map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{
				"tool": map[string]any{"type": "string"}, "arguments": map[string]any{"type": "object", "additionalProperties": true}}, "required": []string{"tool", "arguments"}}}}, "required": []string{"calls"}}
		feedback := "完整能力契约在 propose_capability_plan 工具描述中；只提交计划，执行后端负责核验。"
		for attempt := 1; attempt <= 2; attempt++ {
			decision, err := p.Decider.Decide(ctx, actionloop.Request{Role: "planning", Goal: request, Round: attempt,
				Observation: actionloop.Observation{Summary: feedback},
				Tools:       []actionloop.Tool{{Name: "propose_capability_plan", Description: "提交按用户顺序排列的能力调用计划。每个 arguments 必须完全遵守对应目录中的 inputSchema。目录：" + string(wire), InputSchema: schema}}})
			if err != nil {
				return orchestration.Bundle{}, true, err
			}
			if decision.Tool != "propose_capability_plan" {
				return orchestration.Bundle{}, true, fmt.Errorf("%w: %s", intent.ErrClarificationRequired, decision.Blocked)
			}
			if err := capability.Validate(decision.Arguments, schema); err != nil {
				return orchestration.Bundle{}, true, fmt.Errorf("%w: %v", intent.ErrClarificationRequired, err)
			}
			raw, _ := json.Marshal(decision.Arguments)
			var proposed struct {
				Calls []capability.Call `json:"calls"`
			}
			if err := json.Unmarshal(raw, &proposed); err != nil {
				return orchestration.Bundle{}, true, err
			}
			calls = proposed.Calls
			if err := validateProposedCalls(calls, entries); err == nil {
				break
			} else if attempt == 2 {
				return orchestration.Bundle{}, true, fmt.Errorf("%w: %v", intent.ErrClarificationRequired, err)
			} else {
				feedback = "上一份计划未通过能力目录校验：" + err.Error() + "。请根据目录重新提交完整计划；不要丢失用户步骤或加入未请求的动作。"
			}
		}
	}
	if len(calls) == 0 || len(calls) > 16 {
		return orchestration.Bundle{}, true, fmt.Errorf("goal requires a complete bounded plan")
	}
	for i := range calls {
		call := &calls[i]
		argumentWire, marshalErr := json.Marshal(call.Arguments)
		if marshalErr != nil || len(argumentWire) > 65536 {
			return orchestration.Bundle{}, true, fmt.Errorf("capability arguments exceed size limit or are invalid")
		}
		m, ok := entries[call.Tool]
		if !ok {
			return orchestration.Bundle{}, true, fmt.Errorf("%w: capability unavailable: %s", intent.ErrClarificationRequired, call.Tool)
		}
		if err := capability.Validate(call.Arguments, m.InputSchema); err != nil {
			return orchestration.Bundle{}, true, fmt.Errorf("%w: %s: %v", intent.ErrClarificationRequired, call.Tool, err)
		}
		if call.Tool == "robot.task" {
			if p.ParseLegacy == nil {
				return orchestration.Bundle{}, true, fmt.Errorf("composite intent planner unavailable")
			}
			parsed, err := p.ParseLegacy(call.Arguments["request"].(string))
			if err != nil {
				return orchestration.Bundle{}, true, err
			}
			var composite manipulation.Intent
			if err := json.Unmarshal(parsed, &composite); err != nil {
				return orchestration.Bundle{}, true, err
			}
			if err := intent.ValidateHomeRouteTargets(call.Arguments["request"].(string), composite); err != nil {
				return orchestration.Bundle{}, true, err
			}
			for _, item := range composite.Tasks() {
				if item.RobotID != "" && item.RobotID != robot {
					return orchestration.Bundle{}, true, fmt.Errorf("composite goal targets another robot")
				}
			}
			composite.RobotID = robot
			for i := range composite.Sequence {
				composite.Sequence[i].RobotID = robot
			}
			call.LegacyIntent, err = json.Marshal(composite)
			if err != nil {
				return orchestration.Bundle{}, true, err
			}
		}
	}
	source := orchestration.SourceDeterministic
	if useModel {
		source = orchestration.SourceLLM
	}
	return orchestration.Bundle{Source: source, Capabilities: &capability.Plan{RobotID: robot, CatalogRevision: capability.Fingerprint(entries), Calls: calls}}, true, nil
}

func validateProposedCalls(calls []capability.Call, entries map[string]capability.Manifest) error {
	if len(calls) == 0 || len(calls) > 16 {
		return fmt.Errorf("goal requires a complete bounded plan")
	}
	for _, call := range calls {
		m, ok := entries[call.Tool]
		if !ok {
			return fmt.Errorf("capability unavailable: %s", call.Tool)
		}
		if err := capability.Validate(call.Arguments, m.InputSchema); err != nil {
			return fmt.Errorf("%s: %w", call.Tool, err)
		}
	}
	return nil
}

var separators = regexp.MustCompile(`[,，;；。]|然后|再然后|接着|并且`)
var mappingLiteral = regexp.MustCompile(`^(?:请)?(?:为家里|给家里|为家庭|给家庭)?(?:重新)?(?:探索|巡检)?(?:建图|建立地图|构建地图)(?:最多行驶([0-9]+(?:\.[0-9]+)?)米)?(?:最多([0-9]+)轮)?$`)
var navigationLiteral = regexp.MustCompile(`^(请)?(再)?(去|前往|移动到).+$`)

// Offline grammar accepts whole clauses only. It does not discard unknown
// constraints or interpret negative mentions of mapping as permission to move.
func literalCalls(request string) ([]capability.Call, bool) {
	// A complete route accepted by the conservative intent grammar is already
	// an exact composite task. Keep the whole sentence together so a model
	// cannot turn a later room into a condition on an earlier step.
	if route, err := intent.NewDeterministicParser().Parse(request); err == nil &&
		route.Action == manipulation.ActionHomeRoute && len(route.Sequence) == 0 && len(route.RouteRooms) >= 2 {
		return []capability.Call{{Tool: "robot.task", Arguments: map[string]any{"request": request}}}, true
	}
	var calls []capability.Call
	generic := false
	for _, raw := range separators.Split(request, -1) {
		part := strings.TrimSpace(raw)
		if part == "" {
			continue
		}
		part = strings.TrimPrefix(part, "再")
		call := capability.Call{Arguments: map[string]any{}}
		switch {
		case part == "检查标定" || part == "读取标定" || part == "请检查标定":
			call.Tool = "calibration.get"
			generic = true
		case part == "运行标定" || part == "重新标定" || part == "请运行标定":
			call.Tool = "calibration.run"
			generic = true
		case part == "检查地图" || part == "读取建图状态":
			call.Tool = "mapping.status"
			generic = true
		case mappingLiteral.MatchString(part):
			call.Tool = "mapping.build"
			call.Arguments = map[string]any{"mode": "explore"}
			if strings.Contains(part, "巡检") {
				call.Arguments["mode"] = "survey"
			}
			limits := mappingLiteral.FindStringSubmatch(part)
			if limits[1] != "" {
				call.Arguments["maxTravelM"], _ = strconv.ParseFloat(limits[1], 64)
			}
			if limits[2] != "" {
				call.Arguments["maxLegs"], _ = strconv.Atoi(limits[2])
			}
			generic = true
		case part == "检查语义地点":
			call.Tool = "semantic.locations"
			generic = true
		case navigationLiteral.MatchString(part):
			call.Tool = "robot.task"
			call.Arguments["request"] = part
		default:
			return nil, false
		}
		calls = append(calls, call)
	}
	return calls, generic
}
