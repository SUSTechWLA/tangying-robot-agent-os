# 文档索引

同步日期：2026-09-11。操作入口适用于 `v0.3.0`；新用户按 `git clone --branch v0.3.0` 获取准确代码。发布身份与本轮实际验证见 [v0.3.0 发布记录](releases/v0.3.0.md)，功能变更见[Changelog](../CHANGELOG.md)。

本索引区分当前操作说明与历史证据。当前能力和限制以 [V1 当前状态](production/v1-release-status.md)为准；设计文档解释决策，代码与对应测试确定实际接口。发现冲突时核对源码并更新当前指南，不把历史测试结果自动套到新版本。

首版交付主线是[单机器人 V1](production/single-robot-v1.md)与[RGB-D 闭环开发指南](development/single-robot-loop.md)。`make rgbd-start` 保持固定工位；`make navigation-start NAVIGATION_ARGS='--build --mode mapping'` 从离桌位置运行双相机导航，名义接近位移约 65 cm。家庭路线使用[四房间家庭场景操作](guides/home-scene-operations.md)：`--scene home` 验证路线/SLAM，`--scene home_task` 验证自然语言导航、抓取、放置和环境确认。任务与工具证据、相机视角、局部点云和暂停恢复共用 Local 路线；真实 ROS 输入见[ROS 2 RGB-D](development/ros2-rgbd.md)。双机器人文档保留为扩展路线。

## 按任务阅读

| 任务 | 阅读顺序 |
| --- | --- |
| 购买了 XLeRobot | [购机后 Sim2Real 上手](sim2real/README.md) → [树莓派安装](install/robot-pi.md) → [首次实验](install/xlerobot-experiment.md) |
| 加入项目开发 | [开发快速上手](development/getting-started.md) → [原则与源码地图](development/principles.md) → [架构](production/architecture.md) |
| 接入不同机器人和传感器 | [适配器 SDK、三维感知与接入验收](development/robot-adapters.md) → [统一 MCP](../robot/mcp/README.md) |
| 接通建图、定位和移动任务 | [RTAB-Map / Nav2 与双 RGB-D](development/rtabmap-navigation.md) → [导航部署包](../deploy/navigation/) |
| 使用 Gazebo Harmonic 家庭仿真完成 SLAM 与自然语言路线 | [Gazebo 家庭场景操作](guides/gazebo-house-operations.md) |
| 验证家庭场景和 Sim2Real | [家庭场景操作](guides/home-scene-operations.md) → [家庭 Sim2Real](guides/home-sim2real.md) → [发布验收清单](operations/release-checklist.md) |
| 先体验无硬件闭环 | [固定工位与离桌导航仿真](quickstart.md) → [18 步与长暂停验收](development/single-robot-loop.md#移动任务与长暂停验收) → [RoboCasa 双机](robocasa-handoff.md) |
| 测试自然语言 Agent | [评测结果、复现命令与能力边界](development/natural-language-evaluation.md) → [Agent V1](agent-v1.md) |
| 日常操作工作台 | [用户说明](user-console.md) → [前端 V1 与开发诊断](frontend/console-v1.md) |
| 部署和排障 | [生产手册索引](production/README.md) → [配置与安全](production/configuration-and-security.md) → [异常运维](production/operations-and-failures.md) |
| 检查版本或 MuJoCo 依赖差异 | [v0.3.0 发布记录](releases/v0.3.0.md) → [仿真引擎版本与兼容检查](development/mujoco-compatibility.md) |

## 当前参考资料

| 范围 | 文档 |
| --- | --- |
| 架构与分布式边界 | [完整架构](production/architecture.md)、[架构演进](architecture.md)、[分布式成熟度](distributed-agentos.md)、[多机器人](multi-robot.md) |
| Agent 与基础设施 | [Agent V1](agent-v1.md)、[LLM 编排](orchestration.md)、[Middleware](middleware.md) |
| 协议与扩展 | [API](production/api-reference.md)、[数据契约](production/data-contracts.md)、[Runtime 不变量](protocols.md)、[策略 sidecar](production/policy-tools.md) |
| 安装 | [Local](install/local.md)、[树莓派完整](install/robot-pi.md)、[树莓派快捷](install/robot-pi-quick.md)、[Fleet](fleet-cloud.md)、[阿里云](install/alicloud-cloud.md)、[安装排障](install/troubleshooting.md) |
| 硬件与晋级 | [XLeRobot 集成边界](xlerobot-setup.md)、[安全检查表](safety-checklist.md)、[离线前置检查](production-readiness.md)、[Sim2Real 架构接入](production/sim-to-real.md) |
| 发布与运维 | [综合快速上手](production/quickstart.md)、[测试与验收](production/testing-and-acceptance.md)、[部署与容量](production/deployment-and-capacity.md)、[V1 当前状态](production/v1-release-status.md) |

当前前端操作入口是“工作台 / 任务记录 / 我的机器人”。“开发诊断”需要开启开发模式；LLM、底层状态与调试信息不在默认任务流程。Local 任务需要单独批准；Fleet 页面创建并开始会立即批准。服务端权限始终由后端决定。

当前语义以完整确定性解析优先，识别到未解决的约束时要求澄清；RoboCasa 仍为单回合定向交接。任务 Experience 的同版本进展可更新，WorldSnapshot 的同版本事实不能改写，两者规则见[数据契约](production/data-contracts.md)。最新 13 项固定用例、浏览器补测和边界见[自然语言评测](development/natural-language-evaluation.md)。

## 历史与设计档案

这些文件保留原始时间、版本和判断，不能作为当前安装步骤或实机放行依据：

- [2026-09-05 V1 评估](production/v1-assessment-2026-09-05.md)：当时的审计、修复与验证快照，后续变化看当前状态页。
- [2026-08-24 发布证据](production/release-evidence.md)：`v0.2.0-rc.2` 与签名 `round4` 的历史证据；离线重验证明该包完整，不证明现有工作区已重新采集。
- [早期 Fleet 论文闭环](fleet-paper-loop.md)：杯子/瓶子与全局栅格的旧实验；当前共享红方块和 Harness 流程看 [RoboCasa](robocasa-handoff.md)。
- [旧 Cloud 安装](install/cloud.md)：PostgreSQL 旧控制面的归档，当前云部署使用 Fleet。
- [superpowers/specs](superpowers/specs/) 与 [superpowers/plans](superpowers/plans/)：按日期保存的设计和实施计划。早期 local-first、ROS 2 默认安装及未来 HA 设想都保留为演进记录；计划打勾不替代发布证据。

新增功能时同步更新本索引、对应操作指南、接口/配置说明和验证范围。不要只在 README 新增链接而留下被链接指南中的旧命令。
