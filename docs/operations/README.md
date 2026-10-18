# 部署、运维与现场交付导航

核对日期：2026-10-16，实现参考 `821659df`。目标委托的部署、持久状态、续跑判断与升级顺序集中见[目标运维](delegated-goal-operations.md)。

本页按实际工作顺序组织已有文档，供部署人员、值班人员和设备集成人员查阅。需要学习架构或查研究证据时，返回[文档总入口](../README.md)或[实验索引](../experiments/README.md)。具体命令、参数和限制保留在各专题原文，避免多处复制后产生版本漂移。

无人车、足式与无人机统一按 ROS 2 接入的最新设备侧流程见[边缘 Agent 实机测试部署](edge-field-testing.md)，包括单机长任务、配置指纹、数据通信、云端交接及状态文件迁移；本轮软件验证见[升级记录](../development/2026-10-13-edge-field-candidate.md)。

2026-10-18 增量：首轮[大疆、宇树、AGV 的 ROS 2 绑定与只读预检](ros2-device-bindings.md)，配合[最新分层验收](../experiments/2026-10-18-edge-swarm-readiness.md)检查真实已验证范围；运维现在保留失联时的未解决异常，并按传感器来源显示时效。

最新[单机器人鲁棒性报告](../experiments/2026-10-15-single-robot-robustness.md)保留真实模型规划超时、Host 首次失败及修复后同原句新任务的证据，并与软件长步骤链分开。当前已有 Gazebo 仿真中的任务闭环证据，包括[点云语义五站巡检与一次只读故障自动恢复](../experiments/2026-10-13-semantic-execution.md)。这项实验使用人工语义标注、二维地面导航和到位验证，不能作为真机、开放词汇感知或多机器人现场协作的验收。安装完成、服务存活、离线检查通过与真实硬件放行是不同结论；实机仍需设备适配、现场测试和负责人签署。

## 1. 环境准备：先选验证环境，再安装

| 你的起点 | 优先阅读 | 用途 |
| --- | --- | --- |
| 新开发机、希望先跑仿真 | [开发入门](../development/getting-started.md) → [Gazebo 默认引擎指南](../guides/gazebo-default.md) | 准备依赖，启动当前同源家庭场景，区分建图与定位阶段 |
| 空机器上的通用部署预检 | [全新部署](fresh-deployment.md) | 查平台与安装角色；Gazebo 场景和资产以默认引擎指南为准 |
| 理解家庭环境与原始画面 | [装修家庭演示](../guides/furnished-home-demo.md) | 了解布局与资源；其中既有图片和历史成功率属于其声明的 MuJoCo 实验 |
| 单独排查 ROS/Gazebo 栈 | [Gazebo 家庭操作](../guides/gazebo-house-operations.md) | 查端口、ROS domain、容器和导航数据卷，不与完整演示栈争用资源 |
| 复现旧场景或双机交接 | [MuJoCo 家庭回归](../guides/home-scene-operations.md)、[RoboCasa 交接](robocasa-handoff.md) | 仅按对应后端、场景和版本复现，不合并不同实验的成功率 |

## 2. 部署角色：明确谁持有任务与动作权限

先读[部署目标与代码归属](deployment.md)，再选择下面的一条部署路线。同一 Runtime 的 Local Agent 与 Fleet Worker 不应同时成为任务控制端；Orin 的两种模式也应择一。云端负责粗目标、机群分工与批准，Local/Worker 共享 Edge Host 在授权内编排子任务并验证；旧冻结计划模式继续兼容，实际动作经过机器人侧执行与安全门禁。

| 部署位置 | 安装与配置入口 | 后续查阅 |
| --- | --- | --- |
| 开发机／单机器人 Local | [Local 安装与配对](../install/local.md) | [控制台](../guides/user-console.md)、[可用性自检](../architecture/readiness.md) |
| 树莓派 Robot Runtime | [完整安装](../install/robot-pi.md)、[快捷部署](../install/robot-pi-quick.md) | [XLeRobot 集成边界](../install/xlerobot-setup.md)、[首次动作检查](../install/xlerobot-experiment.md) |
| Orin NX 边缘设备 | [Orin 单机器人／Fleet Worker](../install/edge-orin.md) | 模式互斥、持久卷、模型路由与设备权限 |
| 云端 Fleet | [阿里云／Fleet 部署](../install/alicloud-cloud.md) | [配置与安全](../production/configuration-and-security.md)、[拓扑与容量](../production/deployment-and-capacity.md) |
| 其他型号机器人 | [硬件与 Agent 集成](../guides/hardware-agent-integration.md) | [适配器开发](../development/robot-adapters.md)、[家庭移动 Sim2Real](../guides/home-sim2real.md) |

