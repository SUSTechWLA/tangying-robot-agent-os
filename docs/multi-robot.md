# Multi-Robot AgentOS 协调设计

## 当前结论

多机器人协同已通过 Fleet 云端闭环落地：`codex/v0.1` 保留**本地优先、单机器人**
默认架构，同时提供 Fleet 云端部署画像执行多机器人协同任务（见
`docs/fleet-cloud.md`）。

已有基础：

- `RobotRuntime` 协议包含 `robot_id`
- `edge/runtime` 提供语义能力抽象
- `tasks` 提供本地任务与执行状态
- `middleware` 预留持久化、队列、锁等扩展点
- LLM 编排可生成多步 skill graph

已落地：

- 多机器人客户端注册与路由 → `edge/runtime.Router` + Fleet 设备注册（mTLS 证书 CN=robot id）
- 任务节点与 `robot_id` 绑定 → `parser` 的 `extractRobotID` + `SkillStep.RobotID`
- 跨机器人分布式协调 → `fleet/coordinator`（意图级任务图、按机器人扇出队列）
- 事件驱动刷新 → coordinator 在意图 complete 时刷新下一节点并重新投递队列（跨机器人交接）
- 跨机器人互斥与安全 fencing → 意图声明租约（超时自动回收）+ 每机器人串行执行
- 多机器人全局场景融合 → `fleet/fusion`（占用栅格 + 轨迹 + 实体，`GET /v1/maps/global`）

## 目标多机器人任务图

```text
User: "把A桌的杯子放到B桌，然后让2号机器人取走"
  ↓
LLM 编排
  TaskPlan:
    node-1: robot_1.observe_scene      -> {node-2, node-3}
    node-2: robot_1.manipulation.pick  -> {node-4}
    node-3: robot_2.navigate_to_B      -> {node-4}
    node-4: robot_2.manipulation.place -> {}
  ↓
Coordinator
  node-1 completed
    -> refresh node-2, node-3
  node-2 completed
    -> refresh node-4
  node-3 completed
    -> refresh node-4
```

该图的意图级实现即 `docs/fleet-cloud.md` 的演示任务（「让1号机器人把红色杯子放进右侧收纳盒，然后让2号机器人把蓝色瓶子放进左侧收纳盒」的双机器人事件驱动交接闭环）。

关键语义：

- 每个 node 可以指定 `executor`（robot id / capability）
- 每个 node 维护 `PENDING / READY / RUNNING / SUCCEEDED / FAILED / CANCELLED`
- 节点完成时触发事件，Coordinator 根据 `dependsOn` 反向刷新依赖
- 只有所有依赖完成才把下游节点置为 `READY`
- 同一机器人同一时刻只允许一个物理动作节点运行
- 跨机器人共享资源需要 fencing / lock，避免两个机器人同时操作同一物体

## 需要新增的模块

1. `edge/runtime.Router`
   - 多 `runtime.Client` 注册
   - 根据 `Command.RobotID` 路由到对应机器人
2. `core/taskgraph` 扩展
   - `SkillStep.Executor` / `RobotID`
   - 节点状态机支持动态 ready
3. `middleware` 事件总线
   - `NodeCompleted` 事件
   - 下游节点 refresh
4. `Coordinator`
   - 可替换 Local App 的单队列 worker
   - 支持并行跨机器人节点
   - 串行化同一机器人上的节点
5. 全局场景
   - 多机器人 telemetry 聚合
   - 前端地图显示多个 robot_id 和各自轨迹

## 论文创新点建议

- 以“分布式 AgentOS”作为论文创新点，不能只靠“有一个 LLM Agent”，必须强调：
  1. 自然语言 → 多机器人任务图
  2. 任务节点与机器人能力解耦
  3. 事件驱动的跨机器人节点刷新
  4. 安全 fencing 下的并行/串行混合执行
  5. Sim2Real 同一 Runtime 边界
- 当前项目已经有很好基础，但需要补充多机器人协调、全局状态、事件总线和验证实验。
- 建议先做一个最小可行多机器人仿真：两个 MuJoCo 机器人 + 一个 Coordinator，跑通“A 机器人放置，B 机器人取走”的任务图。
