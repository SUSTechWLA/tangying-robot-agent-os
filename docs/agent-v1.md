# V1 Agent 与 Sim2Real 契约

V1 的硬约束是：Agent 能力必须先通过 MuJoCo 闭环，再允许连接 XLeRobot。联网主形态是 Fleet + Edge + Runtime；Local Brain 是独立离线形态。默认 XLeRobot 路径为 ROS2-free direct backend，ROS 2 为可选兼容路径。

## Agent

`agent` 提供同一个解析接口的两种实现：

1. `deterministic`（默认）：处理中英文 `pick_and_place`、`fetch` 和有序复合请求。
2. `openai`：完整、已支持的表达仍优先使用确定性解析，保留用户指定的机器人、物体、起点和终点；其余表达才调用 OpenAI 兼容 `chat/completions` 函数工具接口。模型失败则返回原解析错误。

已识别的否定、条件、停止要求，以及部分理解但存在歧义的请求，会要求用户澄清，不交给模型忽略这些约束后继续执行。同一句请求支持“一号机器人”“红色积木”“移到交接点”等表达；后续步骤中的“它”绑定前一步物体，并以前一步终点作为起点。独立请求中的“它”没有跨轮记忆，需要补全物体名称。

修改已有任务时，“最后放到右侧蓝色垫子上”只更新最后一步目的地，并经过版本预览、确认和安全点流程；否定、额外未知动作、多个不明确物体不能被上下文回退丢弃。当前语法不支持任意条件工作流、多物体批量搬运或冰箱开关。支持范围和可复现实测见[自然语言评测](development/natural-language-evaluation.md)。

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

物理任务需要实体感知、动作策略和结果 verifier provider。缺少任意能力时返回明确错误，不会制造物理成功。部署和验收见[树莓派快捷部署](install/robot-pi-quick.md)与[生产就绪判定](production-readiness.md)。

当前本地 LLM 不生成低层 action_chunk；Fleet 策略链由 `edge/worker` 调用 `edge/policy`。没有已训练实机模型随仓库交付，购买设备后按[Sim2Real 上手](sim2real/README.md)完成集成、现场授权与验收。
