# 文档索引

同步日期：2026-09-13。装修家庭与关键帧功能使用最新 `main`；[v0.6.0 发布记录](releases/v0.6.0.md)记录本轮发布身份，[v0.5.0 发布记录](releases/v0.5.0.md)保留历史发布身份。

本索引区分当前操作说明与历史证据。当前能力和限制以 [V1 当前状态](production/v1-release-status.md)为准；设计文档解释决策，代码与对应测试确定实际接口。发现冲突时核对源码并更新当前指南，不把历史测试结果自动套到新版本。

**当前主线是一台机器人在装修家庭场景完成自然语言任务**：`make home-furnished` 启动，完成标定、移动建图与地图启用后，说“从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅”，走完观察 → 导航 → 到达确认 → 重新观察 → 解析目标 → 规划抓取 → 拿取 → 抓取确认 → 放置 → 放置确认 → 返回 → 到达确认。操作步骤、模型来源与限制见[装修家庭演示指南](guides/furnished-home-demo.md)，原理见[RGB-D 闭环开发指南](development/single-robot-loop.md)，实机见[家庭 Sim2Real](guides/home-sim2real.md)。

固定工位（`make rgbd-start`）、双机 RoboCasa 与 Fleet 云端仍然保留且仍有测试，但不是当前投入方向，见[其他路线](#其他路线暂不聚焦)与[部署目标与代码归属](operations/deployment.md)。

## 按任务阅读

| 任务 | 阅读顺序 |
| --- | --- |
| **全新机器/机器人/云服务器，从这里开始** | [全新部署冷启动](operations/fresh-deployment.md)：先跑 `./scripts/precheck.sh`，再按路线装 |
| 搞清楚哪部分装云端、哪部分装机器人 | [部署目标与代码归属](operations/deployment.md) → [`deploy/README.md`](../deploy/README.md) → [安装](install/local.md) |
| 一次启动全部组件 | [部署目标与代码归属 § 一次启动全部组件](operations/deployment.md#6-一次启动全部组件)：`./scripts/start-all.sh up` |
| 购买了 XLeRobot | [购机后 Sim2Real 上手](sim2real/README.md) → [树莓派安装](install/robot-pi.md) → [首次实验](install/xlerobot-experiment.md) |
| 加入项目开发 | [开发快速上手](development/getting-started.md) → [原则与源码地图](development/principles.md) → [架构](production/architecture.md) |
| 提交改动、准备发布 | [分支与发布规范](development/branching.md)：`main` 是最新可发布状态，发布打 `vX.Y.Z` 标签，不建长期版本分支 |
| 接入不同机器人和传感器 | [适配器 SDK、三维感知与接入验收](development/robot-adapters.md) → [统一 MCP](../robot/mcp/README.md) |
| 接通建图、定位和移动任务 | [RTAB-Map / Nav2 与双 RGB-D](development/rtabmap-navigation.md) → [导航部署包](../deploy/robot/navigation/) |
| 使用 Gazebo Harmonic 家庭仿真完成 SLAM 与自然语言路线 | [Gazebo 家庭场景操作](guides/gazebo-house-operations.md) |
| 验证家庭场景和 Sim2Real | [家庭场景操作](guides/home-scene-operations.md) → [家庭 Sim2Real](guides/home-sim2real.md) → [发布验收清单](operations/release-checklist.md) |
| 标定、建图与地图启用 | [注册服务工作流](guides/robot-service-workflow.md)：整机标定、巡检建图、关键帧检查、地图启用 |
| 分析保存地图的 SLAM 关键帧 | [关键帧检查](guides/slam-keyframe-inspection.md)：RGB/深度预览、里程计与优化位姿、配准/回环质量、历史版本与资源预算 |
| 接实机 | [家庭 Sim2Real](guides/home-sim2real.md) → [整机标定](development/robot-calibration.md)（`scripts/calibrate_guided.py`）→ [发布验收清单](operations/release-checklist.md) |
| 实机前置工作的前端方案 | [标定与建图的前端方案](development/sim2real-onboarding-frontend.md)：相机部署、覆盖指标与补拍、three.js 稠密地图 |
| 标定与建图的 ROS 方案 | [ROS 方案与精度门禁](development/calibration-slam-ros-plan.md)：内参/手眼/外参/里程计各自的门槛与实施顺序 |
| 稠密地图浏览器查看器 | [升级方案与任务拆解](development/dense-map-viewer-plan.md)：COPC 选型、LOD、API 契约、P1–P3 拆解与性能预算 |
| 稠密地图运维 | [构建、部署与运维](development/dense-map-operations.md)：`scripts/build_map.py`、`TANGYING_MAP_ROOT`、什么数据库不能导出 |
| 家居场景扩充 | [从 1 个物体到完整任务](development/home-scene-expansion-plan.md)：四处耦合改动、感知颜色表、多物体 NL 任务与验收顺序 |
| RGB-D 相机修正 | [位置错误与 D435i 统一](development/rgbd-camera-fix.md)：实测证据、补丁、以及为何需先重算自滤波 |
| Real2Sim | [用机器人自己的 SLAM 建图生成仿真场景](development/real2sim-from-robot-slam.md)：分段保真度、P1–P3、以及为什么可操作物体不该从点云重建 |
| 其他路线（暂不聚焦） | [固定工位与离桌导航仿真](guides/quickstart.md) → [RoboCasa 双机](operations/robocasa-handoff.md) → [Fleet 云端](architecture/fleet-cloud.md) |
| 测试自然语言 Agent | [评测结果、复现命令与能力边界](development/natural-language-evaluation.md) → [Agent V1](architecture/agent-v1.md) |
| 日常操作工作台 | [用户说明](guides/user-console.md) → [前端 V1 与开发诊断](frontend/console-v1.md) |
| 复盘任务执行过程 | [任务全过程回放](frontend/console-v1.md#任务全过程回放)：按任务编号打开任意历史任务，逐步对齐工具调用、观测证据与恢复状态，并列出不一致项 |
| 理解/扩展 Agent 层 | [多 Agent 运行时](architecture/multi-agent-runtime.md) → [Agent 事件规范](architecture/agent-events.md) → [如何新增一个 Agent](development/adding-an-agent.md) → [配置与回滚](operations/agent-runtime-config.md) |
| 验证监督 agent 是否真的有用 | [监督 Agent：故障矩阵与可回溯验证](architecture/supervision-verification.md)：14 个故障场景逐一验证检出+分类+建议，含重启盲区的发现与修复 |
| 搞懂监督 agent 怎么工作、边界在哪 | [Review Agent 运行原理](architecture/review-agent.md)：三条不可动摇的规则、告警为何自己消失、恢复闭环走到哪一步 |
| 看这轮改动的发现过程与教训 | [监督能力的三个盲区](development/2026-09-17-supervision-blind-spots.md)：怎么发现的、怎么修的、以及我犯的错 |
| 搞懂一次任务里有几种对象在各自走状态 | [生命周期对象](architecture/lifecycle-objects.md)：一次拿杯子任务拆到秒，配真实事故记录；含“该不该加新对象”的判据 |
| 部署和排障 | [生产手册索引](production/README.md) → [配置与安全](production/configuration-and-security.md) → [异常运维](production/operations-and-failures.md) |
| 检查版本或 MuJoCo 依赖差异 | [v0.6.0 发布记录](releases/v0.6.0.md) → [仿真引擎版本与兼容检查](development/mujoco-compatibility.md) |

## 新人第一小时

1. **看这个项目是什么**（10 分钟）：根目录 [`README.md`](../README.md) 的主张与一句话任务；本文档的"当前主线"一段。
2. **跑起来**（20 分钟）：[仿真开发快速上手](guides/quickstart.md) → `make setup && make home-furnished`，打开 `http://127.0.0.1:8897`。
3. **看一次完整任务**（10 分钟）：控制台里提交"从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅"，在"任务记录"里看每一步的证据与恢复状态。
4. **理解设计**（15 分钟）：[完整架构](production/architecture.md) → [架构演进](architecture/architecture.md) → [闭环契约与失败分类](../core/closedloop/)。
5. **改第一行代码**（15 分钟）：[开发快速上手](development/getting-started.md) → [原则与源码地图](development/principles.md) → 跑 `make lint && make test-python`。

## 仓库地图：每类东西放在哪

| 目录 | 放什么 | 谁维护 | 会被提交吗 |
| --- | --- | --- | --- |
| `docs/` | **人读的文档**：按目的分 `install/`（装）、`guides/`（用）、`architecture/`（原理）、`development/`（开发与逐轮升级记录）、`operations/`（放行与安全）、`production/`（当前状态与契约）、`releases/`（发布身份）、`sim2real/`、`frontend/` | 改代码的人同步改 | 是 |
| `artifacts/` | **机器产出的证据**：地图、标定、验收记录、基准报告、事故记录（`incidents/`）。大多数被 `.gitignore` 排除，只有**结论性小文件**入库（如 `destination-policy/`、`slam-exploration-coverage/`、`incidents/`、`semantic-benchmark/*.json`） | 脚本自动写 | 部分 |
| `artifacts/marketing/` | **对外宣传材料**（人工撰写 + 截图产物），按"一期一目录"组织，不属于产品构建，也不参与测试 | 发布者 | 是 |
| `robot/`、`sim/`、`edge/`、`fleet/`、`core/`、`tasks/`、`agent/`、`orchestration/`、`skills/`、`console/`、`web/`、`internal/`、`cmd/` | 代码（见各目录自己的 `README.md`） | 开发者 | 是 |
| `proto/` | 跨语言契约（`robot.v1`、`world.snapshot.v1`、`scene.reconstruction.v1`、`robot.profile.v1`） | 改接口的人 | 是 |
| `scripts/` | 可执行工具：评测、验收、诊断（`evaluate_slam.py`、`diagnose_task.py`、`build_sim_map.py`…） | 开发者 | 是 |
| `tests/` | 单元、e2e、故障矩阵与文档校验（含链接检查） | 开发者 | 是 |
| `artifacts/training/`（忽略） | 训练产物与检查点；策略以 manifest + 权重哈希登记，运行时通过 `policy_execution` 引用 | 训练者 | 否 |

**判断一个文件该放哪**：能被脚本重新生成的（地图、点云、基准、事故记录）放 `artifacts/`；人写给人看的（指南、设计、宣传）放 `docs/` 或 `artifacts/marketing/`；接口约定放 `proto/`；其余按代码模块归属。

## 升级与审计记录（按日期，最新在上）

这些是每一轮优化的**对照实验与实测结论**，写清"为什么改、改成什么、量到了什么、边界在哪"。
读它们比读提交历史快，也比读代码省事；实现细节仍以源码与测试为准。

| 主题 | 记录 |
| --- | --- |
| 硬件故障发布成观测：能否被大脑看见 | [硬件故障发布成观测](development/2026-09-16-faults-as-observations.md)：运行时只发布能自证的三种故障、`safety` 级整体封锁但急停永不可摘、Go 契约解码拒绝"自相矛盾的故障表"、跨语言契约测试与真机栈实测，以及实测暴露的两个真问题（`occurrences` 单位错误、三项能力"不可用但没说原因"） |
| 演示地图升级计划 | [2026-09-13 演示地图升级计划](development/2026-09-13-demo-map-upgrade-plan.md) |
| 仓库整理：文档/证据/宣传/代码各归其位 | [仓库整理](development/2026-09-16-repository-organization.md)：docs 归类与链接重写、新人第一小时与仓库地图、artifacts 与 marketing 的边界、模型产物放哪、以及修掉的一个仓库检查 bug |
| **跑通家居自然语言闭环（当前主线）** | [装修家庭演示](guides/furnished-home-demo.md)：`make home-furnished` → 标定建图 → 输入任务 → 核对证据；[实际验收记录](development/2026-09-13-furnished-home-acceptance.md) |
| 语义层升级的对比实验与量化结论 | [语义层升级：物体记忆与工作区可达位姿](development/2026-09-14-semantic-object-layer-upgrade.md)：为什么改、四个维度（运行时间/占用资源/复杂任务/执行响应）的实测差异、边界与下一步 |
| 系统当前评价与下一步重点 | [系统评价与重点优化方向](development/2026-09-15-optimization-backlog.md)：八项按重要程度排序的优化，每项含"为什么非加不可"与对比实验设计（指标 + 判据） |
| P0-1/P0-2 升级与实测 | [上机性能遥测与“回到上次看到它的地方”](development/2026-09-15-latency-and-recall-goal-upgrade.md)：四段耗时插桩的实测开销、vantage 记录与回退规则、对照实验的真实结果与三个环境发现 |
| SLAM 探索为什么扫不全、怎么改 | [探索覆盖升级：进门、预算、关键帧与深度不足](development/2026-09-15-slam-exploration-coverage-upgrade.md)：门宽实测、预算去向、四处参数改动与 38.9% → 81% 的对照 |
| 勘测回望 / 任务前置摆位 / 回忆新鲜度参数 | [三项收口：回望、前置摆位、新鲜度](development/2026-09-15-survey-lookback-and-pre-position.md)：起点未观测问题的实测与分工、NAV_ROTATION_LIMIT 的真实成因与分步对齐、窗口成为部署参数 |
| 机器人异常处理审计（能否恢复、是否入表） | [机器人异常处理审计](development/2026-09-15-robot-fault-handling-audit.md)：机器人侧 13 类异常的行为/恢复/入表对照，云侧 10 类故障实跑结果，以及本轮修掉的"故障丢弃整张地图"缺陷 |
| 放置核验失败与房间目标认证 | [PLACEMENT_NOT_OBSERVED 的定位、验证与两处修复](development/2026-09-15-placement-verification-and-certified-goals.md)：失败自证、房间目标吸附到地图认证位姿、抓取链首次全绿的实测 |
| 工具封装与导航受阻的可扩展性评审 | [可扩展性评审：工具与导航](development/2026-09-15-extensibility-review-tools-and-navigation.md)：五处工具定义、已留好的三个扩展口、写死的物体属性轴、导航受阻的四层保护与四个缺口、v1 只加接口的建议 |
| 可扩展性落地（一）：物体物理属性 + 地图冲突只读 | [可扩展性评审：工具与导航](development/2026-09-15-extensibility-review-tools-and-navigation.md)第三节：material/mass/力上限词汇表与抓取预算、mapping.conflicts 只读比对 |
| AI 回溯诊断与自动运维闭环（含自动产出） | [AI 回溯诊断与自动运维闭环](development/2026-09-15-ai-incident-diagnosis-and-ops-loop.md)：异常终态自动写 bundle、incident.v1 记录、7 个故障族与回归测试指针、coding agent 复盘闭环与护栏、仍缺的持久化 |

## 当前参考资料

| 范围 | 文档 |
| --- | --- |
| 分支与发布 | [分支与发布规范](development/branching.md) |
| 模块化机器人的故障上报与自愈 | [模块故障与自愈设计](architecture/module-health-and-faults.md)：模块身份（profile 数据）→ 故障事实 `robot.faults.v1` → 能力联动（复用已有门禁）→ 处置阶梯（自愈/请人/维修）→ 用户提醒；检测四层与落地顺序 |
| 架构与分布式边界 | [完整架构](production/architecture.md)、[架构演进](architecture/architecture.md)、[分布式成熟度](architecture/distributed-agentos.md)、[多机器人](architecture/multi-robot.md) |
| 部署目标与代码归属 | [云端 / 机器人端 / 本地单机](operations/deployment.md)、[`deploy/` 目录清单](../deploy/README.md)、[一键启动](../scripts/start-all.sh) |
| Agent 与基础设施 | [Agent V1](architecture/agent-v1.md)、[LLM 编排](architecture/orchestration.md)、[Middleware](architecture/middleware.md) |
| 协议与扩展 | [API](production/api-reference.md)、[数据契约](production/data-contracts.md)、[Runtime 不变量](architecture/protocols.md)、[策略 sidecar](production/policy-tools.md) |
| 整机标定 | [整机标定：robot.calibration.v1](development/robot-calibration.md) |
| 机器人工具层 | [工具层总览](development/robot-tool-layer.md)、`tools.json`（LLM function calling）、[MoveIt 2 适配](development/arm-moveit-adapter.md)、[异构适配器开发](development/robot-adapters.md) |
| 安装 | [Local](install/local.md)、[树莓派完整](install/robot-pi.md)、[树莓派快捷](install/robot-pi-quick.md)、[Fleet](architecture/fleet-cloud.md)、[阿里云](install/alicloud-cloud.md)、[安装排障](install/troubleshooting.md) |
| 硬件与晋级 | [XLeRobot 集成边界](install/xlerobot-setup.md)、[安全检查表](operations/safety-checklist.md)、[离线前置检查](operations/production-readiness.md)、[Sim2Real 架构接入](production/sim-to-real.md) |
| 发布与运维 | [综合快速上手](production/quickstart.md)、[测试与验收](production/testing-and-acceptance.md)、[部署与容量](production/deployment-and-capacity.md)、[V1 当前状态](production/v1-release-status.md) |

当前前端操作入口是“工作台 / 任务记录 / 我的机器人”。“开发诊断”需要开启开发模式；LLM、底层状态与调试信息不在默认任务流程。Local 任务需要单独批准；Fleet 页面创建并开始会立即批准。服务端权限始终由后端决定。

当前语义以完整确定性解析优先，识别到未解决的约束时要求澄清；RoboCasa 仍为单回合定向交接。任务 Experience 的同版本进展可更新，WorldSnapshot 的同版本事实不能改写，两者规则见[数据契约](production/data-contracts.md)。最新 13 项固定用例、浏览器补测和边界见[自然语言评测](development/natural-language-evaluation.md)。

## 对外宣传材料

不是产品文档，也不参与构建与测试：系列文章、配图与素材出处放在 [`artifacts/marketing/`](../artifacts/marketing/README.md)，按"一期一目录"组织。每期带 `素材来源与数字出处.md`，**发布前必须逐条核对**；能力说法与本文档冲突时以本索引与源码为准。

**为什么它和证据放在同一个 `artifacts/` 下**：本仓库的 `artifacts/` 不是"构建中间产物"，而是"**不参与产品构建与测试的非产品材料**"——地图/基准/事故记录这类**脚本产出**的证据，以及宣传这类**人工撰写**的对外材料都放这里，两者都以"不进构建、不被服务读取"为共同点。如果你更希望物理上分开，顶层 `marketing/` 是等价的另一种放法（代价是 README、本索引与各期互相引用的链接要一起改）；现在的选择以"引用稳定"为先。

## 其他路线（暂不聚焦）

这些能力**仍然保留且仍有测试**，只是当前不投入，也不作为新用户的第一条路径：

| 路线 | 入口 | 说明 |
| --- | --- | --- |
| 固定工位桌面任务 | `make rgbd-start`（`--scene tabletop`） | 单工位、离桌导航与长暂停验收；家居场景的前身 |
| 双机 RoboCasa 交接 | [RoboCasa 指南](operations/robocasa-handoff.md) · `./scripts/fleet-sim.sh handoff` | 需要独立 Conda 环境与本地场景资产 |
| Fleet 云端与多机器人 | [Fleet 部署](architecture/fleet-cloud.md) · `./scripts/fleet-up.sh up` | 单机器人把自己的任务做完即可；多机协调属于调度层 |

## 历史与设计档案

这些文件保留原始时间、版本和判断，不能作为当前安装步骤或实机放行依据：

- [2026-09-05 V1 评估](production/v1-assessment-2026-09-05.md)：当时的审计、修复与验证快照，后续变化看当前状态页。
- [2026-08-24 发布证据](production/release-evidence.md)：`v0.2.0-rc.2` 与签名 `round4` 的历史证据；离线重验证明该包完整，不证明现有工作区已重新采集。
- [早期 Fleet 论文闭环](architecture/fleet-paper-loop.md)：杯子/瓶子与全局栅格的旧实验；当前共享红方块和 Harness 流程看 [RoboCasa](operations/robocasa-handoff.md)。
- [旧 Cloud 安装](install/cloud.md)：PostgreSQL 旧控制面的归档，当前云部署使用 Fleet。
- [superpowers/specs](superpowers/specs/)：按日期保存的设计决策记录（ADR）。早期 local-first、ROS 2 默认安装及未来 HA 设想都保留为演进记录；设计档案不替代发布证据。当时的实施计划清单不随发布分发。

新增功能时同步更新本索引、对应操作指南、接口/配置说明和验证范围。不要只在 README 新增链接而留下被链接指南中的旧命令。
