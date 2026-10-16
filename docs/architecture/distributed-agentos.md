# 分布式 AgentOS：实现范围与成熟度

2026-10-16 按实现提交 `821659df` 核对。联网形态为 Fleet + Worker，独立单机形态为 Local Agent；两种边缘入口共享 `internal/edgeagent.Host`、能力执行器与 Runtime 契约。新增目标通道由云端提交粗目标和授权包络，批准后边缘自主编排、诊断并受控重规划；原 `/v1/tasks` 冻结计划通道保留。详见[分层目标架构](hierarchical-edge-objectives.md)和[目标接入](../guides/delegated-edge-goals.md)。代码入口和依赖方向见[开发代码地图](../development/principles.md)，完整数据流见[系统架构](../production/architecture.md)。

v0.7.0 新增 Agent 注册发布权限、精确故障因果链、长历史预算与归档、云边执行 Basis 和 v2 完成收尾。新增功能与各证据层的完整说明见[长程任务、多 Agent 通讯与异构协作](long-horizon-collaboration.md)。异构 Profile 接入、按序双机交接及单机器人长程实测分别成立，不等于异构机群自动分工已经完成。

当前扩展性评价、各模块权威来源、通信边界和后续验收顺序见[模块化与扩展性](modular-extensibility.md)。本轮将云边共享值/存储端口下沉到 core，执行摘要校验放入 orchestration，SLAM 工作流改用明确的 Provider 合同。

## 两条边界

目标授权边界为 `core/objective`：批准 `robot.objective.v1` 后生成一个 `edge.goal` 父节点；共享 Host 在该包络内用 `capabilityagent.Planner` 生成子计划。`agent` 与 `orchestration` 保留意图解析和旧执行图职责，`capabilityagent.Executor` 与 Runner 承担能力和物理闭环，执行端边界是 `edge/runtime.Command`。Runtime 接收 task/command/robot、能力、参数、期限、lease、幂等、安全 profile 与审批等字段，不依据命令由本地还是云端创建来绕过安全校验。

```text
Fleet API / Task / Coordinator / WorldHub / Harness
                  ↓ mTLS Link + 设备任务数据面（批准目标、claim、证据）
              Edge Worker → 共享 Edge Host（Task / Ops / Recovery）
                                      ↓ 能力执行器与闭环 Runner
Local API + SQLite → 同一 Edge Host → Runtime / Safety / Journal / Adapter
```

这是部署替换边界，不是已经实现的云端/本地自动故障切换。对同一设备切换控制端前必须停止旧派发、确认当前命令与持物状态、重新建立身份和资源权限。

## 已实现的协同闭环

Tool Catalog 描述可用能力，Observation Registry 描述验证来源，两者独立注册。`fleet/coordinator` 根据有序意图、robot ID、目录版本、资源 lease/fencing 和世界 basis 分派工作。`core/harness` 只接受命令后新鲜、连续稳定且坐标一致的环境证据。

共享红方块场景已覆盖双 Edge/Runtime、任务更新、安全点、资源监护权、未知结果对账和多种故障边界；操作入口见[RoboCasa](../operations/robocasa-handoff.md)。签名历史证据与本轮验证须分别看[V1 状态](../production/v1-release-status.md)，不能从 UI 演示推断实机已完成。

## 持久化与单写范围

Local 使用 SQLite；Worker 也在边缘 SQLite 保存执行阶段、`edge-goal/<task>/<revision>` 检查点和独立的 `edge-child/<childId>` 追加式账本。Fleet 使用 MySQL Task/Revision/Event/Outbox、Redis 队列/租约等适配器。父授权不随子计划变化，模型只接收有预算的进度视图；完整历史参与重复写额度检查，不能靠压缩抹去未决动作。WorldHub 的 `FLEET_WORLD_SNAPSHOT_PATH` 可保存同机单主 checkpoint；Compose 默认使用持久卷。恢复保留世界与源序列身份，旧观测不会因此变成新证据，delta 历史需要客户端重同步。

本版另外用不可变完成回执、pending gate 和 finalize outbox 恢复成功事务之后的部分收尾。生产中心领导权使用共享 Redis 租约和唯一进程 incarnation；旧 coordinator 混跑仍须由部署排除。这解决单主进程重启的一部分状态恢复，不提供跨主机共识或跨存储事务。文件锁/损坏/保存失败应失败关闭；不能多开 writer、删除快照或降低 token 来“恢复服务”。详细行为见[部署与容量](../production/deployment-and-capacity.md)。

## 仍须独立验证的范围

- leader fencing 与所有业务提交的同存储原子校验；
- MySQL、Redis、世界投影与物理结果之间可恢复的资源转移 saga；
- 数据库/队列切主、网络重排和长期多进程故障验证；
- 实机传感器质量、坐标标定、匹配策略、实体急停、停止响应和受限任务验收；
- 目标终端可见帧率、现场容量、备份恢复和长期运维。

`docs/superpowers/specs/` 保留架构演进中的设计决策；已实现状态以当前代码、测试和发布记录为准。参考目录 `tangying-ai-operation-system/` 不参与当前机器人系统的业务运行时。
