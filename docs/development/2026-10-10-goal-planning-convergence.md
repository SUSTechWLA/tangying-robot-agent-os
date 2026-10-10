# GOAL 规划收敛边界修复（2026-10-10）

## 实际触发与证据限制

同一句完整家庭长程请求在 `run-005` 创建任务时返回 HTTP 422：`planning exceeded 10 model decisions`。该次没有创建 Task，没有审批、物理派发或故障注入。原始请求、422 和验收失败报告保留在 `artifacts/acceptance/long-horizon-20261010/run-005/`。

已有成功规划轨迹可证明预算压力存在，但不能还原 run-005 的具体十轮选择：

| 实际运行 | 规划决策 | Provider 只读 | 无效模型回复 | 完整提案 |
| --- | ---: | ---: | ---: | ---: |
| run-003 | 9 | 6，含一次重复 `calibration.get` | 2 次空回复 | 第 9 轮 |
| run-004 | 4 | 3 | 0 | 第 4 轮 |
| run-005 | 10 轮后拒绝 | 原轨迹未保存，不能确定 | 不能确定 | 未形成可执行计划 |

`Planner.PlanGoal` 已在失败 bundle 中保留各轮 Context 和真实 `ModelRequestJSON`，但当时 `tasks.Service.Create` 收到 error 就丢弃 bundle；因此 run-005 的失败轮次不能事后补造。本修复只处理收敛边界，失败尝试的独立持久化由宿主审计接线处理。只读分析及输入/source SHA 在 `planning-diagnosis-005/planner-readonly-review.json`。

## 改动

`internal/capabilityagent/planner.go` 保留原有上限：10 次模型决策、6 次实际 Provider 只读调用、3 次提案。每轮加入 `planning:budget` scoped guard，记录 `goal-planning-budget.v1`、当前轮次、含本轮的剩余决策次数、剩余 Provider 调用/提案次数及当前可用阶段；同样在当前反馈中说明这些边界。这个 guard 是 harness 的预算事实，不是模型授权。

- 第 1–8 轮：按预算提供 Provider 声明的语义只读工具；第 6 次实际调用后撤下所有 Provider 查询工具。参数校验未通过不会实际调用，也不消耗 Provider 调用额度；失败的真实调用仍计数。
- 有归档时，第 1–8 轮仍允许 `context_read` 读取同一规划作用域的原始证据，即使六次 Provider 调用已耗尽。它不产生新观测、不增加物理权限。
- 第 9、10 轮只允许提交完整提案或 `cannot_proceed`。没有足够证据必须澄清；不能补造状态、忽略用户要求或继续查询。
- 模型反馈之外，planner 会重新检查当轮工具 allowlist。自定义 Decider 硬选已撤下的 Provider/归档工具，同样只记录拒绝，不触发调用。
- `LLMDecider` 仅对 `Role=planning` 移除 HTTP tool schema 中的 `finish`，并从响应 allowlist 移除它。其他执行、恢复、系统角色的结束语义保持原样。规划器仍拒绝自定义 Decider 的 `Done`。

不增加轮次、读取额度、提案额度或上下文阈值，不加入读取缓存/刷新工具。重复读取仍是新 Provider 请求并计入六次额度；不会把先前观测宣称为新鲜。模型也仍可能不收敛，届时安全拒绝，不用确定性计划替代模型的缺失提案。

## 验证

`internal/capabilityagent/planning_convergence_test.go` 覆盖：六次实际读后第 7/8 轮按原 SHA/分页边界回查，第 9 轮无效提案后第 10 轮修正；尚余查询额度时也不能越过第 9 轮门禁；已撤下 Provider、归档和物理工具均不能派发；澄清及自定义 `Done` 不产生计划；无效参数不消耗实际调用额度；三次提案上限不变。

`internal/actionloop/planning_controls_test.go` 使用真实本地 HTTP 模型端点验证规划请求只提供提案和 `cannot_proceed`，越权 `finish` 被拒绝，并逐字节核对实际请求与留存的 `ModelRequestJSON`、SHA、scope、用户 Context。既有执行角色工具测试和原 7,000 字节归档分页测试继续通过。

本轮目标测试日志为 `planning-diagnosis-005/convergence-stable-tests.log`，race 日志为 `planning-diagnosis-005/convergence-final-race.log`。这些属于离线回归；原 run-005 失败不因此改写成成功。后续真实新尝试的证据应单独记录。
