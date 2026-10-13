# 第 8 章 分布式一致性、租约与故障恢复

**学习目标**：区分因果、持久化与物理效果；分析重复、丢响应和旧持有者；识别恢复窗口。

**先修知识**：第2、3、7章；事务和网络故障。

**关键术语**：happened-before、lease、fencing、CAS、Outbox、checkpoint

## 8.1 因果顺序与时间顺序

分布式事件不能仅靠墙钟或到达顺序重建因果。Lamport的happened-before关系用进程内顺序、发送/接收与传递性定义事件偏序；本书将它用于理解causation链，不声称项目实现了完整逻辑时钟算法。[原论文](https://lamport.azurewebsites.net/pubs/time-clocks.pdf)

任务账本序号能排序本聚合记录，不能证明跨设备时钟精度或实际采集质量。消息携带原因ID、设备和版本，是为了拒绝不匹配，不是仅增加字段数量。

## 8.2 租约、fencing与共识

租约在有限时间内授予权威；fence使受保护入口拒绝更旧纪元。租约失效不撤销已经发生的物理效果，也不证明设备停止。

中心单写租约与共识协议不同。Raft讨论复制日志、一致性和领导者协调；本项目Redis leader/resource租约不构成同等复制状态机或跨存储事务。[Raft论文](https://raft.github.io/raft.pdf)

当前生产leader owner使用唯一incarnation，避免进程重启沿用旧身份。共享存储失效、业务提交与租约校验窗口仍须单独测试。

## 8.3 事务与Outbox

Outbox把业务事实与待投递工作在同一存储事务中提交，再允许重复发送。它解决“状态已落盘但通知未发出”的部分故障，不把电机动作放进事务。

HTTP的幂等语义讨论相同请求重复的预期效果，不能自动赋予机器人命令物理幂等。应用还需命令身份、fingerprint、持久结果与未知策略。[RFC9110 §9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)

## 8.4 边缘checkpoint的恢复表

| 阶段 | 重启动作 | 未知边界 |
| --- | --- | --- |
| 无记录 | 校验后CAS提交ACCEPTED | 不落盘不派发 |
| ACCEPTED | 停止/对账 | 可能在实际派发后中断 |
| EXECUTED | 只重传原completion | 不能再执行物理图 |
| COMPLETED | 不动作，补ACK | 队列确认可重试 |

同机器人worker应共享执行库，CAS决定唯一接收者；不同DB不是共享锁，还需Runtime幂等/fence。领取租约过期的已执行任务不能自动分给另一机器人重做。

## 8.5 完成响应丢失与v2收尾

中心把精确receipt、成功状态、pendingCompletion和finalize outbox一起提交。边缘若丢响应且revision已变，可重发旧completion；中心核对不可变回执确认旧结果，不推进新图。

收尾用原grants/effects释放旧租约、投影共享归属、尊重更高fence和后来取消/安全停止，再关闭pending。未完成收尾不得认领新工作，也不调用Runtime。

## 8.6 未覆盖窗口

成功事务前外部Transfer已经发生但未持久记录、过期且从未投影的共享grant、数据库/世界读取失败和不理解pending的旧coordinator混跑，仍可能需要对账。部署须排除旧leader，当前无自动二进制版本联锁。

对这些窗口应画故障注入点、持久事实和允许动作，不能只写“支持断点续跑”。

### 中断推演：为何无法从超时推断效果

```text
本机持久 ACCEPTED → 派发 Runtime → 实体动作 → 收到/核验结果
                                               → 持久 EXECUTED
```

若进程在派发前或实体动作后中断，本机都可能只剩 ACCEPTED。仅凭该阶段无法区分两段历史，所以“没有 EXECUTED 就重试”不成立。更强设备协议可提供原 operation 查询、执行幂等与采集证据，但必须证明它们的范围；查询失败仍保留未知。

EXECUTED 确定结果和 completion 已落盘，把故障从物理执行转为结果交付。它支持重复上报既有结果，不授予新的物理效果。恢复设计的目标是缩小未知范围，并明确无法自动恢复的边界。

## 8.7 CAS 与 Outbox 的教学模型

设任务聚合版本为 v，两个消费者都读到 v。CAS 更新可用“仅当当前版本仍是 v，提交状态并增版本”表达；其中一个成功后，另一个重读/拒绝，不能覆盖前者。比较和提交必须由同一存储原子完成。

```text
事务：检查 expectedVersion → 写业务状态 → 写 outbox → 提交
投递器：读未完成 outbox → 发送 → 收到确认后标记
消费者：验证身份/版本 → 幂等消费 → 保存结果
```

投递器发送后、标记前中断会重复发送，因此消费者需要幂等。事务保护的是这一存储中的状态与待发记录；另一个数据库、共享资源服务和实体动作不能因为出现在同一图里就自动获得原子性。

设计评审时应为每个跨边界箭头画“效果前、效果后、回执前、回执后”的故障点。需要强一致的业务可使用适用共识/事务，物理效果仍需要命令查询、证据和对账。当前项目的 lease、checkpoint 和 v2 收尾分别缩小部分窗口，不宣称覆盖全部。

### 先持久化，再确认通知

Worker 的任务通知先写本机 SQLite inbox，再 ACK Redis；重启恢复原通知。取消和暂时失败不能因固定尝试次数耗尽而悄悄丢掉工作。这个顺序缩小“队列已确认、本机却没有记录”的丢失窗口；ACK 响应丢失仍可能重投，消费者去重后读取当前 claim。

消息可靠性与动作可靠性分开：通知可以重投，物理 ACCEPTED 但结果未知不能重做。设备数据 inbox/outbox 同样只保证其落盘与投递范围，不能将收到地图消息与修改本地地图包合成一个事务。

## 8.8 小结与阅读

协议实现见[Basis](../../core/contextcontract/basis.go)、[边缘checkpoint](../../edge/worker/context.go)、[completion](../../fleet/coordinator/completion.go)与[finalization](../../fleet/coordinator/finalization.go)。

## 8.9 分层习题

基础题 B：为什么队列ACK不等于物理成功？

工程题 E：分别在ACCEPTED、EXECUTED及中心成功后丢响应，列出可恢复事实和禁止动作。

研究题 R：设计跨存储Transfer故障矩阵，说明哪些结论需要共识/事务或对账来支撑。

作答应写明假设、判据与证据。评价要点见[附录 I](../appendix/I-exercise-guide.md)，实践路线见[附录 F](../appendix/F-learning-labs.md)。
