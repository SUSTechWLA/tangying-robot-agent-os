# Tangying Robot AgentOS 生产交付手册

本目录描述当前代码已经实现并经过测试的系统，而不是未来设想。Tangying 的核心价值是把自然语言任务、分布式多机器人协调、工具执行、环境观测与 Harness Agent 验证放入同一条可审计链：命令返回成功不等于任务成功，只有权威世界状态满足后置条件才会完成任务。云端 Fleet 是重点部署形态；Local Brain 复用相同 Runtime、工具目录、观测契约与世界快照，用于无网络场景。

## 我是谁，我该读什么

| 角色 | 建议阅读顺序 | 读完应能完成 |
| --- | --- | --- |
| 项目负责人 | 本页 → [系统架构](architecture.md) → [测试与验收](testing-and-acceptance.md) | 判断能力边界、创新点、交付成熟度与剩余风险 |
| 开发者 | [快速上手](quickstart.md) → [数据契约](data-contracts.md) → [接口文档](api-reference.md) | 启动栈、开发 Agent/工具/观测、定位契约错误 |
| 云端运维 | [配置与安全](configuration-and-security.md) → [异常运维](operations-and-failures.md) | 部署 Fleet、轮换密钥、恢复数据库/队列/协调器 |
| 机器人技术员 | [仿真到实机](sim-to-real.md) → [快速上手](quickstart.md#机器人实机) | 接入 XLeRobot、标定、注册工具与观测源、执行实机验收 |
| 安全审核 | [系统架构](architecture.md#安全边界) → [异常运维](operations-and-failures.md) → [测试与验收](testing-and-acceptance.md) | 核对急停、fencing、幂等、证据真实性和失效关闭 |
| 系统集成 | [接口文档](api-reference.md) → [数据契约](data-contracts.md) → [仿真到实机](sim-to-real.md) | 对接 UI、Fleet、Runtime、感知与第三方 Agent |

## 文档地图

- [系统架构](architecture.md)：云端 Fleet、Local Brain、Edge、Runtime、WorldModel、Coordinator、Harness Agent、Web Console 的边界与数据流。
- [从零快速上手](quickstart.md)：服务器端、用户端、RoboCasa 仿真、Local Brain 与机器人实机。
- [全部接口](api-reference.md)：HTTP API、WebSocket、gRPC、鉴权、幂等和错误码。
- [数据契约](data-contracts.md)：TaskRevision、ToolActivity、ObservationEnvelope、WorldSnapshot、资源 custody 与兼容规则。
- [仿真到实机](sim-to-real.md)：工具注册、观测源、地图坐标系、标定、mTLS、安全验收与回滚。
- [学习型策略工具](policy-tools.md)：VLA、模仿学习、强化学习 sidecar，动作边界、恢复状态机和 sim2real 晋级。
- [配置与安全](configuration-and-security.md)：端口、环境变量、RBAC、证书、密钥轮换、备份与加固。
- [异常运维手册](operations-and-failures.md)：分布式故障的现象、检查、恢复、安全不变量和防止复发。
- [测试与验收](testing-and-acceptance.md)：测试矩阵、签名证据、发布检查和实机独立验收。
- [本次发布证据](release-evidence.md)：本次可交付基线的提交、测试计数、签名仿真证据、性能和已知边界。
- [部署与容量](deployment-and-capacity.md)：环境分层、最小生产拓扑、SLO、容量和滚动升级。

## 当前交付边界

已经验证：中文自然语言创建任务；第 1 版运行中更新为第 2 版；安全点切换；两台 XLeRobot 在 RoboCasa/MuJoCo 中完成红色方块交接；双 Edge/Runtime；VLA/模仿学习/强化学习通用策略契约；四个策略驱动的抓取/放置动作；世界状态与资源 fencing；Harness 证据；完整 WebGL 厨房与双机器人；视觉故障时 Canvas 降级；签名、可移植、离线重验证证据包。

仍需在客户现场独立完成：真实机械臂标定和安全认证、生产数据库 HA、跨地域灾备、真实网络长稳与容量压测、非限频显示器上的可见帧率验收。仿真通过不是物理安全认证。

开发演示环境可使用 `admin / admin123`；生产 `scripts/fleet-up.sh up` 会在 `deploy/cloud/.env` 生成随机密码，必须读取生成值且不得继续使用演示密码。