当前 Fleet 按单主控制面部署；增加数据库、Redis 或应用副本不等于已获得多主 HA。[旧云安装文档](../install/cloud.md)已标记历史归档，不作为当前 Fleet 安装步骤。

## 3. 建图与标定：让地图、机器人和观测使用同一版本

| 工作 | 入口 | 完成后需要核对 |
| --- | --- | --- |
| 调用标定、建图等注册服务 | [机器人服务工作流](../guides/robot-service-workflow.md) | 服务目录、标定身份、地图制品以及真实读回结果 |
| 用自然语言安排标定和 SLAM | [统一能力目标](../guides/unified-capability-goals.md) | 草案、批准、操作回执和独立验证；旧建图入口的迁移规则 |
| 保存地图、重定位、房间导航 | [SLAM 与语义地点导航](../guides/slam-semantic-navigation.md)、[RTAB-Map / Nav2 接入](../development/rtabmap-navigation.md) | workflow 地图包与 RTAB-Map 数据库分别持久化，资源、标定与定位均匹配 |
| 检查地图质量与来源 | [SLAM 关键帧检查](../guides/slam-keyframe-inspection.md) | 采集时间、RGB-D 来源、相机外参、配准结果和地图摘要 |
| 从三维标注规划巡检路线 | [点云语义地图指南](../guides/semantic-point-cloud.md)、[语义执行协议](../architecture/semantic-execution-protocol.md) | 地图／语义版本、目标顺序、可达候选、冻结检查点与新鲜到位证据 |

修改相机、标定、地图、场景资源或语义版本后，按对应协议重新验证，不沿用失效的目标绑定。语义标签说明目标在哪里、来源是什么，不授予物理动作权限；`arrival` 证明到位，`object` 还需要符合合同的对象复核。

## 4. 任务运营：从创建到验收，而不只看终态

建议按[一条自然语言任务的完整生命周期](../guides/natural-language-task-lifecycle.md)阅读一次实际任务，再使用[统一能力目标](../guides/unified-capability-goals.md)和[控制台指南](../guides/user-console.md)操作。Local 的计划查看与批准流程、Fleet 的任务创建入口应按各自指南使用，不把一种模式的审批语义套到另一种模式。

| 日常问题 | 查阅入口 |
| --- | --- |
| 进程在线，但现在能否接任务 | [可用性自检](../architecture/readiness.md)；`healthz` 不能替代机器人、地图和观测就绪判断 |
| 启用 Task / Ops / Recovery 协作 | [Agent 运行时配置](agent-runtime-config.md)区分 Local 事件总线与共享 Host 的逻辑角色；按任务因果事件核对实际参与 |
| 云边模型、凭据与阶段路由 | [配置与安全](../production/configuration-and-security.md)、[统一能力目标](../guides/unified-capability-goals.md) |
| 接入抓放策略模型 | [学习型策略工具](../production/policy-tools.md)；区分语言规划和设备专属动作策略 |
| 任务很长，历史多、需要继续执行 | [长程协作与上下文](../architecture/long-horizon-collaboration.md)、[单机器人闭环与恢复](../development/single-robot-loop.md) |
| 为升级或论文保留本次运行 | [测试与验收](../production/testing-and-acceptance.md)、[实验索引](../experiments/README.md)；保存版本、原始请求、任务 ID、失败及原始证据，区分新运行与离线复验 |

## 5. 故障与恢复：按现象找入口

