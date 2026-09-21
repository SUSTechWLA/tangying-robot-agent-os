package agentcontext

// StageDecisionContract describes the requested decision, never a world answer.
// Field names and operational semantics must be explicit at the model boundary.
func StageDecisionContract(stage string) map[string]any {
	fields := map[string]map[string]string{
		"goal":         {"target_id": "string", "destination_id": "string", "forbidden_ids": "unique string array", "clarification_required": "boolean"},
		"planning":     {"done_steps": "unique step ID array", "ready_steps": "unique step ID array", "blocked_steps": "unique step ID array"},
		"tool_result":  {"execution_state": "string", "physical_state": "VERIFIED|FALSIFIED|UNKNOWN", "retry_allowed": "boolean"},
		"verification": {"verdict": "VERIFIED|FALSIFIED|UNKNOWN", "usable_records": "unique record ID array", "excluded_records": "unique record ID array"},
		"ops":          {"diagnosis": "string", "priority": "P0|P1|P2", "investigate_tool": "registered tool name"},
		"reflection":   {"affected_steps": "unique step ID array", "preserve_steps": "unique step ID array", "invalid_assumption": "string"},
		"recovery":     {"tool": "registered tool name", "arguments": "object with exactly step_id and object_id string keys", "needs_approval": "boolean copied from selected tool requires_approval"},
		"handoff":      {"current_revision": "integer", "completed_steps": "unique step ID array", "pending_steps": "unique step ID array", "unresolved_step_ids": "unique step ID array", "next_tool": "registered tool name"},
	}
	rules := map[string]string{
		"planning": "这是直接依赖的快照分类任务。VERIFIED 是当前快照中已确认的步骤状态。只检查该步骤 depends_on 直接列出的步骤，不传播祖先依赖，不重新验证已 VERIFIED 的步骤。即使一个已 VERIFIED 步骤的祖先现为 PENDING，也不改变此题的直接依赖分类；一致性诊断应单列。每个步骤恰好进入 done_steps、ready_steps、blocked_steps 之一。",
		"recovery": "arguments 的输出键名必须是 step_id 和 object_id；值分别取 goal.current_step_id 与 goal.object_id，键名不包含 goal.。needs_approval 表示工具目录的静态批准要求，不表示当前是否还缺批准；即使 guard.approved=true，选择 requires_approval=true 的工具仍输出 needs_approval=true。选择顺序严格为 cancelled、terminal_known、verified、fresh、plan_ready、approved；必须读取布尔字段的值，字段存在不等于 true。",
	}
	return map[string]any{"version": "stage-decision-contract.v1", "stage": stage, "answer_fields": fields[stage], "extra_answer_fields_allowed": false, "operational_semantics": rules[stage], "authority": "描述待回答的问题，不是观测、验证、批准或动作执行结果。"}
}

func WithDecisionContract(p DecisionContext) DecisionContext {
	goal := map[string]any{}
	for k, v := range p.Goal {
		goal[k] = v
	}
	goal["decision_contract"] = StageDecisionContract(p.Stage)
	p.Goal = goal
	return p
}
