# v0.7.0 协作机制文档修订记录

日期：2026-10-11。代码与发布核对基线 `30df0602f3baaab6cb75644e9051b919248c53af`。本轮只修订文档，没有变更业务代码、运行配置或发布标签，也没有重新执行仿真/实机任务。

## 目标与完成项

新增[长程任务、多 Agent 通讯与异构协作](../architecture/long-horizon-collaboration.md)，说明新增系统功能与对应源码、两种部署形态、Agent 发布/订阅和持久账本、只读恢复因果链、上下文预算与作用域、异构 Profile/驱动接入、多机器人资源交接以及边缘检查点/完成回执。

根 README、文档索引、运行时/事件/Fleet/分布式/协议架构及实机集成指南增加导读并同步当前规则。生产架构更新统一能力入口现状；CHANGELOG 记录本轮文档变更。历史长程上下文报告仅添加后续版本修复提示，保留原验证、失败和当时限制。

## 源码核对与纠偏

| 问题 | 当前依据 | 文档处理 |
| --- | --- | --- |
| 发布口被描述成原始 bus | `agentruntime/publisher_policy.go` | 明确 name/version/permission 绑定与 topic 发布权限，区分实际 executor/verifier |
| 去重被描述成只比较 ID | `agentruntime/bus.go` 与交互协议 | 同内容重传去重，冲突身份/参数/fence 拒绝；实时投递不等于落盘 |
| 内存 Ledger/Beads 容易与任务持久历史混淆 | `tasks/context.go`、`tasks/context_memory.go` | 区分记忆接口与持久 TaskEvent/TaskRevision/ExecutionStore |
| Fleet 租约超时被描述成自动回收重做 | `fleet/coordinator/coordinator.go` | 已认领执行过期进入未知结果/对账，不能假定动作没发生 |
| 旧生产架构仍说建图走独立主入口 | `internal/capabilityagent/planner.go`、本版验收 | 更新为统一能力目标，保留旧入口与历史审计链接 |
| 异构接入容易被当成自动异构调度 | `tasks/capability.go`、`tasks/service.go`、`edge/worker/context.go` | 写明单 capability plan 的 robot/catalog 和 Task 单 Adapter 约束，指出 auto 的实际归一行为 |
| 历史上下文报告仍列 completion 跨 revision 丢响应的旧限制 | `fleet/coordinator/completion.go`、`finalization.go` | 用顶部后续修复说明关联 v2 协议，不改写历史验收 |

## 结论边界

指定 Gazebo 单机器人长程、多 Agent 只读恢复、独立 Fleet 双机交接与异构 Python/Go 契约测试分别有证据。本轮没有把它们组合成异构实体机群长程成功；没有声称 Ops/Recovery 都是独立模型，也没有把冷归档分页离线覆盖说成长程实测调用。任务原末尾 ready=false、操作员暂停及物理未知结果的停止规则继续明确记录。

通用异构匹配、跨不同 adapter 的节点契约、任意并行 DAG、自动换机与跨存储事务仍需扩展和验收；软件文档不授予实体机器人动作权限。

## 验证

- 文档/链接/Make 目标/接口与仓库版本合同：`.venv/bin/pytest -q tests/docs tests/test_repository.py`，35 项通过。
- 根 README 声明检查：`go test ./tests/docs`，通过。
- `git diff --check`，通过。
- 新增导读对照上述源文件和 2026-10-10 原始验收结论；无测试条件放宽，无原始失败事件修改。

本轮提交排除已有两份营销文稿的未提交改动。完整提交内容和后续 CI 以本轮 Git 提交与文档 PR 为依据，历史发布门禁不作为本轮新 CI 结果。
