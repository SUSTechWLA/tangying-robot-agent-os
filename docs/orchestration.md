# LLM 编排能力、指标与提升方法

## 编排边界

```text
用户自然语言
  -> agent intent parser（完整已知指令优先 deterministic，其余表达可选 LLM；歧义要求澄清）
  -> orchestration.Planner（LLM 可选，deterministic 兜底）
  -> taskgraph.TaskPlan（能力技能图模板）
  -> Local Agent 落地实体并生成 approval/deadline/lease/idempotency
  -> Robot Runtime capability check + Safety Supervisor
```

LLM 只选择和排序注册能力。计划不得携带安全字段，且每个工具和参数都要通过 `[]skills.SkillManifest` 生成的目录校验。

## 扩展能力

- 在 domain `Catalog()` 添加 `SkillManifest` 公开能力描述；还必须实现 Runtime capability、输入校验、所需观测与后置条件，目录声明不会自动产生硬件能力。
- `RequiredParameters` 自动进入 prompt 和 Guard 校验。
- `SideEffect` 和 `SafetyLevel` 决定审批与本地安全包络。
- `skills/manipulation.Plan` 始终作为确定性后备。

## 指标

本地接口 `GET /v1/orchestration/metrics` 提供任务总量、序列任务、计划来源、LLM 候选通过/拒绝、fallback、终态成功率和综合观察分。分数只用于迭代，不参与物理放行。

这些是 Planner/任务指标，不是自然语言开放域正确率。已知请求走确定性 Parser 与 Planner 是否采用 LLM 是两个决定；不要用 fallback 率推断用户要求是否被完整保留。最新[语言评测](development/natural-language-evaluation.md)固定测试解析、场景执行和拒绝行为，没有评测线上模型。

## 提升方法

1. 用 `AGENT_ORCHESTRATION_SAMPLES=3` 采样多个候选并选择通过校验的一致计划。
2. 在 `orchestration/llm_test.go` 增加 golden request 与期望计划。
3. 按任务中的 rejection 原因修正能力描述或 prompt，不放宽安全校验。
4. 保持 `make sim2real-check` 通过，并把实体失败转为 verify/recover capability。
5. 可以降低 fallback 率，但不删除 deterministic fallback。

迭代前先复现 `scripts/evaluate_natural_language.py` 的固定用例，把新口语、歧义、来源约束和场景不支持动作作为成对正反例。下一阶段缺口是场景预检、跨任务上下文和可验证的重新授权；RoboCasa 目前限定单回合单向交接，不能只修改 prompt/别称就声明支持通用往返搬运。

LLM 配置位于 Local 的“开发模式 → 开发诊断”或受限配置文件。它不生成低层 action_chunk；Fleet 的动作模型由[策略 sidecar](production/policy-tools.md)处理。新增能力按[开发原则](development/principles.md)与[Sim2Real 上手](sim2real/README.md)分别验证软件语义和真实物理行为。
