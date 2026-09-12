# 文档索引

同步日期：2026-09-11。操作入口适用于 `v0.5.0`；新用户按 `git clone --branch v0.5.0` 获取准确代码。发布身份与本轮实际验证见 [v0.5.0 发布记录](releases/v0.5.0.md)，功能变更见[Changelog](../CHANGELOG.md)。

本索引区分当前操作说明与历史证据。当前能力和限制以 [V1 当前状态](production/v1-release-status.md)为准；设计文档解释决策，代码与对应测试确定实际接口。发现冲突时核对源码并更新当前指南，不把历史测试结果自动套到新版本。

**当前主线是一台机器人在四房间家庭场景完成自然语言任务**：`make home-start` 起场景，说“从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅”，走完观察 → 导航 → 到达确认 → 重新观察 → 解析目标 → 规划抓取 → 拿取 → 抓取确认 → 放置 → 放置确认 → 返回 → 到达确认。操作步骤、工具与证据见[四房间家庭场景操作](guides/home-scene-operations.md)，原理见[RGB-D 闭环开发指南](development/single-robot-loop.md)，实机见[家庭 Sim2Real](guides/home-sim2real.md)。

固定工位（`make rgbd-start`）、双机 RoboCasa 与 Fleet 云端仍然保留且仍有测试，但不是当前投入方向，见[其他路线](#其他路线暂不聚焦)与[部署目标与代码归属](deployment.md)。

## 按任务阅读

| 任务 | 阅读顺序 |
| --- | --- |
| 搞清楚哪部分装云端、哪部分装机器人 | [部署目标与代码归属](deployment.md) → [`deploy/README.md`](../deploy/README.md) → [安装](install/local.md) |
| 一次启动全部组件 | [部署目标与代码归属 § 一次启动全部组件](deployment.md#6-一次启动全部组件)：`./scripts/start-all.sh up` |
| 购买了 XLeRobot | [购机后 Sim2Real 上手](sim2real/README.md) → [树莓派安装](install/robot-pi.md) → [首次实验](install/xlerobot-experiment.md) |
| 加入项目开发 | [开发快速上手](development/getting-started.md) → [原则与源码地图](development/principles.md) → [架构](production/architecture.md) |
| 提交改动、准备发布 | [分支与发布规范](development/branching.md)：`main` 是最新可发布状态，发布打 `vX.Y.Z` 标签，不建长期版本分支 |
| 接入不同机器人和传感器 | [适配器 SDK、三维感知与接入验收](development/robot-adapters.md) → [统一 MCP](../robot/mcp/README.md) |
| 接通建图、定位和移动任务 | [RTAB-Map / Nav2 与双 RGB-D](development/rtabmap-navigation.md) → [导航部署包](../deploy/robot/navigation/) |
| 使用 Gazebo Harmonic 家庭仿真完成 SLAM 与自然语言路线 | [Gazebo 家庭场景操作](guides/gazebo-house-operations.md) |
| 验证家庭场景和 Sim2Real | [家庭场景操作](guides/home-scene-operations.md) → [家庭 Sim2Real](guides/home-sim2real.md) → [发布验收清单](operations/release-checklist.md) |
| **跑通家居自然语言闭环（当前主线）** | [四房间家庭场景操作](guides/home-scene-operations.md)：`make home-start` → 输入任务 → 核对证据；命令行复现用 `make home-accept` |
| 接实机 | [家庭 Sim2Real](guides/home-sim2real.md) → [整机标定](development/robot-calibration.md)（`scripts/calibrate_guided.py`）→ [发布验收清单](operations/release-checklist.md) |
| 实机前置工作的前端方案 | [标定与建图的前端方案](development/sim2real-onboarding-frontend.md)：相机部署、覆盖指标与补拍、three.js 稠密地图 |
| 标定与建图的 ROS 方案 | [ROS 方案与精度门禁](development/calibration-slam-ros-plan.md)：内参/手眼/外参/里程计各自的门槛与实施顺序 |
| 稠密地图浏览器查看器 | [升级方案与任务拆解](development/dense-map-viewer-plan.md)：COPC 选型、LOD、API 契约、P1–P3 拆解与性能预算 |
| 稠密地图运维 | [构建、部署与运维](development/dense-map-operations.md)：`scripts/build_map.py`、`TANGYING_MAP_ROOT`、什么数据库不能导出 |
| 家居场景扩充 | [从 1 个物体到完整任务](development/home-scene-expansion-plan.md)：四处耦合改动、感知颜色表、多物体 NL 任务与验收顺序 |
| RGB-D 相机修正 | [位置错误与 D435i 统一](development/rgbd-camera-fix.md)：实测证据、补丁、以及为何需先重算自滤波 |
| 其他路线（暂不聚焦） | [固定工位与离桌导航仿真](quickstart.md) → [RoboCasa 双机](robocasa-handoff.md) → [Fleet 云端](fleet-cloud.md) |
| 测试自然语言 Agent | [评测结果、复现命令与能力边界](development/natural-language-evaluation.md) → [Agent V1](agent-v1.md) |
| 日常操作工作台 | [用户说明](user-console.md) → [前端 V1 与开发诊断](frontend/console-v1.md) |
| 复盘任务执行过程 | [任务全过程回放](frontend/console-v1.md#任务全过程回放)：按任务编号打开任意历史任务，逐步对齐工具调用、观测证据与恢复状态，并列出不一致项 |
| 部署和排障 | [生产手册索引](production/README.md) → [配置与安全](production/configuration-and-security.md) → [异常运维](production/operations-and-failures.md) |
| 检查版本或 MuJoCo 依赖差异 | [v0.5.0 发布记录](releases/v0.5.0.md) → [仿真引擎版本与兼容检查](development/mujoco-compatibility.md) |

## 当前参考资料

| 范围 | 文档 |
| --- | --- |
| 分支与发布 | [分支与发布规范](development/branching.md) |
| 架构与分布式边界 | [完整架构](production/architecture.md)、[架构演进](architecture.md)、[分布式成熟度](distributed-agentos.md)、[多机器人](multi-robot.md) |
| 部署目标与代码归属 | [云端 / 机器人端 / 本地单机](deployment.md)、[`deploy/` 目录清单](../deploy/README.md)、[一键启动](../scripts/start-all.sh) |
| Agent 与基础设施 | [Agent V1](agent-v1.md)、[LLM 编排](orchestration.md)、[Middleware](middleware.md) |
| 协议与扩展 | [API](production/api-reference.md)、[数据契约](production/data-contracts.md)、[Runtime 不变量](protocols.md)、[策略 sidecar](production/policy-tools.md) |
| 整机标定 | [整机标定：robot.calibration.v1](development/robot-calibration.md) |
| 机器人工具层 | [工具层总览](development/robot-tool-layer.md)、`tools.json`（LLM function calling）、[MoveIt 2 适配](development/arm-moveit-adapter.md)、[异构适配器开发](development/robot-adapters.md) |
| 安装 | [Local](install/local.md)、[树莓派完整](install/robot-pi.md)、[树莓派快捷](install/robot-pi-quick.md)、[Fleet](fleet-cloud.md)、[阿里云](install/alicloud-cloud.md)、[安装排障](install/troubleshooting.md) |
| 硬件与晋级 | [XLeRobot 集成边界](xlerobot-setup.md)、[安全检查表](safety-checklist.md)、[离线前置检查](production-readiness.md)、[Sim2Real 架构接入](production/sim-to-real.md) |
| 发布与运维 | [综合快速上手](production/quickstart.md)、[测试与验收](production/testing-and-acceptance.md)、[部署与容量](production/deployment-and-capacity.md)、[V1 当前状态](production/v1-release-status.md) |

当前前端操作入口是“工作台 / 任务记录 / 我的机器人”。“开发诊断”需要开启开发模式；LLM、底层状态与调试信息不在默认任务流程。Local 任务需要单独批准；Fleet 页面创建并开始会立即批准。服务端权限始终由后端决定。

当前语义以完整确定性解析优先，识别到未解决的约束时要求澄清；RoboCasa 仍为单回合定向交接。任务 Experience 的同版本进展可更新，WorldSnapshot 的同版本事实不能改写，两者规则见[数据契约](production/data-contracts.md)。最新 13 项固定用例、浏览器补测和边界见[自然语言评测](development/natural-language-evaluation.md)。

## 其他路线（暂不聚焦）

这些能力**仍然保留且仍有测试**，只是当前不投入，也不作为新用户的第一条路径：

| 路线 | 入口 | 说明 |
| --- | --- | --- |
| 固定工位桌面任务 | `make rgbd-start`（`--scene tabletop`） | 单工位、离桌导航与长暂停验收；家居场景的前身 |
| 双机 RoboCasa 交接 | [RoboCasa 指南](robocasa-handoff.md) · `./scripts/fleet-sim.sh handoff` | 需要独立 Conda 环境与本地场景资产 |
| Fleet 云端与多机器人 | [Fleet 部署](fleet-cloud.md) · `./scripts/fleet-up.sh up` | 单机器人把自己的任务做完即可；多机协调属于调度层 |

## 历史与设计档案

这些文件保留原始时间、版本和判断，不能作为当前安装步骤或实机放行依据：

- [2026-09-05 V1 评估](production/v1-assessment-2026-09-05.md)：当时的审计、修复与验证快照，后续变化看当前状态页。
- [2026-08-24 发布证据](production/release-evidence.md)：`v0.2.0-rc.2` 与签名 `round4` 的历史证据；离线重验证明该包完整，不证明现有工作区已重新采集。
- [早期 Fleet 论文闭环](fleet-paper-loop.md)：杯子/瓶子与全局栅格的旧实验；当前共享红方块和 Harness 流程看 [RoboCasa](robocasa-handoff.md)。
- [旧 Cloud 安装](install/cloud.md)：PostgreSQL 旧控制面的归档，当前云部署使用 Fleet。
- [superpowers/specs](superpowers/specs/)：按日期保存的设计决策记录（ADR）。早期 local-first、ROS 2 默认安装及未来 HA 设想都保留为演进记录；设计档案不替代发布证据。当时的实施计划清单不随发布分发。

新增功能时同步更新本索引、对应操作指南、接口/配置说明和验证范围。不要只在 README 新增链接而留下被链接指南中的旧命令。
