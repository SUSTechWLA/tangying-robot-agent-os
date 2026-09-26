# 躺营 · Tangying Robot AgentOS 部署与运维手册

当前代码的验证边界以 [2026-09-26 生产就绪审计与实机交接](field-readiness-2026-09-26.md)和 [V1 当前状态](v1-release-status.md) 为准；[2026-09-05 评估](v1-assessment-2026-09-05.md)保留为历史审计快照。历史发布证据不自动覆盖后续代码改动；离线前置检查通过不等于实机生产就绪。

原有单机及仿真内容记录其对应版本；2026-09-25 云边升级的当前实现、部署和证据分别见[角色 Harness 规范](../superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md)、[Orin NX 安装](../install/edge-orin.md)、[软件验收](agent-harness-docker-acceptance.md)。新开发应从[开发者上手](../development/getting-started.md)进入，再按[源码与文档对应关系](../development/principles.md#文档与变更一起更新)定位模块。

本目录说明当前实现、操作方式与生产前置条件；其中 HA、细粒度 RBAC、容量和现场验收要求不等于已经交付。Tangying 的核心价值是把自然语言任务、分布式多机器人协调、工具执行、环境观测与 Harness Agent 验证放入同一条可审计链：命令返回成功不等于任务成功，只有权威世界状态满足后置条件才会完成任务。当前提供云端 Fleet 系统任务 Agent、Orin NX 单机 Agent 与云端派单的 Edge Worker；三者复用 Agent 镜像，按角色限制工具和模型路由。云端与单机的任务权威不同，不能同时控制同一 Runtime。

单机器人首版从[用户流程与上线准备](single-robot-v1.md)开始；开发与真实相机接入见[RGB-D 闭环](../development/single-robot-loop.md)和[ROS 2](../development/ros2-rgbd.md)。

## 我是谁，我该读什么

| 角色 | 建议阅读顺序 | 读完应能完成 |
| --- | --- | --- |
| 项目负责人 | 本页 → [系统架构](architecture.md) → [测试与验收](testing-and-acceptance.md) | 判断能力边界、创新点、交付成熟度与剩余风险 |
| 开发者 | [开发者上手](../development/getting-started.md) → [开发原则](../development/principles.md) → [数据契约](data-contracts.md) → [接口文档](api-reference.md) | 启动栈、开发 Agent/工具/观测、定位契约错误 |
| 云端运维 | [配置与安全](configuration-and-security.md) → [异常运维](operations-and-failures.md) | 部署 Fleet、轮换密钥、恢复数据库/队列/协调器 |
| 机器人技术员 | [购机后上手](../sim2real/README.md) → [仿真到实机](sim-to-real.md) → [快速上手](quickstart.md#7-机器人实机) | 接入 XLeRobot、标定、注册工具与观测源、执行实机验收 |
| 安全审核 | [系统架构](architecture.md#6-安全边界) → [异常运维](operations-and-failures.md) → [测试与验收](testing-and-acceptance.md) | 核对急停、fencing、幂等、证据真实性和失效关闭 |
| 系统集成 | [接口文档](api-reference.md) → [数据契约](data-contracts.md) → [仿真到实机](sim-to-real.md) | 对接 UI、Fleet、Runtime、感知与第三方 Agent |

## 文档地图

- [系统架构](architecture.md)：云端 Fleet、Local Brain、Edge、Runtime、WorldModel、Coordinator、Harness Agent、Web Console 的边界与数据流。
- [云边软件验收](cloud-edge-upgrade-acceptance.md)与[角色 Harness / Docker 验收](agent-harness-docker-acceptance.md)：已通过的回归与仍待 Orin、GPU、真机和机群容量认证的项目；原始升级规范保留在 `docs/superpowers/specs/`。
- [2026-09-26 生产就绪审计与实机交接](field-readiness-2026-09-26.md)：当前放行判断、软件加固、无运动预检、实机及目标规模的逐项证据模板。
- [从零快速上手](quickstart.md)：服务器端、用户端、RoboCasa 仿真、Local Brain 与机器人实机。
- [全部接口](api-reference.md)：HTTP API、WebSocket、gRPC、鉴权、幂等和错误码。
- [数据契约](data-contracts.md)：TaskRevision、ToolActivity、ObservationEnvelope、WorldSnapshot、资源 custody 与兼容规则。
- [仿真到实机](sim-to-real.md)：工具注册、观测源、地图坐标系、标定、mTLS、安全验收与回滚。
- [学习型策略工具](policy-tools.md)：VLA、模仿学习、强化学习 sidecar，动作边界、恢复状态机和 sim2real 晋级。
- [配置与安全](configuration-and-security.md)：端口、环境变量、RBAC、证书、密钥轮换、备份与加固。
- [异常运维手册](operations-and-failures.md)：分布式故障的现象、检查、恢复、安全不变量和防止复发。
- [测试与验收](testing-and-acceptance.md)：测试矩阵、签名证据、发布检查和实机独立验收。
- [V1 当前状态](v1-release-status.md)：当前验证结果、范围与剩余条件。
- [历史 rc.2 证据](release-evidence.md)：2026-08-24 的测试、签名仿真包与性能，不自动覆盖当前工作区。
- [部署与容量](deployment-and-capacity.md)：环境分层、最小生产拓扑、SLO、容量和滚动升级。

## 当前交付边界

不同版本已有仿真证据覆盖（精确当前通过项见状态页）：中文自然语言创建任务；第 1 版运行中更新为第 2 版；安全点切换；两台 XLeRobot 在 RoboCasa/MuJoCo 中完成红色方块交接；双 Edge/Runtime；VLA/模仿学习/强化学习通用策略契约；四个策略驱动的抓取/放置动作；世界状态与资源 fencing；Harness 证据；完整 WebGL 厨房与双机器人；视觉故障时 Canvas 降级；签名、可移植、离线重验证证据包。

购机用户先按[Sim2Real 上手](../sim2real/README.md)准备版本化配置和证据；新增开发者先按[快速上手](../development/getting-started.md)运行无硬件闭环。

仍需在客户现场独立完成：Orin NX 量化模型的内存/时延与容器验证、GPU 服务器真实模型协议与并发、真实机械臂标定和安全认证、生产数据库 HA、跨地域灾备、真实网络长稳与机群容量压测、非限频显示器上的可见帧率验收。仿真通过不是物理安全认证，千台逻辑隔离测试不是数千台生产容量证明。

独立 E2E/自然语言评测夹具使用 `admin / admin123`；`scripts/fleet-up.sh up` 和调用它的 RoboCasa Compose profile 会在 `deploy/cloud/.env` 生成密码，必须读取实际值。两类环境的账号和端口不能混用。
