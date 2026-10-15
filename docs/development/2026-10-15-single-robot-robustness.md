# 单机器人长程鲁棒性与实验留存

基线：`0d36aa127ad2132857775241478ac7689478e783`。目标是检查一个边缘 Agent 在多代计划、连续目标、宿主重建与云边丢包后，是否保留已确认效果和原授权，并为后续论文保留可复核数据。实验设计见[协议](../experiments/2026-10-15-single-robot-robustness-protocol.md)，实际样本见[报告](../experiments/2026-10-15-single-robot-robustness.md)。

## 实施顺序与边界

先登记实验单位、故障条件和判据，再扩展软件长步骤链与实际 Gazebo 任务；首次失败独立保存；最小修复后回归；独立审查恢复权威；最后运行完整测试和导出证据。地图、运行中数据库和历史失败不清除，不通过修改版本、放松碰撞或重置已完成效果让实验通过。

软件任务使用生产 Host、Planner harness、Executor 与 SQLite，决策器和设备为脚本 fixture；实际 Gazebo 另用真实模型、ROS 2/Nav2/RTAB-Map、传感器和动力学。两者分别计数。共享宿主不要求每个逻辑角色调用一个模型，Ops/Recovery 是有明确权限的确定性组件。

## 修复与数据路径

| 问题 | 实现方式 | 主要入口 |
| --- | --- | --- |
| 安全检查点续跑后的未知写没有向云端报告 | 首次 claim 与重启路径共用失败处理，用原 claim 身份上报；上下文已取消也允许有界诊断上报，不授予动作权限 | `edge/worker/worker.go`、`objective_robustness_test.go` |
| 子动作完成但父确认写入丢失后无法恢复 | 同时要求本地 `StepCompleted` 与完整绑定的验证事件，重新检查实时父权限、机器人和目录，追加 `EDGE_STEP_RECOVERED`；缺证据、错身份和后续失败保持对账 | `internal/edgeagent/recovery.go`、`host.go`、`recovery_test.go` |
| 最终完成观测瞬态不可用导致任务失败 | 仅对 gRPC Unavailable/DeadlineExceeded 做完成条件只读恢复，全目标两次持久重试；不重规划或重发动作 | `completion.go`、`completion_test.go` |
| 恢复证据中的写分类损坏可能漏计效果额度 | 以通过目录哈希校验的 Manifest 核对 `mutatesWorld`，不能把损坏的写证据当只读完成 | `recovery.go`、`recovery_test.go` |
| 完成读取后耗尽标记未落盘可能重取额度 | 调用前持久预留完成读；中断窗口不能靠重建宿主多读，保留阻断信息等待复核 | `journal.go`、`completion.go`、`completion_test.go` |
| 普通“去厨房巡检”落入不必要的模型解析 | 完整匹配巡检/巡查后缀，保留命名返回；未识别的测温、开烤箱、关电仍拒绝 | `agent/intent/parser.go`、`inspection_suffix_test.go`、`agent/agent_test.go` |
| 真实多轮规划可能超过固定 HTTP 等待 | Harness 的 planning-timeout 只作用于创建，默认 120 秒、有界最高 900 秒；不延长运动期限或其他读取 | `scripts/evaluate_long_horizon.py`、`tests/install/test_long_horizon_eval.py` |
| 新 Host 合法复合子执行被误判成父目标重启未决 | Runner 已选定 legacy 路径时只查物理步骤账本；外部 CheckRecovery 保留父能力恢复分派；历史 STARTED 物理动作、未批准与权限撤销仍阻断 | `edge/agent/runner.go`、`composite_recovery_test.go` |

新增检查点字段使用默认零值兼容已有 v1 账本，不改变授权包络。未知动作仍不得自动重放；从账本确认已有完成事实不等于再次动作。

## 实验基础设施

`internal/edgeagent/campaign_test.go` 在同一机器人状态和 SQLite 上顺序执行 32、48、64、96、128 个能力动作的五个目标，保存自然语言、授权、各代上下文投影、检查点和逐调用时间线。每代最多 16 次能力调用，单目标仍最多 8 代/128 次能力调用。另测预算停止、长前缀后的未知效果、数据库关闭重开。

纯软件长步骤链为 CI/磁盘负载留 600 秒合同期限；这是仪器运行预算，不是机器人运动或推理延迟指标。早期 120 秒探索运行、误把完整 campaign 放入 120 秒测试进程超时的记录另存，不解释为现场故障。严格 1 秒期限和取消有独立回归，生产约束未放松。

```bash
# 只运行软件实验，不连接机器人；输出目录必须不存在
.venv/bin/python scripts/run_edge_robustness_campaign.py \
  --output artifacts/acceptance/edge-robustness-run-001
```

采集器保留 Go 原始 JSONL，按测试重组长 Output，再提取 task/step 记录。任务 CSV 同时记录业务结论与源测试结果，失败或未结束的源测试不能按任务字段单独计成功。预定测试/场景缺项、记录截断或中途改源码会将整个运行标成未通过。manifest 提供逐文件 SHA-256，run.json 保存命令、UTC 时间、环境和源码身份。提取测试覆盖分片交错、截断与拒绝覆盖。

完整 campaign 默认不启用 race；race 对故障与交接独立运行，避免混淆检测器与任务本身的开销。保留每条记录的 `durationScope`，不能混比不同计时范围。

## 运维与后续扩展

保留原数据库与 Runtime episode，按原 claim 续跑。待定动作先核对 operation、子步骤和证据，不能换 task ID 绕过。完成条件只读中断需复核时，保留原请求和预算，不能补造观测结果。

本轮不新增模型 SDK、机器人品牌或规划器依赖；异构差异仍通过 Manifest、验证合同和 ROS 2 适配器表达。超过单目标能力上限应由上层分成各有完成条件的目标。父事件全量序列化与证明补传在长历史下有明显开销；后续优化须单独验证缓存/出站 ACK 不破坏补传与回溯，当前软件耗时不能作为实机性能或 MTBF。
