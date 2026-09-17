# V1 Agent 与 Sim2Real 契约

V1 的硬约束是：Agent 能力必须先通过 MuJoCo 闭环，再允许连接 XLeRobot。联网主形态是 Fleet + Edge + Runtime；Local Brain 是独立离线形态。默认 XLeRobot 路径为 ROS2-free direct backend，ROS 2 为可选兼容路径。

## Agent

`agent` 提供同一个解析接口的两种实现：

1. `deterministic`（默认）：处理中英文 `pick_and_place`、`fetch` 和有序复合请求。
2. `openai`：完整、已支持的表达仍优先使用确定性解析，保留用户指定的机器人、物体、起点和终点；其余表达才调用 OpenAI 兼容 `chat/completions` 函数工具接口。模型失败则返回原解析错误。

已识别的否定、条件、停止要求，以及部分理解但存在歧义的请求，会要求用户澄清，不交给模型忽略这些约束后继续执行。同一句请求支持“一号机器人”“红色积木”“移到交接点”等表达；后续步骤中的“它”绑定前一步物体，并以前一步终点作为起点。独立请求中的“它”没有跨轮记忆，需要补全物体名称。

修改已有任务时，“最后放到右侧蓝色垫子上”只更新最后一步目的地，并经过版本预览、确认和安全点流程；否定、额外未知动作、多个不明确物体不能被上下文回退丢弃。当前语法不支持任意条件工作流、多物体批量搬运或冰箱开关。支持范围和可复现实测见[自然语言评测](../development/natural-language-evaluation.md)。

在本地 Console 的“开发模式 → 开发诊断”或私有配置文件中设置：

```text
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://your-provider.example/v1
AGENT_API_KEY=...
AGENT_MODEL=your-model
```

解析器返回经过验证的 `manipulation.Intent`。`orchestration` 根据已安装能力目录生成任务计划；`tasks` 通过 `tasks.Repository` 持久化审批、状态和事件；`internal/localapp` 通过注入的有界 Queue 顺序执行任务并在重启后恢复。当前 composition root 选择 `middleware/sqlite` 和 `middleware/memory`，LLM 不接触适配器 SDK、gRPC 消息或任何安全字段。

## Robot Runtime 边界

`edge/runtime` 定义 Agent 可见的语义能力，`edge/robotclient` 是唯一的 Go gRPC 传输适配器。执行前会刷新能力快照；缺失或受阻能力一律失败关闭。机器人侧 backend 和 Safety 只使用语义 Runtime 模型，protobuf 由 service 边界映射。取消与急停是不同操作，急停在树莓派持久化锁存。

实体绑定必须唯一匹配物体和目的地；用户指定了起点时，还需唯一匹配起点，并从 Runtime 观测确认物体位于该起点（`inside:<id>` 或 `on:<id>`）。位置不符、仍被持有或关系未知时，不发出该步骤的抓取动作。自然语言解析正确并不等于当前场景可执行。

## 仿真优先

```bash
make test-go
.venv/bin/python -m pytest -q
./bin/robot-agent demo
```

MuJoCo 和 XLeRobot 实现同一个 Robot Runtime 合约，因此任务创建、审批、计划、命令安全字段和终态恢复可以在无硬件时验证。

## XLeRobot direct backend

```text
Local Agent --mTLS gRPC--> Robot Runtime
                               -> XLeRobotDirectBackend
                                  -> XLeRobotDriver (LeRobot)
```

物理任务需要实体感知、动作策略和结果 verifier provider。缺少任意能力时返回明确错误，不会制造物理成功。部署和验收见[树莓派快捷部署](../install/robot-pi-quick.md)与[生产就绪判定](../operations/production-readiness.md)。

当前本地 LLM 不生成低层 action_chunk；Fleet 策略链由 `edge/worker` 调用 `edge/policy`。没有已训练实机模型随仓库交付，购买设备后按[Sim2Real 上手](../sim2real/README.md)完成集成、现场授权与验收。

## 迁移说明：Agent 层升级为多 Agent 运行时

本次升级把 Agent 层改造成可扩展多 Agent 运行时，**默认行为不变**：交付的自然语言解析、执行链、闭环契约、失败分类、租约/fencing、任务回放、自动恢复引擎与遥测全部照旧。

### 什么变了，什么没变

| 内容 | 变化 |
| --- | --- |
| 任务执行路径（`localapp` → `Runner.RunControlled` → `closedloop`） | **未变**。闭环契约、证据要求、结果未知不重试，全部原样 |
| 任务状态机 | **未变**。只是多了一个观察者回调，且回调在状态提交之后、不持锁调用，返回错误不影响状态变更 |
| 任务回放 | **未变**，新增可选的 `agentEvents` 字段（`omitempty`），既有字段与消费者不受影响 |
| API / 导出签名 | **未增删**。`Runner.Run(ctx, task)`、`RunControlled`、所有既有方法签名不变 |
| 新增 | Agent 接口（`core/agentcontract`）、EventBus / Registry / Orchestrator / 权限门控 / OpsAgent（`agentruntime`）、控制台"系统观察与诊断"一节 |
| 新增 | `Runner.RunRequest(ctx, task, control)`：宿主已经持有 task 时的契约原生入口，与 `Run`/`RunControlled` 走同一条执行路径 |
| 新增 | `tasks.Service.ObserveEvents`：状态提交后的观察者回调。可选，不设置即回到接入前的行为 |
| 新增 | `edge/agent.Runner` 字段 `Tasks`、`Events`。两者都可为 nil，为 nil 时行为与接入前完全一致 |

### 为什么 TaskAgent 是"原生 Agent"而不是适配器

`edge/agent.Runner` 直接实现 `agentcontract.Agent`。执行逻辑没有被搬到新文件，也没有经过任何包装层：Agent 方法只是**进入同一条执行路径的第二个入口**。

`Execute` 委托给 `RunControlled`，所以闭环规则一条不少。`Run`（两参数）保持原签名，因为本地执行生命周期、暂停/恢复路径和十几个既有测试都依赖它——把接口迁移扩大成调用点重写不会买到任何东西。

### 回滚

`TANGYING_AGENTS=task` 只启用执行 Agent，等价于接入前的行为。详见 [Agent 运行时配置](../operations/agent-runtime-config.md#回滚)。

### 新的边界

- Agent 之间**不直接互相调用**，只通过事件。
- OpsAgent **不修改任务状态、不动机器人**：它不持有执行端口（结构层约束，有测试断言字段清单）。
- 任何会改变世界或任务状态的动作仍然必须经过既有权限系统；Agent 运行时的权限门控只回答"提出请求的 Agent 是不是那种可以做这件事的 Agent"，不重新推导安全。
- `ops.recovery_proposed` 是**请求**，第一版恒为 `advisory` + `requiresApproval`。

详见[多 Agent 运行时](multi-agent-runtime.md)；监督者的工作原理与边界见[Review Agent 运行原理](review-agent.md)。
