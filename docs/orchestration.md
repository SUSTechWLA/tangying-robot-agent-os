# LLM 编排能力、指标与提升方法

## 编排边界

LLM 只负责**选择和排序 Robot Capability**，不负责安全控制：

```text
用户自然语言
  → intent parser（LLM 可选，deterministic 兜底）
  → cloud/orchestration.Planner（LLM 可选，deterministic 兜底）
  → taskgraph.TaskPlan（能力技能图模板）
  → Local Agent 落地实体 ID，并重新生成 approval/deadline/lease/idempotency
  → Guard + Robot Runtime capability snapshot
  → Robot Gateway Safety Supervisor
```

LLM 输出的计划中不允许携带 safety 字段；Local Agent 会覆盖这些字段，因此模型无法绕过安全层。

## 可扩展性

`cloud/orchestration.New(catalog, config)` 的 prompt、允许技能集合和校验规则均由 `[]skills.SkillManifest` 自动生成：

- 增加新技能 = 在对应 domain 的 `Catalog()` 中追加一个 `SkillManifest`；
- 新技能自动进入 LLM 可见技能列表；
- `RequiredParameters` 自动进入 prompt 和 Guard 校验；
- `SideEffect` 自动决定计划是否包含有效动作；
- `SafetyLevel` 自动决定本地安全包络。

没有硬编码的 tool schema 或七步抓取流程。`skills/manipulation.Plan` 只是 deterministic fallback。

## 编排指标

接口：

```http
GET /v1/orchestration/metrics
```

字段：

- `totalTasks`
- `sequenceTasks`：一句话多任务占比
- `deterministicTasks`
- `llmGeneratedTasks`
- `consensusTasks`：self-consistency 计划数
- `llmFallbackTasks`：LLM 尝试后回退 deterministic 的任务数
- `llmPlanRate`：LLM 实际接管计划的比例
- `llmCandidateRate`：LLM 采样中通过校验的比例
- `llmRejectionCount`：被拒绝候选数
- `succeededTasks` / `failedTasks` / `safetyStoppedTasks`
- `endToEndSuccessRate`
- `successByPlanSource`
- `orchestrationScore`：综合分数

当前综合分公式：

```text
orchestrationScore =
  60 * endToEndSuccessRate
+ 25 * llmCandidateRate
+ 15 * llmPlanRate
```

分数只用于观察和迭代，不作为运行时放行条件。

## 提升编排能力的方法

1. **Self-consistency**
   ```bash
   AGENT_ORCHESTRATION_SAMPLES=3
   ```
   对同一任务采样 3 个计划，选出现次数最多的通过校验计划。多数一致时 `source=llm_consensus`，无有效计划自动回退 deterministic。

2. **Golden set 回归**
   `cloud/orchestration/llm_test.go` 是编排回归测试。新增任务类型时先加入 golden request + 期望 plan，确保模型改 prompt 或 catalog 后仍然通过。

3. **查看 rejection 原因**
   每个任务的 `plan.rejections` 记录了模型输出为何被拒绝。按原因分布修正 prompt/catalog 描述或补 few-shot。

4. **提高端到端成功率**
   - 保持仿真对象矩阵 100%：`make sim2real-check`；
   - 在实体实验 runbook 中记录每类失败；
   - 将真实失败原因沉淀为新的 verify/recover capability。

5. **降低 fallback 率但绝不取消 fallback**
   只有当 `llmPlanRate` 与 `llmCandidateRate` 稳定接近 1 时才考虑扩大 LLM 场景；deterministic planner 永远保留。

6. **扩展技能目录**
   新 domain 只需提供 `Catalog()`，不需要修改 planner、prompt 或指标代码。
