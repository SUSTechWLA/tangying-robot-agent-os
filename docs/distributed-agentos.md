# 分布式 AgentOS：实现范围与成熟度

当前联网主形态是云端 Fleet，离线形态是独立 Local Brain；两者共享语义 Runtime、工具、观测与世界契约。代码入口和依赖方向见[开发代码地图](development/principles.md)，完整数据流见[系统架构](production/architecture.md)。

## 两条边界

规划端边界由 `agent`（自然语言 → 意图）与 `orchestration`（校验后的可执行计划）承担，执行端边界是 `edge/runtime.Command`。Runtime 接收 task/command/robot、能力、参数、期限、lease、幂等、安全 profile 与审批等字段，不依据命令由本地还是云端创建来绕过安全校验。

```text
Fleet API / Task / Coordinator / WorldHub / Harness
                  ↓ mTLS Link + 设备任务数据面
              Edge Worker
                  ↓ mTLS RobotRuntime
              Runtime / Safety / Journal / Adapter

Local Brain + SQLite → 同一 RobotRuntime
```

这是部署替换边界，不是已经实现的云端/本地自动故障切换。对同一设备切换控制端前必须停止旧派发、确认当前命令与持物状态、重新建立身份和资源权限。

## 已实现的协同闭环

Tool Catalog 描述可用能力，Observation Registry 描述验证来源，两者独立注册。`fleet/coordinator` 根据有序意图、robot ID、目录版本、资源 lease/fencing 和世界 basis 分派工作。`core/harness` 只接受命令后新鲜、连续稳定且坐标一致的环境证据。

共享红方块场景已覆盖双 Edge/Runtime、任务更新、安全点、资源监护权、未知结果对账和多种故障边界；操作入口见[RoboCasa](robocasa-handoff.md)。签名历史证据与本轮验证须分别看[V1 状态](production/v1-release-status.md)，不能从 UI 演示推断实机已完成。

## 持久化与单写范围

Local 使用 SQLite；Fleet 使用 MySQL Task/Revision/Event/Outbox、Redis 队列/租约等适配器。WorldHub 的 `FLEET_WORLD_SNAPSHOT_PATH` 可保存同机单主 checkpoint；Compose 默认使用持久卷。恢复保留世界与源序列身份，旧观测不会因此变成新证据，delta 历史需要客户端重同步。

这解决单主进程重启的一部分状态恢复，不提供跨主机共识或跨存储事务。文件锁/损坏/保存失败应失败关闭；不能多开 writer、删除快照或降低 token 来“恢复服务”。详细行为见[部署与容量](production/deployment-and-capacity.md)。

## 仍须独立验证的范围

- leader fencing 与所有业务提交的同存储原子校验；
- MySQL、Redis、世界投影与物理结果之间可恢复的资源转移 saga；
- 数据库/队列切主、网络重排和长期多进程故障验证；
- 实机传感器质量、坐标标定、匹配策略、实体急停、停止响应和受限任务验收；
- 目标终端可见帧率、现场容量、备份恢复和长期运维。

`docs/superpowers/` 保留架构演进中的设计与计划；已实现状态以当前代码、测试和发布记录为准。参考目录 `tangying-ai-operation-system/` 不参与当前机器人系统的业务运行时。
