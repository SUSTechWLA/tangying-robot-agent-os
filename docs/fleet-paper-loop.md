# 分布式 AgentOS 论文闭环（Fleet 双机器人协同验证）

本文记录「自然语言 → 多机器人任务图 → 事件驱动跨机器人刷新 → 双机器人
并行/串行执行 → 全局地图融合 → 闭环指标」的完整验证闭环，作为分布式
AgentOS 论文的实验证据基线。所有结论可用仓库内脚本复现。

## 1. 闭环定义

```text
用户一句话 (自然语言)
  │  intent parser: 序列切分 + 「N号机器人」绑定
  ▼
多机器人任务图 (intent 级节点, 每节点绑定 robot_id, 严格有序)
  │  approve -> 按机器人扇出到 Redis Stream / 队列
  ▼
robot-1 worker 认领 intent-0 (7 步: observe→resolve→plan_grasp→pick→
verify_grasp→place→verify_place)   ── 云端协调器 RUNNING (声明租约)
  │  complete
  ▼
事件驱动刷新: intent-1 -> READY, 任务 id 重新投递到 robot-2 队列
  ▼
robot-2 worker 认领 intent-1 (同 7 步链)  ── SUCCEEDED
  ▼
全任务 SUCCEEDED (任务状态机 READY→EXECUTING→VERIFYING→SUCCEEDED)
  │
  └─ 全程遥测: 世界系位姿 + 感知实体 + 本地占用栅格
        └─ 云端融合 -> 全局占用地图 + 多 robot_id 轨迹 + 实体
```

## 2. 复现命令

```bash
# 云端 (Docker)
./scripts/fleet-up.sh up

# 双 MuJoCo 机器人 + 双 edge-worker
./scripts/fleet-sim.sh start

# 演示任务 (完整闭环)
./scripts/fleet-sim.sh demo
# 期望输出:  task <id> final state: SUCCEEDED
#           fleet-sim: multi-robot closed loop SUCCEEDED

# 自动化证据 (无需 Docker)
.venv/bin/pytest tests/e2e/test_fleet_cloud.py -q
# 2 passed in ~28s
```

演示任务原文：

> 让1号机器人把红色杯子放进右侧收纳盒，然后让2号机器人把蓝色瓶子放进左侧收纳盒

## 3. 闭环证据（观测点）

| 阶段 | 证据 | 位置 |
| --- | --- | --- |
| NL→任务图 | `intent.sequence[].robotId == [robot-1, robot-2]` | `GET /v1/tasks/{id}` |
| 队列扇出 | 仅 robot-1 与 robot-2 队列收到任务 id | 协调器/Redis Stream |
| 跨机器人串行 | robot-2 在 intent-0 完成前 claim 返回 null | `POST .../intents/next` |
| 事件驱动刷新 | intent-0 complete 后 intent-1 变 READY 且重新入队 robot-2 | `GET /v1/tasks/{id}/intents` |
| 执行证据 | 14 个 `STEP_SUCCEEDED` (每台机器人 7 步) | task events |
| 物理结果 | red-cup 到 right-bin (≈(0.32,0.34))；blue-bottle 到 left-bin (≈(1.68,0.34)) | 遥测实体位姿 |
| 终态 | task SUCCEEDED；意图 [SUCCEEDED, SUCCEEDED]，claimed {robot-1, robot-2} | `GET /v1/tasks/{id}/intents` |
| 地图融合 | 全局栅格含两台机器人占用、2+ 实体、双轨迹；robot-2 世界位姿比 robot-1 偏移 +2m | `GET /v1/maps/global` |

## 4. 与论文创新点的对应

1. **自然语言 → 多机器人任务图**：确定性 parser 把一句话切成意图序列并
   绑定 `robot_id`（`agent/intent/parser.go` 的 `extractRobotID`）；
   `SkillStep.RobotID` 让任务图节点与执行者解耦（`core/taskgraph`）。
2. **任务节点与机器人能力解耦**：云端协调器只谈「意图节点 + robot_id +
   状态」，物理执行（grounding、7 步物料化、安全字段重造）全部在
   edge-worker；Robot Runtime 永远看不到命令来源（Brain isolation
   不变量，线协议无 `brain_id`/`source`）。
3. **事件驱动的跨机器人节点刷新**：`fleet/coordinator` 在意图 complete
   时把下一意图置 READY 并**重新投递队列**（跨机器人交接）；任务状态机
   沿合法路径前进（READY→…→EXECUTING→VERIFYING→SUCCEEDED）。
4. **租约与故障自愈**：意图声明租约（默认 2m）超时自动回收；设备租约
   由 mTLS Link 心跳续期（15s/5s）；worker 断线重连后继续拉取。
5. **Sim2Real 同一 Runtime 边界**：双仿真与真机共享 `RobotRuntime` gRPC
   契约；`--dev-insecure` 只允许在仿真画像使用，真机强制 mTLS。
6. **多机器人全局状态融合**：`fleet/fusion` 把每机器人本地占用栅格按
   世界系位姿合并为全局地图，附多 robot_id 轨迹与去重实体。

## 5. 安全与限制声明（论文必须如实描述）

- 占用栅格是**感知实体的简化光栅化**，不是 SLAM/激光建图；融合假设
  机器人位姿已知且世界系一致（由偏移场景保证）。
- 意图级串行执行：当前 parser 产出的序列严格有序（「先…然后…」语义）；
  同一机器人同一时刻只有一个物理动作（worker 单线程 + 云端声明互斥）。
- 云端协调器状态在内存中（单实例画像）；任务数据本身持久化在 MySQL。
- LLM 编排可以参与计划模板生成，但机器人分配与安全字段由确定性层
  重造（prompt 明确禁止 LLM 输出 robotId 与安全字段）。

## 6. 数据规模（论文表格素材）

两台机器人 × 每 2s 遥测：位姿 + 15 实体 + 15×15 占用栅格；全局地图
≈44×24 栅格 @0.1m；轨迹保留最近 600 点；任务事件按序审计。
