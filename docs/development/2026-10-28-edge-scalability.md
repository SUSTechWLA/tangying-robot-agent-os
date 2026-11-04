# 2026-10-28 · 持续运行、模型成本与规划经验升级

基线 `deb36504fee4f7281f1f35d0e434f41da168f075`；[实施计划](2026-10-28-edge-scalability-plan.md)、[系统评估](2026-10-28-system-upgrade-assessment.md)、[实验与来源](../experiments/2026-10-28-edge-scalability/README.md)。用户在执行中追加 token 管理和重复/相似任务复用要求，两项纳入同轮。未改动原有两份营销文稿。

## 1. 为什么优先做这些修改

边缘长任务已有 Host、批准包络、持久 journal、unknown 对账及 ROS 2 Harness。本轮发现的主要问题是持续运行后历史增长影响性能和可用性、缺少聚合模型成本的入口，以及重复规划每次从零开始。继续增加 Agent 角色不能解决这些问题，因此沿现有模块边界做局部升级。

| 问题 | 实现 | 保留的不变量 |
| --- | --- | --- |
| 超过 1,000 条历史消息使 CoverageView 返回空 | 可选 `LatestSnapshotJournal`，SQLite 按永久入站 cursor 选择当前流头；完整身份先过滤，再 limit | 最新无效、过期或已被 GC 清除不回退旧值；历史 feed 保留；建议不授予执行权 |
| 父任务每追加一条审计都读写完整历史 | 可选 `tasks.EventAppender` + `Service.RecordEvent`；SQLite 写锁内分配序号、严格 INSERT 单条事件 | 原事件不改写；不传入旧 Task，因此不能用旧状态恢复批准、revision 或取消 |
| 完成回传逐步骤重复读取同一 child | `reportGoal` 在单次调用内缓存 child 读取结果 | 下一次补传重新读持久库；每次动作前的权限检查仍读取当前权威 |
| 连续任务累积后 child 快照反复反序列化、重复写 checkpoint | 单次 Host.Run 私有写快照；派生 TOOL_ACTIVITY 只更新原子 state/event，关键事件仍写 checkpoint | 读取与恢复仍用当前数据库；CAS 冲突拒绝，缓存不暴露可变引用；不删历史 |
| 模型逐次记录存在，缺少运营汇总与可控响应额度 | GOAL/RECOVERY 阶段输出上限；独立 call ID、元数据 JSONL start/result 与只读汇总工具 | usage 缺失不补 0；截断候选拒绝；记录不成为动作/完成证据 |
| 重复目标重复询问模型 | 持久、有界 recipe；精确目标重新读条件并 freeze；相似目标提供一个有界参考 | 记录对象是验证过的草案；不复用批准、命令、旧观测和完成证明 |

## 2. 存储与性能实现

`AppendTaskEvent` 的第一次写操作只更新 `updated_at`，先取得 SQLite 写锁，再取下一序号并 INSERT。事务失败全部回滚；并发连接不能同时领取相同序号。保持事件观察者在提交之后、Service 锁之外运行，避免重入死锁。原 `AppendEvent` 返回完整 Task 的兼容接口保留；其他 repository 没有可选端口时使用原路径。

Host 与 child 的父审计出口使用增量端口，child 自己的持久 receipt 仍是执行依据。最终完成 proof 与原子 goal journal 未缩减。报告函数缓存只在当前栈帧生效，没有将批准状态、租约或机器人观测放入长寿命缓存。

完整 campaign 首轮 180 秒超时后，进一步优化 child writer：仅在一次串行 Host.Run 内保留最近成功提交的私有快照，下一次追加继续携带原版本做 CAS；冲突或写入失败立即丢弃写快照。`Get`、权限检查和重启恢复始终从数据库读取，输入 payload 深复制，写缓存不通过返回值暴露。私有冷加载保留 JSON 数值精度，避免重启后追加把历史整数重新舍入。派生 `TOOL_ACTIVITY` 的完整事件仍与当前 aggregate 原子提交，只省去同次的第二份 checkpoint；回执、验证、停止等边界仍写完整 checkpoint。checkpoint 可能低于 aggregate 版本，因此恢复不能把它当作当前状态。本次沿用既有 `LoadState` 恢复规则和 Task JSON，无 schema 迁移。

Coverage 的新端口无数据库 schema 变更。SQLite 当前视图在同一事务中读取 `head/prunedThrough/items`。真实候选流超过 limit 仍为空提案。旧 Journal 没有永久游标读接口时，Snapshot 截断或 `prunedThrough>0` 都拒绝形成建议，避免 GC 使旧正文复活。该设计解决历史消息数对视图的限制，不承诺无限流数、无限保留或自动多机地图融合。

## 3. 模型成本与经验边界

运行配置、字段、汇总命令、预算分母和消融建议见[模型成本与任务经验](../production/model-cost-and-recipes.md)。计量接入 Local GOAL/RECOVERY、Worker GOAL，以及 Fleet GOAL/SYSTEM 的 action-loop；旧 INTENT/PLANNING 独立客户端未纳入 JSONL 总账。GOAL/RECOVERY 支持新的 128–16,384 输出额度，默认沿用原全局 4,096。没有账户级总金额硬限额或精确通用输入 tokenizer。

