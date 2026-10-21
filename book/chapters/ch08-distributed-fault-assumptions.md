# 第 8 章 分布式故障：超时、重复与恢复窗口

本章的核心问题是：网络只能告诉我们“没收到回复”时，如何避免把一个物理动作执行两次？读完应能列举崩溃窗口，并为每个窗口指定恢复依据。

## 8.1 先推演两个看起来相同的超时

场景一：请求没有送到 Runtime，客户端超时。场景二：机器人已到 B，回包丢失，客户端超时。客户端看到相同错误，但第一次可能需要执行，第二次重做可能浪费路线、破坏物品或扩大风险。

仅靠重试次数和指数退避不能区分两者。它们解决发送节奏，不解决效果身份。系统需要稳定 commandId、设备侧 journal、执行检查点和命令后证据，才能把“不知道”缩小到确定的窗口。

## 8.2 幂等、租约和 fence 分别防什么

| 机制 | 防止的问题 | 不能代替的机制 |
| --- | --- | --- |
| 幂等键 / commandId | 同一请求重复到达造成重复处理 | 不证明设备效果已发生或已停止 |
| claim / lease | 两个 Worker 同时获得当前任务权 | 旧 Worker 在网络分区中可能仍存活 |
| fencing token | 资源拒绝旧持有者的写 | 仅在接收端实际检查时有效 |
| CAS 聚合版本 | 并发写覆盖新状态 | 不把设备动作纳入数据库事务 |
| Outbox / Inbox | 落库与消息投递之间丢通知 | 不提供物理动作 exactly-once |

例如新 Worker 使用 fence=12，旧 Worker 仍带 11；真正接收命令的边界必须拒绝 11。只在云端页面显示新持有者，对设备没有约束作用。当前租约和资源机制也不等同于 Raft 共识系统。

## 8.3 四个持久化位置决定重启行为

![图 8-1：检查点和恢复分支](../assets/08-checkpoint.svg)

| 崩溃位置 | 现有证据 | 正确恢复方向 |
| --- | --- | --- |
| 持久接单之前 | 没有本次检查点 | 在当前权限仍有效时判断是否首次执行 |
| 接单后、结果确定前 | ACCEPTED 或 pending | 可能有物理效果；调查而非盲重做 |
| 子执行已验证、父记录未推进 | 精确绑定的完整子账本 | 重新核对权限后只补父记账 |
| 完成证明已落盘、上报或响应丢失 | EXECUTED 与原 completion | 补传原报告，不重做动作 |
| 完成确认已持久化 | COMPLETED | 返回已知结果 |

[edge/worker/context.go](../../edge/worker/context.go) 处理 Worker 检查点，[internal/edgeagent/recovery.go](../../internal/edgeagent/recovery.go) 处理目标内子账本窗口。表中相似原则不能使两种状态结构互换。

## 8.4 为什么先写 pending，再调用设备

考虑伪代码：

```text
提交：本步骤 pending=true，预留预算，绑定 command
调用：执行器 → Runtime → 验证
提交：保存精确结果，pending=false，推进索引
```

如果把第一次提交移到调用之后，在设备执行与落盘之间崩溃，重启会误以为动作尚未开始。先落盘虽然不能消除未知窗口，却能把它标出来。进一步用子账本的完整绑定结果缩小未知范围，而不是假装窗口不存在。

当前 [internal/edgeagent/journal.go](../../internal/edgeagent/journal.go) 使用 ExpectedVersion，将聚合状态、事件和检查点提交到同一 eventstore 事务。Runtime 的物理世界仍在事务外；外部资源 Transfer 与云端成功事务之间也存在需对账窗口，不能因此宣称端到端 exactly-once。

## 8.5 完成响应丢失后，为何不能套用新 revision

任务 revision 1 已完成并上报，但响应丢失；期间用户创建 revision 2。Worker 重传时应拿 revision 1 的原执行摘要和完成证明对照不可变回执。若拿当前 revision 2 去解释旧结果，可能把未执行的新目标标成成功。

[core/contextcontract/basis.go](../../core/contextcontract/basis.go) 通过 taskRevision、claimVersion、stepId、commandId、robotId、executionDigest 等字段表达执行依据；[fleet/coordinator/completion.go](../../fleet/coordinator/completion.go) 与 `finalization.go` 负责相应完成和收尾。digest 用于验证字段组合，仍不能代替设备真实性与通信身份。

## 8.6 用最小反例学习恢复

阅读并运行以下软件测试，不会驱动机器人：

```bash
go test ./internal/edgeagent -run 'TestDelegatedVerifiedCrash|TestDelegatedExpiredUnknown' -count=1
```

先预测三个结果：完整匹配证据、缺失 StepCompleted、相同工具但错误 binding。再看测试如何要求后两者保持拒绝。若只记得“有检查点就可以恢复”，你会在最危险的分支作错判断。

**设计取舍**：越精确的日志通常能提高自动恢复范围，但会增加写放大、存储压力和对账成本。优化方向是保留决定执行权与效果身份的最小充分记录，而不是删掉所有中间状态追求速度。

## 8.7 分层习题

基础题 B：为什么队列ACK不等于物理成功？

工程题 E：分别在ACCEPTED、EXECUTED及中心成功后丢响应，列出可恢复事实和禁止动作。

研究题 R：设计跨存储Transfer故障矩阵，说明哪些结论需要共识/事务或对账来支撑。

作答应写明假设、判据与证据。评价要点见[附录 I](../appendix/I-exercise-guide.md)，实践路线见[附录 F](../appendix/F-learning-labs.md)。
