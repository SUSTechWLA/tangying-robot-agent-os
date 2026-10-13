package capabilityagent

import (
	"encoding/json"
	"sort"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
)

// Schemas are structural tool parameters, not JSON quoted inside a description.
// The latter was duplicated in both the managed context and provider tool list,
// exhausting the default budget after one point-cloud query. Keep every input
// constraint and effect contract; only presentation duplication is removed.
func proposalContract(entries map[string]capability.Manifest) (map[string]any, string) {
	names := make([]string, 0, len(entries))
	for name := range entries {
		names = append(names, name)
	}
	sort.Strings(names)
	branches := make([]any, 0, len(names))
	descriptions := make(map[string]any, len(names))
	for _, name := range names {
		m := entries[name]
		branches = append(branches, map[string]any{"type": "object", "additionalProperties": false,
			"properties": map[string]any{"tool": map[string]any{"type": "string", "enum": []any{name}}, "arguments": m.InputSchema},
			"required":   []any{"tool", "arguments"}})
		// Output schemas and planningFields govern harness-side observation
		// projection. They do not constrain a proposal's arguments or authority.
		descriptions[name] = struct {
			Description  string                   `json:"description"`
			MutatesWorld bool                     `json:"mutatesWorld"`
			Effects      []string                 `json:"effects,omitempty"`
			Resources    []string                 `json:"resources,omitempty"`
			Operation    *capability.Operation    `json:"operation,omitempty"`
			Verification *capability.Verification `json:"verification,omitempty"`
		}{m.Description, m.MutatesWorld, m.Contract.Effects, m.Contract.Resources, m.Contract.Operation, m.Contract.Verification}
	}
	wire, _ := json.Marshal(descriptions)
	schema := map[string]any{"type": "object", "additionalProperties": false,
		"properties": map[string]any{"calls": map[string]any{"type": "array", "minItems": 1, "maxItems": 16, "items": proposalAlternatives(branches)}},
		"required":   []any{"calls"}}
	return schema, "提交完整、有序的执行计划供审批。涉及条件时先读取相关状态再决定分支；计划必须覆盖用户每项要求。每个 calls 项必须选择工具名匹配的参数分支；完整参数约束在本工具 JSON Schema 中。以下能力说明只解释用途、副作用、操作与复验契约：" + string(wire)
}

// The shared schema contract bounds oneOf to sixteen branches. Nested groups
// represent the entire catalog (up to 64 provider services plus robot.task)
// without truncating less common capabilities or weakening their validation.
func proposalAlternatives(branches []any) map[string]any {
	if len(branches) == 1 {
		return branches[0].(map[string]any)
	}
	if len(branches) > 16 {
		groups := make([]any, 0, (len(branches)+15)/16)
		for start := 0; start < len(branches); start += 16 {
			end := start + 16
			if end > len(branches) {
				end = len(branches)
			}
			groups = append(groups, proposalAlternatives(branches[start:end]))
		}
		return proposalAlternatives(groups)
	}
	return map[string]any{"oneOf": branches}
}