| 现象或错误 | 权威入口 | 首先区分什么 |
| --- | --- | --- |
| 安装失败、配对失败、找不到设备 | [统一安装排障](../install/troubleshooting.md)、[机器人配对](../architecture/robot-pairing.md) | 平台、服务、地址、证书和设备身份 |
| 有健康响应，却没有可执行任务 | [可用性自检](../architecture/readiness.md)、[分布式排障](../production/operations-and-failures.md) | 进程存活与执行就绪是否混淆 |
| `grounding absent`／目标有歧义 | [分布式排障](../production/operations-and-failures.md)、[统一能力目标](../guides/unified-capability-goals.md) | 请求是否完整、观测是否新鲜、目标和场景是否匹配 |
| 定位过期、地图不匹配、语义版本冲突 | [点云语义指南](../guides/semantic-point-cloud.md)、[语义执行协议](../architecture/semantic-execution-protocol.md) | 实际地图、坐标、标定与冻结计划的版本；不要只改时间戳或强行沿用旧候选 |
| 目标待定、完成读取中断或结果未送达 | [目标运维状态表](delegated-goal-operations.md#3-按持久事实判断能否继续) | 区分可补认的已验证步骤、未知写入、只读完成重试和仅重传证明 |
| Agent 重启后任务中断 | [单机器人闭环与恢复](../development/single-robot-loop.md)、[分布式排障](../production/operations-and-failures.md) | 已确认步骤、可继续检查点和未知物理结果 |
| `PHYSICAL_OUTCOME_UNKNOWN`／`EXECUTION_OUTCOME_UNKNOWN` | [分布式排障](../production/operations-and-failures.md)、[Sim2Real 停止与恢复](../sim2real/README.md) | 命令是否已经可能产生物理效果；先对账，不能因超时自动重放 |
| 没有 Ops 诊断或恢复建议 | [Agent 运行时配置](agent-runtime-config.md) | 角色是否启用、事件是否发布、恢复是否被未知结果阻断 |
| Fleet 队列、租约、证书、World 或界面不同步 | [分布式排障](../production/operations-and-failures.md) | Task／事件、Runtime、World 和展示层各自的权威与新鲜度 |
| 实机急停、失联、停止后不能继续 | [物理安全检查](safety-checklist.md)、[Sim2Real 上手](../sim2real/README.md) | 现场安全、持物情况、锁存与显式复位；服务重启不是安全解锁 |

恢复时保留任务、观测和 Runtime journal；不要删除状态以消除冲突。语义导航的已完成阶段与未完成阶段有不同恢复条件，具体按执行协议处理，不能把“有检查点”理解成任意重启后都可继续运动。

## 6. 升级、回滚与实机放行

| 工作阶段 | 入口 | 应留下的交付物 |
| --- | --- | --- |
| 评估变更与兼容性 | [部署拓扑的升级与回滚](../production/deployment-and-capacity.md)、[数据契约](../production/data-contracts.md) | 代码、镜像、协议、目录、模型、地图与标定版本，以及受影响测试范围 |
| 冻结派发与备份 | [分布式故障与灾难恢复](../production/operations-and-failures.md)、[Gazebo 数据与资源](../guides/gazebo-default.md) | 关联的任务库／事件／World、完整 edge-context/edge-goal/edge-child 聚合、Runtime journal、地图与标定；SQLite 一致性备份，凭据按私有配置管理 |
| 验证候选并发布 | [测试与验收](../production/testing-and-acceptance.md)、[家庭发布清单](release-checklist.md) | 本次构建的测试结果、任务证据、失败处理、限制和回滚版本 |
| 新设备的现场集成 | [Sim2Real 上手与接入包](../sim2real/README.md)、[首次动作检查](../install/xlerobot-experiment.md) | 本设备配置、标定、观测、策略、停止测试和逐次任务记录 |
| 判断能否放行 | [生产就绪门禁](production-readiness.md)、[物理安全检查](safety-checklist.md) | 离线前置条件与实测现场结论分开，现场负责人签署限定范围 |

回滚同样要冻结派发、确认停止与持物，并保留未决命令；不能降低 fencing token、改写任务修订或删除 journal。模型、驱动、标定、安全限值和地图变化后，应重新运行受影响的验收。

[2026-09-26 现场就绪审计](../production/field-readiness-2026-09-26.md)、[V1 状态](../production/v1-release-status.md)、[发布记录](../releases/)用于查看对应时间的判断和证据；它们不会自动覆盖当前制品。一次仿真成功、30 次试验资料或一次长稳记录均不等于通用可靠性认证。目标硬件、限定任务、停止响应、容量和运维流程仍需各自的实测与交付结论。

空间地图采集、定位质量和控制器路径部署见 [空间建图](../development/spatial-mapping.md)与[空间导航](../development/spatial-navigation.md)。临时输出清理与原始证据保留见 [产物规则](artifact-retention.md)。

本机磁盘空间、Docker 盘满恢复和数据保留情况见 [2026-10-18 清理记录](2026-10-18-local-disk-cleanup.md)。
