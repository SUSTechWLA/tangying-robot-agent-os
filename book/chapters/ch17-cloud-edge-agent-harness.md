# 第 17 章 云边与机器人之间究竟传递什么

本章是全书的通信总图。读完应能为一条消息确定发送者、接收者、字段、持久化位置和 ACK 的含义，并解释为什么数据共享不会自动获得动作授权。

## 17.1 先分三条通道，再选传输技术

| 通道 | 主要内容 | 权威效果 |
| --- | --- | --- |
| 云边任务控制 | 目标合同、批准、claim、执行 Basis、完成回执 | 在合同与当前执行权内委托任务 |
| Agent 角色事件 | 原失败、诊断、恢复提案、进度 | 驱动协作；必须回查绑定与权限 |
| 机器人间数据 | 观测、地图依据、覆盖建议、经验引用 | 提供信息；不授予运动或覆盖本地权威 |

HTTP、gRPC、Redis Streams、ROS 2 都是承载方式。先问消息允许接收方改变什么，再选择传输；“用了可靠消息队列”并不能回答任务是否已完成。

## 17.2 云端给目标，边缘负责现场细化

云端提交 `robot.objective.v1`，批准目标与授权包络；Worker 领取后以当前 claim/Basis 进入共享 Host。Host 生成受限子计划，调用 Runtime，验证并留下完成证明。Local 入口也可运行同一 Host，只是没有云端领取这一步。

![图 17-1：云边控制与回执时序](../assets/17-cloud-edge.svg)

云端同事实现时最容易漏的是“完成上报丢响应”分支。若完成证明已经持久化，边缘应补传原证明；云端用不可变完成回执识别重复，不能要求机器人再跑一次才返回成功。

## 17.3 Basis 的字段为什么不能只留 taskId

[core/contextcontract/basis.go](../../core/contextcontract/basis.go) 的执行依据包含以下关键维度：

| 维度 | 典型字段 | 区分的冲突 |
| --- | --- | --- |
| 业务版本 | taskId、taskRevision、executionDigest | 原目标与修改后的目标 |
| 派发权 | claimVersion、resourceId、fencingToken | 当前 Worker 与旧持有者 |
| 动作身份 | stepId、commandId、intentIndex | 相同工具的不同调用 |
| 设备与能力 | robotId、adapter、catalogRevision | 错设备或能力语义变化 |
| 世界依据 | WorldBasis 内的 revision、来源等 | 用旧世界解释新动作 |

这些字段共同描述“这次动作为什么被允许”。只留 taskId，无法识别同任务的新 revision 或重复领取。序列号和 fence 还要保持精确整数，跨动态 JSON 边界时避免浮点舍入改变身份。

## 17.4 点对点数据的实际信封

[core/exchangecontract/contract.go](../../core/exchangecontract/contract.go) 的 Envelope 使用 `schemaVersion=agent.data.v1`，带 messageId、sender/recipient（agentId、robotId、domain）、stream、kind、sequence、createdAtMs、expiresAtMs、basis 与 payload。sequence 的 JSON 编码为字符串，以保留 uint64 精度。

空间 Basis 包括 worldId、frameId、transformRevision，可带 mapId/mapRevision。payload 描述具体业务，而信封负责身份、顺序与时效。发送请求的 requestId 用于稳定发布身份；重试同一请求但改变内容应成为冲突，不能默默覆盖。

![图 17-2：数据 ACK 与业务消费的区别](../assets/17-peer.svg)

发送端 Outbox 保留待投递数据，接收端 Inbox 持久接受后返回 accepted 或 duplicate。业务消费者随后按 feed 游标读取。**投递成功、业务采纳和物理完成是三个独立结果**，监控与论文指标必须分开。

下面是与实际 Envelope 字段一致的**教学消息**，不是本次抓取的运行日志。时间设为课堂时钟 T=1000000 毫秒，便于手算 60 秒 TTL；真实服务会按真实时钟拒绝这个过期示例。