recipe 存于原 SQLite 的有界 eventstore aggregate：每 robot/model namespace 最多 32 条，单条 64 KiB，24 小时有效。范围摘要包括机器人、模型/端点/模板/部署命名空间、目录、grants 和当前进度。`validated` 模式也必须至少有一条非空原规划查询，且每条结果都非空；重新取得完整语义字段，相等后重新 freeze 候选。新查询失败硬停；成功但状态变化才进入普通模型规划，其读预算不重置。`hint` 和相似目标始终由模型重新提交候选。默认 `off`。

独立复核修复了 Local 模型设置热加载仍沿用启动 recipeMode 的问题：先解析、验证新模式并构造整组替代对象，再应用；`validated→off` 真正关闭新规划的经验入口，非法模式保留原运行配置并回滚设置文件。Worker 的环境配置仍在进程启动时加载。

完整性的实际限制：重新查询只覆盖原模型已经查询的条件，无法形式化证明原模型查询了所有业务必要条件；因此复用仍依赖 capability 合同、审批与执行准入。保留草案不等于已学会成功完成物理任务；成功经验的统计与晋级应使用任务实际结局另外设计。本轮不会把一次规划校验当作技能成功样本。

## 4. 实际结果

下列数字均为 Apple M5 / macOS arm64 的进程内软件 fixture，没有新 Gazebo、ROS 2、实机或外部 LLM 测量。原日志、命令、源文件与二进制摘要见[证据目录](../experiments/2026-10-28-edge-scalability/README.md)。

- **协作视图**：1,005 次同流历史更新、SQLite 重开后仍只读取最新当前值；完整历史分页仍可回溯。最新坏值、过期、GC、身份重配、真实流超限和旧适配器负例通过。
- **32 步配对性能**：3 组交替运行、同输入无 race，初始 `Host.Run` 中位数由 **803.859 ms → 245.779 ms**，约 3.27 倍。每次仍有 32 次 fixture 物理调用，重放新增动作/计划均为 0。该指标不含完成报告重放，不是机器人运动速度。
- **较长任务**：分别选择 96 步与 128 步 race 场景通过，初始 Host 耗时约 25.08 s、33.61 s；重放新增动作/计划均为 0。选定场景不能直接与旧完整 campaign 的 10 分钟超时相除形成加速倍数。
- **增量追加 microbenchmark**：固定 512 条 1 KiB 历史时，单次完整路径约 6.07 ms / 3.17 MB 分配，追加路径约 0.106 ms / 2.3 KB；不使用时间阈值断言，也不推导整机吞吐承诺。
- **重复规划**：HTTP fixture 从首次 2 次 GOAL 请求变为精确复用 0 次，当前 Provider 查询仍为 1 次，物理写 0；真实模型 token/费用节省未测。状态改变、scope 变更、空条件、存储错误、拒绝、过期和六读预算均覆盖。

最终整合检查及未运行项以实验报告为准。开发中出现的测试夹具工具 alias 错误、并行编辑中间态编译失败保留说明；它们不计作成功执行或实体故障。

以上配对性能采于父审计优化后、私有 child writer 优化前。后者的首轮完整 campaign 在同一 180 秒上限内通过，整包 168.415 秒，连续五个任务合计 368 次确认的 fixture 动作；未知结果和预算停止另列。余量较小，现有授权读取和 child 聚合序列化仍随历史增长，不能宣称任意长任务已具备线性开销。完整时间和精确代码身份见[实验记录](../experiments/2026-10-28-edge-scalability/README.md)。

## 5. 升级与回退

没有改变 peer wire schema、ROS 2 合同、目标批准 API 或现有 Task 格式。ModelCall 新增可选字段；recipe aggregate 为可丢弃缓存，旧版本不读取它。升级时仍一致备份任务库、Runtime journal、地图/标定和配置；不能用旧数据库覆盖新版本已发生的动作。

默认不启用新 recipe 和独立用量账本；按指南选择 `hint` 或 `validated`、输出上限和私有计量路径。错误配置在入口检查中拒绝，配置的计量写入失败会阻止消费模型候选，需纳入磁盘监控。回退可先关闭 recipe/计量并取消阶段新配置，保留真实任务状态；完整旧程序回退应在停止/对账后进行。

源码学习入口：`core/exchangecontract/feed.go` → `middleware/sqlite/exchange_feed.go`；`tasks/store.go` → `middleware/sqlite/tasks.go` → `internal/edgeagent/journal.go`；`internal/actionloop/llmdecider.go` / `usage_log.go`；`internal/capabilityagent/recipe_memory.go`。README、通信专题、运维指南和[教材补充](../../book/updates/2026-10-28-cost-and-long-horizon.md)同步更新；已出版 3.2.0 正文/快照不覆盖。
