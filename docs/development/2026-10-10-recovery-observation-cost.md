# 2026-10-10：长程任务历史下的巡检成本与持久调查去重

## 实测问题

本轮 Gazebo 长程任务沿用 `artifacts/acceptance/long-horizon-20261010/deployment-001/agent-data/agent.db`。旧失败任务保持可见；没有用新数据库或隐藏失败任务消除负载。

现场只读审查时，6 个任务的计划 JSON 约 2.1 MB，但 214 个 `ops.recovery_executed` 事件的 payload 已合计 464,415,978 字节，单事件最大 4,793,140 字节。每个诊断回合持久化完整可追溯上下文；对同一长期失败每两分钟重复调查，导致历史源被反复保存。检查一份最新归档源未发现旧模型输入递归嵌套；本次主要问题是重复诊断和重复扫描。

生产调用链是 `Ops.Observe → abnormalTasks → taskHistory.Abnormal → Service.List → SQLite.List`。每五秒仅为了任务 ID 和状态，旧实现也会反序列化所有任务计划、模型输入和事件 payload。启动恢复还会完整读一次每个任务。独立现场 `ps` 曾采到 Local Agent 高 CPU 与约 1.8 GB RSS；这证明了资源竞争风险，不能据此断言它造成了某次 `RGBD_NOT_READY`。

## 改动

`tasks.SummaryReader` 为可选仓储接口。SQLite 直接读取任务状态、revision、aggregate version、更新时间及事件序号；不读取 `plan_json`、`intent_json`、`payload_json`。内存仓储只读元数据，不深拷贝历史。旧仓储仍可回退到完整读取。Ops 的任务索引、异常任务列表及 Recovery 的当前状态读取使用该接口。

索引同时提供两个水位：`EventSequence` 包含所有事件；`SourceSequence` 排除 `ops.*`、`agent.*` 观察者事件。后者只用于自动调查的代次，不能用于认定物理成功。`UpdatedAt` 会被诊断事件修改，因此不充当新故障代次。任务的完整 `Get`、事件及上下文查询保持原语义，未决动作和原始来源不从索引中推断。

自动 Recovery 订阅者在 `Recover`、`ReadFacts`、构造模型上下文之前，使用现有原子 `eventlog.Store` 持久预约一次调查 pass。预约、审计事件和 checkpoint 同事务提交：

- 精确来源：任务、原始 failure event ID、已声明的 revision / robot / step / command，加异常身份、完整本地恢复目录指纹、调查策略版本及 Recovery Agent 版本。重新生成 anomaly / plan ID 或进程重启不会获得新 pass。
- 无精确来源的长期状况：保留缺失 binding 为未知，另以任务当前 revision / state / 业务事件水位、稳定的 issue 字段和最近持久清除边界分代。年龄、时间戳、新传感器帧、失败任务总数不生成新调查。
- 清除后重现：Ops 发布端先规范本地身份、event ID、时间及 payload，再同步持久保存关闭边界，随后才发布 `ops.anomaly_cleared`。重复及旧边界不会新增代次。成功清除同时重置对应状况的报告冷却，短于 60 秒的重现也立即报告。精确旧命令的未知结果忽略此边界，不会被“状况消失”洗掉。
- 目录或策略升级：新的动作/工具/风险目录指纹或显式策略/Agent 版本允许一次新调查；人工明确请求仍经原有执行/审批入口，不受自动预约挡住。

本地 `Finding.InvestigationID` 让获准的新代次避开旧两分钟规划冷却，否则已经消费的预约可能被旧冷却吞掉。它仅由本地持久预约赋值，不从消息或模型导入，也不进入 `RecoveryBinding` 或执行权限。一次 pass 仍受 Supervisor 原有动作数和只读门禁约束。

预约失败不读取诊断上下文、不派发任何恢复动作。预约成功后，即使诊断失败或进程在执行前崩溃，也不自动再次执行同一 pass；这是一种有界、保守的策略，需要新来源/业务代次、明确版本升级或人工调查才能再试。没有完成事件的预约不被宣称为已诊断成功。重复抑制只限制自动调查，不清除异常显示，不提供未知物理动作重放权。