```json
{
  "schemaVersion": "agent.data.v1",
  "messageId": "demo-message-42",
  "sender": {"agentId": "uav-edge", "robotId": "uav-1", "domain": "lab"},
  "recipient": {"agentId": "agv-edge", "robotId": "agv-1", "domain": "lab"},
  "stream": "coverage-M",
  "kind": "observation",
  "sequence": "42",
  "createdAtMs": 1000000,
  "expiresAtMs": 1060000,
  "basis": {
    "worldId": "warehouse", "frameId": "world", "transformRevision": "R4",
    "mapId": "M2", "mapRevision": "8"
  },
  "payload": {
    "schemaVersion": "agent.coverage.advisory.v1",
    "missionId": "M", "missionRevision": "3", "geometryId": "G7",
    "observedAtMs": 999500,
    "observedTargetIds": ["shelf-top-2"]
  }
}
```

假设 T=1001000、peer 已配置且目标属于当前集合：信封年龄是 1000 毫秒，观测年龄是 1500 毫秒，它们不是同一个数。信封未过期仍可能不满足消费者的 MaxAgeMS。例如 MaxAgeMS=1000 时，应拒绝采纳。现在只改 transformRevision，推演下一节的冲突。

## 17.5 一个跨机地图冲突的完整推演

UAV 报告目标 shelf-top-2 已观察，任务为 M/revision 3，geometry=G7，map=M2/revision 8，transform=R4。AGV 当前任务相同但使用 transform=R5。即使 payload 中坐标数值一样，也不能合并：变换版本变化可能改变实际区域。

当前 [edge/exchange/coverage.go](../../edge/exchange/coverage.go) 对 peer 身份、最新流序号、TTL、空间 Basis、mission revision、geometry、采集年龄与目标集合逐项检查。输出携带 rejected 原因，且 `advisoryOnly=true`、`grantsExecution=false`、`physicalCompletion=false`。如果持久快照被限额截断，返回空建议而不是假装读完全部数据。

代价是保守拒绝可能造成重复巡视。收益是不会为了节省路程把不兼容地图的数据当作完成依据。未来可增加经验证的坐标重映射，但必须保留变换来源和误差范围，不能简单改 revision 字符串。

## 17.6 多机器人合作还缺哪几项决定

通信让 A 知道 B 说了什么；合作还需要谁分配子目标、共享资源怎么占用、冲突如何裁决、最终联合目标怎样验证。工作台切换器只路由访问，peer 覆盖建议只传信息，二者都不是任务调度器。

当前存在按序交接和异构探索/覆盖的工程基础，但通用联合资源、任意设备动态编组、共同地图误差与无线网络故障下的实机群体长稳，仍需针对性验证。云端粗目标应给每台设备明确范围，边缘保持单机闭环并按协议上报。

## 17.7 对接时用三个故障检验协议

先丢一条通知：重新投递不能重复物理动作。再丢一条完成响应：原证明可以重传。最后让 peer 发布一条新 sequence 但旧地图依据的报告：接收方应拒绝业务采纳，不能为了“保持在线”回用更旧报告。

这些软件行为可从 [edge/exchange/coverage_test.go](../../edge/exchange/coverage_test.go)、`edge/tasksource/` 和 `edge/worker/` 测试阅读。真实无线网络的带宽、乱序、重连和证书生命周期仍需另外实验。

**本章交付**：为三条通道各画一条完整消息链，给每个 ACK 写一句准确承诺。详细字段与现有接线参见[通信、上下文与 Harness 专题](../../docs/architecture/edge-agent-communication-and-context.md)。

## 17.8 分层习题

基础题 B：解释Local、Fleet和Assist为何不是三套等价任务权威。

工程题 E：画出A放置到B认领的完整消息/资源/验证路径，标出丢响应点。

研究题 R：为跨Adapter合作定义节点合同和联合后置条件，比较按序与并行方案的验证成本。

评分与参考要点见[附录 I](../appendix/I-exercise-guide.md)。
