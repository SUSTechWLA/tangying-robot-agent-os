# V1 Agent 与 Sim2Real 契约

V1 的硬约束是：Agent 能力必须先通过 MuJoCo 闭环，再允许连接 XLeRobot。默认产品是笔记本 Local Agent 加树莓派直连 Robot Runtime；ROS 2 仅为可选兼容路径。

## Agent

`agent` 提供同一个解析接口的两种实现：

1. `deterministic`（默认）：处理中英文 `pick_and_place`、`fetch` 和有序复合请求。
2. `openai`：调用 OpenAI 兼容 `chat/completions` 函数工具接口；无效或不可用时回退确定性解析。

在本地 Console 或配置文件中设置：

```text
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://your-provider.example/v1
AGENT_API_KEY=...
AGENT_MODEL=your-model
```

解析器返回经过验证的 `manipulation.Intent`。`orchestration` 根据已安装能力目录生成任务计划；`tasks` 在 SQLite 中持久化审批、状态和事件；`internal/localapp` 顺序执行任务并在重启后恢复。LLM 不接触 gRPC 消息或任何安全字段。

## Robot Runtime 边界

`edge/runtime` 定义 Agent 可见的语义能力，`edge/robotclient` 是唯一的 gRPC 传输适配器。执行前会刷新能力快照；缺失或受阻能力一律失败关闭。取消与急停是不同操作，急停在树莓派持久化锁存。

## 仿真优先

```bash
go test ./...
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
