# 2026-11-02：任务回溯与故障定位验收

证据类型：**software-contract-fixture**。使用真实自动 session、任务创建、冻结计划、审批、能力 Executor、SQLite 和回溯代码；机器人服务与规划决策使用确定性测试接口。没有调用真实模型 API，没有执行 Gazebo 或实体机器人运动，不能估计物理成功率或 token 节省率。

## 实验设计与结果

自然语言原句为 **“检查标定，建图”**，每个场景独立创建任务，重复 3 次。故障由 provider 注入，判据在测试中固定，不把未完成任务改判为成功。

| 场景 | 次数 | 预期与实际任务状态 | 每次 RPC | 核心断言 |
| --- | ---: | --- | ---: | --- |
| normal | 3 | SUCCEEDED | 4 | 两个父命令，建图发起/查询/复验均可定位 |
| read-retry | 3 | SUCCEEDED | 5 | 首次只读失败保留；诊断一次后重试，独立 rpcId |
| bad-verification | 3 | RECOVERABLE_FAILURE | 4 | RPC 均返回成功，但复验数据不足，父任务不能成功 |
| lost-write-reply | 3 | RECOVERABLE_FAILURE | 2 | 模拟已接收动作但回复丢失；仅发起一次，不自动重放 |
| host-three-stations | 1 | 仅验收 Host 子账本 | 子计划 6 | 实际 Host 生成 1 个子计划、3 个命令，父声明与权威子账本精确关联 |

最终 **13 次软件验收均满足预期断言**。前 12 次任务共 45 次 RPC、24 个父能力命令，全部在 SQLite 关闭重开后导出内容逐字节相同。Host 样例另有 38 条父事件、30 条子事件、3 个子命令和 6 次子 RPC；其父 Task 的生命周期不由此测试推进，因此不能把该样例的父状态当作完整 Local 工作台任务终态。

结果未知和未导入的证据产生 `incomplete`，这是保留不确定性的正确行为。子计划没有独立任务版本表，导出不会伪造首版或传感器证据来消除提示。三次确定性重复是回归稳定性检查，不是三个独立物理样本。

## 原始证据与复现

- [final/manifest.json](final/manifest.json)：逐任务状态、事件/RPC 数、诊断次数、重开结果、文件 SHA-256、基线提交及工作树源码摘要。
- [final/test-events.jsonl](final/test-events.jsonl)：最终 race 实验的机器可读测试过程。
- `final/evidence/*.json`：13 份原始导出与场景说明，包含原句、session、版本、原始事件、RPC 和子计划。
- `evidence/*.json` 与 [campaign.log](campaign.log)：早期 12 次集成检查，尚未加入最终子计划/字段完善。保留为中间过程，不与 final 合并计算样本量；论文引用优先使用 final 的源码摘要和证据。
- `final/validation/`：最终相关包 race 与文档检查记录。

在仓库根目录运行，指定一个**新的**输出目录，避免覆盖旧实验：

```sh
TRACE_CAMPAIGN_OUTPUT=/tmp/trace-campaign-new-run \
  go test -race ./internal/capabilityagent ./internal/edgeagent \
  -run 'TestTraceNaturalLanguageCampaign|TestHostTraceLoadsAuthoritativeChildAtomicCalls' \
  -count=1 -json > /tmp/trace-campaign-new-run.jsonl
```

任务、session 与 RPC ID 随运行生成；跨运行不要求字节相同。要求逐字节相同的是**同一已完成记录在同一次实验的数据库重开前后**。哈希用于固定导出文件，不是证明 provider 或传感器从未撒谎的签名。

## 相关回归与剩余范围

相关 Go 包 race 覆盖任务、控制台、SQLite、session、能力执行与架构/文档。另完成 Agent runtime、Host、Local app 包回归；Host 全包 race 耗时 298.144 秒，一次更早的诊断中断不计入通过。Web 完整 583 项测试和构建通过。Python 文档/仓库 38 项检查通过。

旧事件不可追补；完整导出的内存/序列化成本随历史增长；child aggregate 仍有写放大；超过 4000 个传感器索引或 64 个子计划需要按覆盖提示继续取证。全局跨设备快照、驱动每个 ROS 消息、长期原图留存、完整实机效果仍不是本轮验收结论。实现与故障修复见[变更记录](../../development/2026-11-02-task-traceability.md)。
