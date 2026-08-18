# 当前系统架构

**状态：本地优先架构，2026-08-18 起生效。**

本页是当前实现的快速入口。完整决策、故障语义、数据模型和迁移边界以[经批准的设计规范](superpowers/specs/2026-08-18-local-first-runtime-design.md)为准；交付顺序和验收项记录在[实施计划](superpowers/plans/2026-08-18-local-first-runtime.md)。两份文件属于长期开发设计资产，不随旧运行态代码删除。

## 运行拓扑

```text
用户 / 浏览器
  -> Laptop Local Agent（一个 Go 进程，默认 127.0.0.1:8787）
       - Console + local HTTP API
       - agent：自然语言到受约束意图
       - orchestration：能力目录到任务计划
       - tasks：审批、状态、事件与指标
       - localapp：单机器人执行队列与恢复
       - localstore：SQLite 持久化
       - robotclient：语义接口到 gRPC 的唯一适配层
       -> OpenAI-compatible LLM API（可选）
       -> mTLS gRPC，连接由笔记本发起
            Raspberry Pi Robot Runtime（一个 Python 服务）
              - RuntimeInfo / Observe / ExecuteSkill / Cancel / EmergencyStop
              - Safety Supervisor、短租约看门狗、幂等和急停锁存
              - XLeRobot direct backend -> USB serial -> 控制板/舵机
```

业务状态的唯一权威是笔记本 SQLite。树莓派只保留有界安全日志，不保存自然语言、LLM 密钥、完整计划或用户历史。云端只可能作为用户选择的 LLM API，不承担任务运行态。

## 组件边界

- `cmd/local-agent` 是产品进程入口；`internal/localapp` 负责组合任务服务和单执行队列。
- `agent` 与 `orchestration` 可调用 LLM，但输出必须解析为领域类型并通过技能目录校验。
- `tasks` 在本地持久化任务、审批、状态和事件，不存在 claim、远程 lease renewal 或远程状态回写。
- `edge/runtime` 是 Go 侧语义协议；只有 `edge/robotclient` 依赖生成的 gRPC 类型。
- `robot/gateway` 在树莓派执行独立的确定性安全校验。ROS 2 是可选适配器，不是 Agent API。
- `console` 只能经任务/运行时应用服务操作机器人，不能绕过审批直接发动作。

## 安全路径

1. 本地任务服务要求物理任务审批。
2. 执行器刷新机器人能力并验证技能、实体落地和计划。
3. Local Agent 生成 command ID、幂等键、deadline、短执行 lease、approval ID 和 safety profile；模型不能设置这些字段。
4. 树莓派再次检查版本、身份、技能白名单、时限、lease、动作键和值域。
5. 驱动检查串口、标定和本地状态后才执行有界动作块。
6. 断线或笔记本休眠时，树莓派在短 lease 到期后停止；重连不会自动重放不确定动作。
7. 远程可以触发急停，但只有现场操作员可以解除锁存。

## 数据与网络

- Console/API：loopback HTTP，默认 `127.0.0.1:8787`。
- 笔记本到树莓派：TLS 1.3 双向认证 gRPC；仿真显式使用 `--dev-insecure`。
- 初次信任：SSH 人工确认主机指纹并部署证书；日常无需 SSH。
- 本地数据：一个 WAL 模式 SQLite 数据库，加操作系统凭据存储或权限为 `0600` 的配置后备。
- LLM API Key 不进入任务、prompt 日志、机器人或状态 API。

线协议见 [`proto/robot/v1/robot.proto`](../proto/robot/v1/robot.proto)，行为不变量见[协议说明](protocols.md)。历史云端安装说明保留在[已取代的云端文档](install/cloud.md)中，仅用于追溯。
