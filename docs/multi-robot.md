# Multi-Robot AgentOS 协调设计

## 当前结论

当前 `codex/v0.1` 是**本地优先、单机器人**架构，尚不能直接执行多机器人协同任务。

已有基础：

- `RobotRuntime` 协议包含 `robot_id`
- `edge/runtime` 提供语义能力抽象
- `tasks` 提供本地任务与执行状态
- `middleware` 预留持久化、队列、锁等扩展点
- LLM 编排可生成多步 skill graph

缺失：

- 多机器人客户端注册与路由
- 任务节点与具体 `robot_id` 绑定
- 跨机器人任务依赖的分布式协调
- 节点完成后的动态刷新/发布-订阅
- 跨机器人互斥与安全 fencing
- 多机器人全局场景/状态融合

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
