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
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"google.golang.org/protobuf/types/known/structpb"
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
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
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
		if item.Name == "robot.task" || item.Name == actionloop.ContextReadTool || item.Name == "propose_capability_plan" {
			return "", nil, fmt.Errorf("provider uses reserved planning capability name")
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
	entries["robot.task"] = capability.Manifest{Name: "robot.task", Description: "通过闭环执行器执行一段导航或家庭抓放。导航子任务只写本段目标；家庭抓放必须在同一次调用中给出完整房间路线、物品与容器，例如『从客厅出发，去厨房把杯子放进收纳盘，然后回到客厅』，不能拆成不带房间的『把杯子放进收纳盘』。request 使用简短、可独立理解的动作句，不附加地图编号、条件、状态查询或英文地点 ID；这些由其他能力和运行时契约处理。不得嵌套标定、建图或通用目标。",
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
	// A configured GOAL model owns the ordering for every natural-language goal.
	// The offline grammar is used only when no model route is configured.
	useModel := p.Decider != nil
	var trace []capability.PlanningStep
	if useModel {
		calls, trace, err = p.modelPlan(ctx, request, robot, entries)
		if err != nil {
			// No calls are returned on failure. A caller can inspect the rejected
			// rounds, but this incomplete bundle cannot become an executable task.
			return orchestration.Bundle{Source: orchestration.SourceLLM, Capabilities: &capability.Plan{
				RobotID: robot, CatalogRevision: capability.Fingerprint(entries), PlanningTrace: trace,
			}}, true, err
		}
	}
	if !useModel {
		if err := p.freezeCalls(calls, request, robot, entries); err != nil {
			return orchestration.Bundle{}, true, err
		}
	}
	source := orchestration.SourceDeterministic
	if useModel {
		source = orchestration.SourceLLM
	}
	return orchestration.Bundle{Source: source, Capabilities: &capability.Plan{RobotID: robot, CatalogRevision: capability.Fingerprint(entries), Calls: calls, PlanningTrace: trace}}, true, nil
}

// modelPlan lets the GOAL model inspect current read-only provider state and
// revise a proposal before an operator sees any mutating call. The harness
// offers no effectful tool in this phase; it only freezes the final proposal.
func (p *Planner) modelPlan(ctx context.Context, goal, robot string, entries map[string]capability.Manifest) ([]capability.Call, []capability.PlanningStep, error) {
	// Provider-declared semantic views are the only read results that may enter
	// model context. Raw grids, image frames and high-rate sensor feeds are not
	// offered, even when a runtime exposes them to its local navigation layer.
	modelEntries := make(map[string]capability.Manifest, len(entries))
	for name, manifest := range entries {
		if manifest.MutatesWorld || len(manifest.Contract.PlanningFields) > 0 {
			modelEntries[name] = manifest
		}
	}
	wire, _ := json.Marshal(modelEntries)
	schema := map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{
		"calls": map[string]any{"type": "array", "minItems": 1, "maxItems": 16, "items": map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{
			"tool": map[string]any{"type": "string"}, "arguments": map[string]any{"type": "object", "additionalProperties": true}}, "required": []string{"tool", "arguments"}}}}, "required": []string{"calls"}}
	tools := []actionloop.Tool{{Name: "propose_capability_plan", Description: "提交完整、有序的执行计划供审批。涉及条件时先读取相关状态再决定分支；计划必须覆盖用户每项要求。每个 arguments 遵守目录中的 inputSchema。目录：" + string(wire), InputSchema: schema}}
	names := make([]string, 0, len(modelEntries))
	for name, m := range modelEntries {
		if !m.MutatesWorld {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	for _, name := range names {
		m := modelEntries[name]
		tools = append(tools, actionloop.Tool{Name: name, Description: "审批前只读查询：" + m.Description, InputSchema: m.InputSchema})
	}
	var trace []capability.PlanningStep
	var history []actionloop.Round
	archives := map[string]agentcontext.Artifact{}
	// There is no task or revision yet. Bind the real catalog robot without
	// inventing either identity for this pre-approval planning invocation.
	scope := agentcontext.Scope{RobotID: robot}
	document := agentcontext.Document{SchemaVersion: agentcontext.Version, Role: "planning", Stage: "planning", Goal: goal, Scope: scope,
		Constraints: []string{"审批前只允许查询目录声明的语义只读字段；提案和历史回查不执行机器人动作。"},
		Records: []agentcontext.Record{{ID: "planning:catalog", Kind: "guard", Scope: scope,
			Statement: "本轮冻结能力目录摘要：" + capability.Fingerprint(entries)}},
	}
	feedback := "先判断是否需要只读查询；若目标含条件，请读取相关状态，再提交覆盖全部目标的计划。提案不会执行，物理动作需后续审批。"
	reads, proposals, decisionErrors := 0, 0, 0
	for round := 1; round <= 10; round++ {
		// Reserve the last two decisions for a complete proposal or an explicit
		// refusal. The model sees the same bounds that dispatch enforces below.
		proposalOnly := round >= 9
		roundTools := append([]actionloop.Tool(nil), tools[:1]...)
		if !proposalOnly && reads < 6 {
			roundTools = append(roundTools, tools[1:]...)
		}
		budgetState, _ := json.Marshal(map[string]any{
			"policy_version": "goal-planning-budget.v1", "round": round,
			"remaining_decisions": 11 - round, "remaining_provider_reads": 6 - reads,
			"remaining_proposals": 3 - proposals, "proposal_only": proposalOnly,
			"provider_reads_available": !proposalOnly && reads < 6,
		})
		roundDocument := document
		roundDocument.Records = append(append([]agentcontext.Record(nil), document.Records...),
			agentcontext.Record{ID: "planning:budget", Kind: "guard", Scope: scope, Statement: string(budgetState)})
		budgetFeedback := fmt.Sprintf("规划预算：第%d/10轮，含本轮剩%d次决策、%d次Provider查询、%d次提案。第9、10轮只提交计划或cannot_proceed；禁止finish。", round, 11-round, 6-reads, 3-proposals)
		if proposalOnly {
			budgetFeedback += "现在仅可propose_capability_plan或cannot_proceed；不能继续查询或历史回查。证据不足时必须澄清，不能补造事实或省略目标。"
		} else if reads >= 6 {
			budgetFeedback += "Provider查询预算已耗尽，请提交完整计划；如有归档可在第8轮结束前回查已有证据，不能取得新观测。仍缺条件时用cannot_proceed。"
		} else {
			budgetFeedback += "已有足够证据时立即提交完整计划，不必耗尽查询额度。"
		}
		request := actionloop.Request{Role: "planning", Goal: goal, Round: round, History: history,
			Observation: actionloop.Observation{Summary: feedback + "\n" + budgetFeedback, Context: &roundDocument}, Tools: roundTools}
		snapshot, err := actionloop.SnapshotFor(request)
		if err != nil {
			trace = append(trace, capability.PlanningStep{Round: round, Verdict: "CONTEXT_REJECTED", Detail: err.Error()})
			return nil, trace, fmt.Errorf("%w: build planning context: %v", intent.ErrClarificationRequired, err)
		}
		if snapshot.Scope != scope {
			return nil, trace, fmt.Errorf("%w: planning context scope changed", intent.ErrClarificationRequired)
		}
		request.ContextSnapshot = &snapshot
		for _, artifact := range snapshot.Artifacts {
			archives[artifact.SHA256] = artifact
		}
		var archiveTool *actionloop.Tool
		if len(archives) > 0 && !proposalOnly {
			view := snapshot
			view.Artifacts = nil
			keys := make([]string, 0, len(archives))
			for sha := range archives {
				keys = append(keys, sha)
			}
			sort.Strings(keys)
			for _, sha := range keys {
				view.Artifacts = append(view.Artifacts, archives[sha])
			}
			reader := actionloop.ContextArchiveTool(view)
			archiveTool = &reader
			request.Tools = append(request.Tools, reader)
		}
		// Pass a snapshot pointer before Decide: LLMDecider retains the exact
		// HTTP body in it even when the response or transport fails.
		decision, err := p.Decider.Decide(ctx, request)
		if err != nil {
			decisionErrors++
			step := capability.PlanningStep{Round: round, Verdict: "MODEL_DECISION_REJECTED", Detail: err.Error(), Context: &snapshot}
			trace = append(trace, step)
			history = append(history, actionloop.Round{Round: round, Verdict: step.Verdict, Detail: step.Detail, ObservedAt: time.Now().UTC()})
			feedback = "上一轮模型输出无效：" + err.Error() + "。每轮只选一个当前提供的工具，并遵守剩余预算。"
			if decisionErrors >= 3 {
				return nil, trace, fmt.Errorf("%w: model could not select one planning tool: %v", intent.ErrClarificationRequired, err)
			}
			continue
		}
		decisionErrors = 0
		if decision.Blocked != "" {
			trace = append(trace, capability.PlanningStep{Round: round, Verdict: "BLOCKED", Detail: decision.Blocked, Context: &snapshot})
			return nil, trace, fmt.Errorf("%w: %s", intent.ErrClarificationRequired, decision.Blocked)
		}
		if decision.Done {
			trace = append(trace, capability.PlanningStep{Round: round, Verdict: "REJECTED", Detail: "goal requires a plan, not a completion claim", Context: &snapshot})
			return nil, trace, fmt.Errorf("%w: goal requires a plan, not a completion claim", intent.ErrClarificationRequired)
		}
		step := capability.PlanningStep{Round: round, Tool: decision.Tool, Arguments: decision.Arguments, Context: &snapshot}
		offered := false
		for _, tool := range request.Tools {
			if tool.Name == decision.Tool {
				offered = true
				break
			}
		}
		if !offered {
			// Custom deciders must obey the same per-round allowlist as the LLM
			// response parser; selecting a known but removed tool cannot dispatch it.
			step.Verdict, step.Detail = "REJECTED", "tool unavailable in this planning round"
			feedback = "只能调用本轮提供的工具；预算或阶段不允许的查询不会执行。"
		} else if decision.Tool == "propose_capability_plan" {
			proposals++
			var proposed struct {
				Calls []capability.Call `json:"calls"`
			}
			if err := capability.Validate(decision.Arguments, schema); err == nil {
				raw, _ := json.Marshal(decision.Arguments)
				err = json.Unmarshal(raw, &proposed)
			}
			if err == nil {
				err = validateProposedCalls(proposed.Calls, modelEntries)
			}
			if err == nil {
				err = p.freezeCalls(proposed.Calls, goal, robot, entries)
			}
			if err == nil {
				step.Verdict = "FROZEN_FOR_APPROVAL"
				trace = append(trace, step)
				return proposed.Calls, trace, nil
			}
			step.Verdict, step.Detail = "REJECTED", err.Error()
			feedback = "提案未通过校验：" + err.Error() + "。请修正并重新提交完整计划；不要省略用户步骤。"
			if proposals >= 3 {
				return nil, append(trace, step), fmt.Errorf("%w: %v", intent.ErrClarificationRequired, err)
			}
		} else if decision.Tool == actionloop.ContextReadTool && archiveTool != nil {
			result, readErr := archiveTool.Call(ctx, decision.Arguments)
			if readErr != nil {
				step.Verdict, step.Detail = "CONTEXT_READ_FAILED", readErr.Error()
			} else if !result.Success {
				step.Verdict, step.Detail = "CONTEXT_READ_FAILED", result.Message
			} else {
				step.Verdict, step.Result = "CONTEXT_READ_OK", result.Detail
			}
			feedback = "历史回查已返回；这是同一规划作用域的原始记录，不是新机器人观测，也不增加执行权限。"
		} else if m, ok := modelEntries[decision.Tool]; ok && !m.MutatesWorld {
			if err := capability.Validate(decision.Arguments, m.InputSchema); err != nil {
				step.Verdict, step.Detail = "REJECTED", err.Error()
				feedback = "只读工具参数无效：" + err.Error()
			} else {
				args, err := structpb.NewStruct(decision.Arguments)
				if err != nil {
					step.Verdict, step.Detail = "REJECTED", err.Error()
					return nil, append(trace, step), err
				}
				readCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
				reads++ // Invalid arguments and archive reads never call the provider.
				response, callErr := p.Provider.CallService(readCtx, &robotv1.ServiceRequest{RobotId: robot, Name: decision.Tool, Parameters: args})
				cancel()
				if callErr != nil {
					step.Verdict, step.Detail = "READ_FAILED", callErr.Error()
				} else if response == nil {
					step.Verdict, step.Detail = "READ_FAILED", "empty provider response"
				} else if !response.Ok {
					step.Verdict, step.Detail = "READ_FAILED", response.Code+": "+response.Message
				} else if response.Result == nil {
					step.Verdict, step.Detail = "READ_FAILED", "provider returned no structured result"
				} else {
					full := response.Result.AsMap()
					result := map[string]any{}
					for _, field := range m.Contract.PlanningFields {
						if value, ok := full[field]; ok {
							result[field] = value
						}
					}
					if err := validatePlanningView(result, 0); err != nil {
						step.Verdict, step.Detail = "READ_REJECTED", err.Error()
						return nil, append(trace, step), fmt.Errorf("%w: %s returned non-semantic planning data: %v", intent.ErrClarificationRequired, decision.Tool, err)
					}
					encoded, err := json.Marshal(result)
					if err != nil || len(encoded) > 8192 {
						step.Verdict, step.Detail = "READ_REJECTED", "planning read result exceeds 8 KiB"
						return nil, append(trace, step), fmt.Errorf("%w: planning read result exceeds 8 KiB", intent.ErrClarificationRequired)
					}
					step.Verdict, step.Result = "READ_OK", result
				}
				feedback = "只读工具 " + decision.Tool + " 的回执已记录；请根据证据和本轮剩余预算提交完整计划或选择允许的工具。"
			}
		} else {
			step.Verdict, step.Detail = "REJECTED", "tool unavailable in planning phase"
			feedback = "只能调用提供的只读工具或 propose_capability_plan。"
		}
		trace = append(trace, step)
		history = append(history, actionloop.Round{Round: round, Tool: step.Tool, Arguments: step.Arguments, Verdict: step.Verdict,
			Detail: step.Detail, ResultDetail: step.Result, Reason: decision.Reason, ObservedAt: time.Now().UTC()})
	}
	return nil, trace, fmt.Errorf("%w: planning exceeded 10 model decisions", intent.ErrClarificationRequired)
}

// A provider must opt in to planningFields, and this second boundary rejects
// obvious dense sensor payloads even if a provider misdeclares its projection.
func validatePlanningView(value any, depth int) error {
	if depth > 8 {
		return fmt.Errorf("view nesting exceeds 8 levels")
	}
	switch typed := value.(type) {
	case map[string]any:
		if len(typed) > 256 {
			return fmt.Errorf("view contains too many fields")
		}
		for key, item := range typed {
			lower := strings.ToLower(key)
			for _, forbidden := range []string{"image", "rgb", "depth", "imusample", "imudata", "imuraw", "imu_", "gyro", "accelerometer", "pointcloud", "pointcolors", "rawsensor", "pixels", "tensor", "cells", "scans"} {
				if strings.Contains(lower, forbidden) {
					return fmt.Errorf("raw sensor field %q", key)
				}
			}
			for _, forbidden := range []string{"imu", "points", "samples", "buffer", "payload", "bytes", "data"} {
				if lower == forbidden {
					return fmt.Errorf("raw sensor field %q", key)
				}
			}
			if err := validatePlanningView(item, depth+1); err != nil {
				return err
			}
		}
	case []any:
		if len(typed) > 256 {
			return fmt.Errorf("view contains a dense array")
		}
		for _, item := range typed {
			if err := validatePlanningView(item, depth+1); err != nil {
				return err
			}
		}
	case string:
		if len(typed) > 2048 {
			return fmt.Errorf("view contains a large string")
		}
	}
	return nil
}

func (p *Planner) freezeCalls(calls []capability.Call, goal, robot string, entries map[string]capability.Manifest) error {
	if len(calls) == 0 || len(calls) > 16 {
		return fmt.Errorf("goal requires a complete bounded plan")
	}
	var allRoutes []string
	for i := range calls {
		call := &calls[i]
		argumentWire, marshalErr := json.Marshal(call.Arguments)
		if marshalErr != nil || len(argumentWire) > 65536 {
			return fmt.Errorf("capability arguments exceed size limit or are invalid")
		}
		m, ok := entries[call.Tool]
		if !ok {
			return fmt.Errorf("%w: capability unavailable: %s", intent.ErrClarificationRequired, call.Tool)
		}
		if err := capability.Validate(call.Arguments, m.InputSchema); err != nil {
			return fmt.Errorf("%w: %s: %v", intent.ErrClarificationRequired, call.Tool, err)
		}
		if call.Tool == "robot.task" {
			if p.ParseLegacy == nil {
				return fmt.Errorf("composite intent planner unavailable")
			}
			parsed, err := p.ParseLegacy(call.Arguments["request"].(string))
			if err != nil {
				return err
			}
			var composite manipulation.Intent
			if err := json.Unmarshal(parsed, &composite); err != nil {
				return err
			}
			if err := intent.ValidateHomeRouteTargets(call.Arguments["request"].(string), composite); err != nil {
				return err
			}
			for _, item := range composite.Tasks() {
				allRoutes = append(allRoutes, item.RouteRooms...)
				if item.RobotID != "" && item.RobotID != robot {
					return fmt.Errorf("composite goal targets another robot")
				}
			}
			composite.RobotID = robot
			for i := range composite.Sequence {
				composite.Sequence[i].RobotID = robot
			}
			call.LegacyIntent, err = json.Marshal(composite)
			if err != nil {
				return err
			}
		}
	}
	return intent.ValidateHomeRouteTargets(goal, manipulation.Intent{Action: manipulation.ActionHomeRoute, RouteRooms: allRoutes})
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