独立审查发现旧实现把清除代次写入放在 Recovery 订阅者：高优先级 reopened 可能越过低优先级 cleared，满队列也可能丢弃 clear。因此最终实现不依赖该有损订阅。宿主配置的 `PersistClear` 只接收 Ops 自己产生的规范清除事件；持久失败保留相同 ID/原始 `OccurredAt` 的 pending edge，Health 显示 `OPS_CLEAR_NOT_PERSISTED`，不发布伪 cleared、不清除冷却。下一轮先重试原清除事实，成功后再报告当前仍然活跃的故障。观察轮次串行处理；普通 Bus 优先级和容量策略保持不变。未能持久化即进程终止时，边界仍未知，不凭缺失记录推断已清除。

第二个积压边界是：旧 detected 在 t60 发出、clear 在 t61 持久化、真正重现在 t100；如果消费者到 t100 后才接收 t60，不能把它归入最新 clear 的新代次。适配后的内部 `Finding.ReportedAt` 仅来自原事件 envelope 的 `OccurredAt`，不取 payload、模型输出或消费时钟。无精确来源的 report 必须严格晚于最新持久 clear，否则不预约；缺失/相等时间也不能证明重现。该时间只作为先后门禁，不进入 episode 哈希，单纯更新时间不能生成新调查。精确命令来源仍不受 clear 门禁影响。

## 验证与边界

针对性回归覆盖：索引不解码无效或大型归档 JSON、原始 `Get` 保留完整来源/错误、业务水位排除观察者输出、旧仓储兼容、两进程并发只一个预约、SQLite 重启和执行前崩溃、完整 scope 分离、age/time 变化不重做、新业务事件、清除/重现、目录变化、磁盘预约失败无调用、诊断失败不重做及新代次不被规划冷却吞掉。任务当前状态改用索引时仍保留无法读取未决动作的 UNKNOWN 门禁。

真实 Bus 集成回归另覆盖：clear-low 与 reopen-high 在消费者积压时倒序送达、容量为 1 时 clear 被淘汰，两者仍获得且只获得一个新调查；清除持久失败期间问题重新出现，恢复持久化后保留旧事实原始时间并重新报告当前活跃告警；旧 t60 报告先消费时被拒绝，t100 实际重现仍获唯一新 pass，payload 声称的未来时间不能覆盖 envelope；不匹配的发布身份/协议拒绝。补测日志为 `recovery-closure-final-race.log` 和 `recovery-chronology-final-race.log`。

验证日志：`artifacts/acceptance/long-horizon-20261010/recovery-observation-cost-race.log` 及最终 `recovery-observation-cost-final-race.log`。同一实际数据库的只读采样单独保存在 `recovery-observation-cost-sample.json`。停机后的数据库为 513,355,776 字节：旧等价完整扫描解码 494,844,181 字节、1,098 个 JSON 值，单次耗时 2,267.9 ms；新索引仅返回 601 字节、解码 0 字节 payload，十次中位 0.360 ms（首次 9.15 ms）。这是 Python SQLite/JSON 查询成本，不等同于 Go 进程 CPU 的因果测量。

采样前明确确认原 Agent 已停止、数据库无 writer 及 WAL/SHM。环境中普通 `mode=ro` 首查询报 `unable to open database file`，因此静态采样使用 `immutable=1`，完成后立即关闭连接。该选项不用于运行中的数据库，也不用于生产仓储。

本次未修改 Console 的完整任务列表、任务 `Get`、完整上下文或历史证据。启动仍完整回放已有业务故障一次；新诊断仍需读取完整来源。现有 `AppendEvent` 会重写该任务旧事件，诊断工件还没有改为跨事件的持久内容寻址存储。后续可在保持精确原文、哈希和 guard 可回查的前提下，独立优化增量追加与大工件去重；不能将本次轻量索引描述为已完成全量 blob 存储重构。
