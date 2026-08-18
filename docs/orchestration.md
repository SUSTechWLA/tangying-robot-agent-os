# LLM 编排能力、指标与提升方法

## 编排边界

```text
用户自然语言
  -> agent intent parser（LLM 可选，deterministic 兜底）
  -> orchestration.Planner（LLM 可选，deterministic 兜底）
  -> taskgraph.TaskPlan（能力技能图模板）
  -> Local Agent 落地实体并生成 approval/deadline/lease/idempotency
  -> Robot Runtime capability check + Safety Supervisor
```

LLM 只选择和排序注册能力。计划不得携带安全字段，且每个工具和参数都要通过 `[]skills.SkillManifest` 生成的目录校验。

## 扩展能力

- 在 domain `Catalog()` 添加 `SkillManifest` 即可公开新技能。
- `RequiredParameters` 自动进入 prompt 和 Guard 校验。
- `SideEffect` 和 `SafetyLevel` 决定审批与本地安全包络。
- `skills/manipulation.Plan` 始终作为确定性后备。

## 指标

本地接口 `GET /v1/orchestration/metrics` 提供任务总量、序列任务、计划来源、LLM 候选通过/拒绝、fallback、终态成功率和综合观察分。分数只用于迭代，不参与物理放行。

## 提升方法

1. 用 `AGENT_ORCHESTRATION_SAMPLES=3` 采样多个候选并选择通过校验的一致计划。
2. 在 `orchestration/llm_test.go` 增加 golden request 与期望计划。
3. 按任务中的 rejection 原因修正能力描述或 prompt，不放宽安全校验。
4. 保持 `make sim2real-check` 通过，并把实体失败转为 verify/recover capability。
5. 可以降低 fallback 率，但不删除 deterministic fallback。
