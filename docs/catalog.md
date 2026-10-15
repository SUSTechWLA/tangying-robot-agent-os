# 全量分类目录

[文档导航](README.md) · [学习](learning/README.md) · [开发](development/README.md) · [运维](operations/README.md) · [研究](research/README.md)

本页由 `python3 scripts/build_docs_catalog.py` 从原文件标题和路径生成。分类表示用途，不自动认证内容仍然有效；以文内版本、源码和对应证据判断。新增资料后重新生成，使用 `--check` 检查是否遗漏。

覆盖 `docs/` 中除本页外的 **240 份 Markdown** 与 **207 份配套文件**；书籍通过[书籍目录](../book/README.md)进入，未重复列出历史版次和生成合订本。

## 分类总览

| 分类 | 文档数 | 阅读原则 |
| --- | ---: | --- |
| [导航与维护](#catalog-navigation) | 4 | 四条路线选择阅读顺序；本目录保留每份资料的原路径。 |
| [使用指南](#catalog-guides) | 15 | 查操作与功能；旧仿真案例按页面注明的引擎和版本阅读。 |
| [架构与协议](#catalog-architecture) | 29 | 查原理、组件边界和协议；设计设想及历史快照不等于当前交付。 |
| [开发参考与演进记录](#catalog-development) | 96 | 先读开发导航；带日期记录回答当时如何设计、实现和修复。 |
| [部署与运维流程](#catalog-operations) | 10 | 查角色、启动、故障、升级及放行条件。 |
| [分角色安装](#catalog-install) | 9 | 按目标机器选择；旧 Cloud 控制面文档保留为历史安装资料。 |
| [运行手册、接口与验收快照](#catalog-production) | 18 | 参考文档与历史审计并存；不能把旧状态页当作当前整仓认证。 |
| [实体机器人接入](#catalog-sim2real) | 1 | 软件接口、离线前置检查和现场物理验收分别核对。 |
| [工作台与交互](#catalog-frontend) | 1 | 查用户流程、开发诊断及界面资料。 |
| [实验、预登记与复验](#catalog-experiments) | 33 | 区分研究对照、工程案例和工具说明；结论以原报告的环境、版本和限制为准。 |
| [软件发布记录](#catalog-releases) | 6 | 记录指定版本的发布身份，不覆盖后续分支改动。 |
| [原始设计档案](#catalog-superpowers) | 18 | 按原日期理解决策背景；计划项不作为已实现或已验收的证明。 |

<a id="catalog-navigation"></a>

## 导航与维护

四条路线选择阅读顺序；本目录保留每份资料的原路径。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [文档导航：学习、开发、运维与研究](README.md) | `README.md` | 导航 / 维护 |
| [学习路线：从系统全貌到可检查实践](learning/README.md) | `learning/README.md` | 导航 / 维护 |
| [文档分类与维护规则](maintenance.md) | `maintenance.md` | 导航 / 维护 |
| [研究与论文资料导航](research/README.md) | `research/README.md` | 导航 / 维护 |

<a id="catalog-guides"></a>

## 使用指南

查操作与功能；旧仿真案例按页面注明的引擎和版本阅读。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [云端与本地目标委托接入指南](guides/delegated-edge-goals.md) | `guides/delegated-edge-goals.md` | 操作 / 接口参考 |
| [装修家庭场景与完整任务验收](guides/furnished-home-demo.md) | `guides/furnished-home-demo.md` | 操作 / 接口参考 |
| [Gazebo：XLeRobot 同源完整家庭](guides/gazebo-default.md) | `guides/gazebo-default.md` | 操作 / 接口参考 |
| [Gazebo 家庭场景操作](guides/gazebo-house-operations.md) | `guides/gazebo-house-operations.md` | 操作 / 接口参考 |
| [实机接入 AgentOS：原理、异构机构与完整交付流程](guides/hardware-agent-integration.md) | `guides/hardware-agent-integration.md` | 操作 / 接口参考 |
| [四房间家庭场景与移动抓取操作指南](guides/home-scene-operations.md) | `guides/home-scene-operations.md` | 操作 / 接口参考 |
| [家庭场景 Sim2Real 接入](guides/home-sim2real.md) | `guides/home-sim2real.md` | 操作 / 接口参考 |
| [一句话到机器人执行：沿一条真实任务看完整闭环](guides/natural-language-task-lifecycle.md) | `guides/natural-language-task-lifecycle.md` | 操作 / 接口参考 |
| [仿真开发快速上手](guides/quickstart.md) | `guides/quickstart.md` | 历史安装 / 回归入口 |
| [机器人服务、标定与建图工作流](guides/robot-service-workflow.md) | `guides/robot-service-workflow.md` | 操作 / 接口参考 |
| [三维点云语义地图：从标注到长程导航与现场复核](guides/semantic-point-cloud.md) | `guides/semantic-point-cloud.md` | 操作 / 接口参考 |
| [SLAM 关键帧检查](guides/slam-keyframe-inspection.md) | `guides/slam-keyframe-inspection.md` | 操作 / 接口参考 |
| [SLAM 地图、语义地点与自然语言导航](guides/slam-semantic-navigation.md) | `guides/slam-semantic-navigation.md` | 操作 / 接口参考 |
| [统一能力目标：标定、SLAM 与机器人任务](guides/unified-capability-goals.md) | `guides/unified-capability-goals.md` | 操作 / 接口参考 |
| [用户端机器人工作台](guides/user-console.md) | `guides/user-console.md` | 操作 / 接口参考 |

<a id="catalog-architecture"></a>

## 架构与协议

查原理、组件边界和协议；设计设想及历史快照不等于当前交付。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [Agent 系统能力评测：分层、可归因、可比较的 Eval 体系](architecture/agent-evaluation-system.md) | `architecture/agent-evaluation-system.md` | 架构 / 协议 / 设计参考 |
| [Agent 事件规范](architecture/agent-events.md) | `architecture/agent-events.md` | 架构 / 协议 / 设计参考 |
| [V1 Agent 与 Sim2Real 契约](architecture/agent-v1.md) | `architecture/agent-v1.md` | 架构 / 协议 / 设计参考 |
| [当前系统架构](architecture/architecture.md) | `architecture/architecture.md` | 架构 / 协议 / 设计参考 |
| [分布式 AgentOS：实现范围与成熟度](architecture/distributed-agentos.md) | `architecture/distributed-agentos.md` | 架构 / 协议 / 设计参考 |
| [Fleet 云端控制平面（分布式 AgentOS 部署画像）](architecture/fleet-cloud.md) | `architecture/fleet-cloud.md` | 架构 / 协议 / 设计参考 |
| [早期 Fleet 论文闭环实验档案](architecture/fleet-paper-loop.md) | `architecture/fleet-paper-loop.md` | 架构 / 协议 / 设计参考 |
| [分层目标委托：云端目标与统一边缘 Agent](architecture/hierarchical-edge-objectives.md) | `architecture/hierarchical-edge-objectives.md` | 架构 / 协议 / 设计参考 |
| [生命周期对象：一次家庭任务里到底有几种"东西"在各自走状态](architecture/lifecycle-objects.md) | `architecture/lifecycle-objects.md` | 架构 / 协议 / 设计参考 |
| [LLM 驱动的执行：让模型在执行过程中选工具与编排](architecture/llm-driven-execution.md) | `architecture/llm-driven-execution.md` | 架构 / 协议 / 设计参考 |
| [长程任务、多 Agent 通讯与异构机器人协作](architecture/long-horizon-collaboration.md) | `architecture/long-horizon-collaboration.md` | 架构 / 协议 / 设计参考 |
| [Middleware 端口与适配器指南](architecture/middleware.md) | `architecture/middleware.md` | 架构 / 协议 / 设计参考 |
| [模块化与扩展性：云端大脑、边缘 Agent 与异构机器人](architecture/modular-extensibility.md) | `architecture/modular-extensibility.md` | 架构 / 协议 / 设计参考 |
| [模块化机器人的故障上报与自愈设计（XLeRobot：底盘 / 机械臂 / 上位机 …）](architecture/module-health-and-faults.md) | `architecture/module-health-and-faults.md` | 架构 / 协议 / 设计参考 |
| [多 Agent 运行时](architecture/multi-agent-runtime.md) | `architecture/multi-agent-runtime.md` | 架构 / 协议 / 设计参考 |
| [多机器人协调](architecture/multi-robot.md) | `architecture/multi-robot.md` | 架构 / 协议 / 设计参考 |
| [编排层的后训练：先量，再蒸，最后才谈强化学习](architecture/orchestration-post-training.md) | `architecture/orchestration-post-training.md` | 架构 / 协议 / 设计参考 |
| [LLM 编排能力、指标与提升方法](architecture/orchestration.md) | `architecture/orchestration.md` | 架构 / 协议 / 设计参考 |
| [后训练流水线：不用 TrainAgent，用什么](architecture/post-training-pipeline.md) | `architecture/post-training-pipeline.md` | 架构 / 协议 / 设计参考 |
| [Robot Runtime 协议不变量](architecture/protocols.md) | `architecture/protocols.md` | 架构 / 协议 / 设计参考 |
| [可用性自检：机器现在到底能不能用](architecture/readiness.md) | `architecture/readiness.md` | 架构 / 协议 / 设计参考 |
| [恢复 Agent（RecoveryAgent）：调查、提议、转人工](architecture/recovery-agent.md) | `architecture/recovery-agent.md` | 架构 / 协议 / 设计参考 |
| [Review Agent 运行原理：发现问题、定位问题、以及"不做什么"](architecture/review-agent.md) | `architecture/review-agent.md` | 架构 / 协议 / 设计参考 |
| [机器人自动发现：上电联网就能被找到](architecture/robot-discovery.md) | `architecture/robot-discovery.md` | 架构 / 协议 / 设计参考 |
| [机器人配对：不需要 SSH 的一键接入](architecture/robot-pairing.md) | `architecture/robot-pairing.md` | 架构 / 协议 / 设计参考 |
| [点云语义到长程任务的交互协议](architecture/semantic-execution-protocol.md) | `architecture/semantic-execution-protocol.md` | 架构 / 协议 / 设计参考 |
| [监督 Agent：故障矩阵与可回溯验证](architecture/supervision-verification.md) | `architecture/supervision-verification.md` | 架构 / 协议 / 设计参考 |
| [TrainAgent 可行性评估：哪些"一切皆 Agent"成立，哪些不成立](architecture/train-agent-assessment.md) | `architecture/train-agent-assessment.md` | 架构 / 协议 / 设计参考 |
| [为什么是分层的，以及"分布式"不是重点](architecture/why-distributed.md) | `architecture/why-distributed.md` | 架构 / 协议 / 设计参考 |

<a id="catalog-development"></a>

## 开发参考与演进记录

先读开发导航；带日期记录回答当时如何设计、实现和修复。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [家庭演示与已保存地图浏览升级](development/2026-09-13-demo-map-upgrade-plan.md) | `development/2026-09-13-demo-map-upgrade-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [装修家庭场景验收记录](development/2026-09-13-furnished-home-acceptance.md) | `development/2026-09-13-furnished-home-acceptance.md` | 实施 / 设计 / 复盘（按文内版本） |
| [注册机器人服务与完整工作流验收](development/2026-09-13-robot-workflow-acceptance.md) | `development/2026-09-13-robot-workflow-acceptance.md` | 实施 / 设计 / 复盘（按文内版本） |
| [通用移动操作 Agent：系统审查与升级记录](development/2026-09-13-system-audit.md) | `development/2026-09-13-system-audit.md` | 实施 / 设计 / 复盘（按文内版本） |
| [实验报告已归档](development/2026-09-14-semantic-object-layer-upgrade.md) | `development/2026-09-14-semantic-object-layer-upgrade.md` | 迁移指针（保留旧引用） |
| [AI 回溯诊断与自动运维闭环：现状能力、缺口与设计](development/2026-09-15-ai-incident-diagnosis-and-ops-loop.md) | `development/2026-09-15-ai-incident-diagnosis-and-ops-loop.md` | 实施 / 设计 / 复盘（按文内版本） |
| [可扩展性评审：工具封装与导航受阻（v1 不动结构，只留接口）](development/2026-09-15-extensibility-review-tools-and-navigation.md) | `development/2026-09-15-extensibility-review-tools-and-navigation.md` | 实施 / 设计 / 复盘（按文内版本） |
| [实验报告已归档](development/2026-09-15-latency-and-recall-goal-upgrade.md) | `development/2026-09-15-latency-and-recall-goal-upgrade.md` | 迁移指针（保留旧引用） |
| [系统评价与重点优化方向（每项附对比实验设计）](development/2026-09-15-optimization-backlog.md) | `development/2026-09-15-optimization-backlog.md` | 实施 / 设计 / 复盘（按文内版本） |
| [PLACEMENT_NOT_OBSERVED 的定位、验证与两处修复](development/2026-09-15-placement-verification-and-certified-goals.md) | `development/2026-09-15-placement-verification-and-certified-goals.md` | 实施 / 设计 / 复盘（按文内版本） |
| [机器人异常处理审计：能不能恢复、异常有没有入表](development/2026-09-15-robot-fault-handling-audit.md) | `development/2026-09-15-robot-fault-handling-audit.md` | 实施 / 设计 / 复盘（按文内版本） |
| [实验报告已归档](development/2026-09-15-slam-exploration-coverage-upgrade.md) | `development/2026-09-15-slam-exploration-coverage-upgrade.md` | 迁移指针（保留旧引用） |
| [实验报告已归档](development/2026-09-15-survey-lookback-and-pre-position.md) | `development/2026-09-15-survey-lookback-and-pre-position.md` | 迁移指针（保留旧引用） |
| [硬件故障发布成观测：从"报出来"到"摘能力、进世界状态"](development/2026-09-16-faults-as-observations.md) | `development/2026-09-16-faults-as-observations.md` | 实施 / 设计 / 复盘（按文内版本） |
| [仓库整理：文档、证据、宣传与代码各归其位](development/2026-09-16-repository-organization.md) | `development/2026-09-16-repository-organization.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-09-17 哑掉的观察者与漂移的身份：四个只有在真机上跑才会暴露的缺陷](development/2026-09-17-mute-observer-and-plan-identity.md) | `development/2026-09-17-mute-observer-and-plan-identity.md` | 实施 / 设计 / 复盘（按文内版本） |
| [监督能力的三个盲区：怎么发现的、怎么修的、以及我犯的错](development/2026-09-17-supervision-blind-spots.md) | `development/2026-09-17-supervision-blind-spots.md` | 实施 / 设计 / 复盘（按文内版本） |
| [系统评价与完善计划：多 Agent 分布式机器人 AgentOS](development/2026-09-18-system-review-and-improvement-plan.md) | `development/2026-09-18-system-review-and-improvement-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [让 Agent 真正能"探索环境"：一个工具，而不是一串服务调用](development/2026-09-19-agent-initiated-mapping.md) | `development/2026-09-19-agent-initiated-mapping.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo 作为机器人后端：已完成什么、还差什么](development/2026-09-19-gazebo-backend-status.md) | `development/2026-09-19-gazebo-backend-status.md` | 实施 / 设计 / 复盘（按文内版本） |
| [SLAM 分层：谁是权威、接缝在哪、怎么单独对比](development/2026-09-19-slam-layering-and-replay.md) | `development/2026-09-19-slam-layering-and-replay.md` | 实施 / 设计 / 复盘（按文内版本） |
| [地图看不见：三个同样的「只找一层」假设，以及一次地图清理](development/2026-09-21-map-visibility-and-inventory.md) | `development/2026-09-21-map-visibility-and-inventory.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo XLeRobot 家庭闭环升级规范](development/2026-09-26-gazebo-xlerobot-home-spec.md) | `development/2026-09-26-gazebo-xlerobot-home-spec.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-09-27 Agent 能力架构审计与修订记录](development/2026-09-27-agent-capability-architecture-review.md) | `development/2026-09-27-agent-capability-architecture-review.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Book 1.1.0当前架构同步报告](development/2026-09-27-book-current-architecture-review.md) | `development/2026-09-27-book-current-architecture-review.md` | 实施 / 设计 / 复盘（按文内版本） |
| [能力操作取消与进度回执竞态修复](development/2026-09-27-capability-cancel-ack-race.md) | `development/2026-09-27-capability-cancel-ack-race.md` | 实施 / 设计 / 复盘（按文内版本） |
| [统一能力目标实施规格（2026-09-27）](development/2026-09-27-capability-goal-implementation-spec.md) | `development/2026-09-27-capability-goal-implementation-spec.md` | 实施 / 设计 / 复盘（按文内版本） |
| [前端 UX 审核与验证记录（2026-09-27）](development/2026-09-27-console-ux-review.md) | `development/2026-09-27-console-ux-review.md` | 实施 / 设计 / 复盘（按文内版本） |
| [工作台 UX 升级规范（2026-09-27）](development/2026-09-27-console-ux-spec.md) | `development/2026-09-27-console-ux-spec.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo SLAM → 语义地点 → 自然语言导航升级规范](development/2026-09-27-gazebo-slam-semantic-navigation-spec.md) | `development/2026-09-27-gazebo-slam-semantic-navigation-spec.md` | 实施 / 设计 / 复盘（按文内版本） |
| [实机与异构机器人接入指南：复核与修订记录](development/2026-09-27-hardware-integration-guide-review.md) | `development/2026-09-27-hardware-integration-guide-review.md` | 实施 / 设计 / 复盘（按文内版本） |
| [GOAL Agent 的逐轮语义读取与复杂任务闭环规范（2026-09-28）](development/2026-09-28-model-led-goal-semantic-views-spec.md) | `development/2026-09-28-model-led-goal-semantic-views-spec.md` | 实施 / 设计 / 复盘（按文内版本） |
| [长程任务与多 Agent 故障闭环更新（2026-10-05）](development/2026-10-05-long-horizon-recovery.md) | `development/2026-10-05-long-horizon-recovery.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-05 真机接入的软件升级与验证边界](development/2026-10-05-physical-robot-integration.md) | `development/2026-10-05-physical-robot-integration.md` | 实施 / 设计 / 复盘（按文内版本） |
| [长程任务上下文升级计划与验收](development/2026-10-09-long-horizon-context-plan.md) | `development/2026-10-09-long-horizon-context-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [长程任务：云边上下文、确定性压缩与恢复检查点](development/2026-10-09-long-horizon-context.md) | `development/2026-10-09-long-horizon-context.md` | 实施 / 设计 / 复盘（按文内版本） |
| [多 Agent 事件与恢复交接协议（2026-10-10）](development/2026-10-10-agent-interaction-protocol.md) | `development/2026-10-10-agent-interaction-protocol.md` | 实施 / 设计 / 复盘（按文内版本） |
| [云边完成回执与重连边界（2026-10-10）](development/2026-10-10-cloud-edge-protocol.md) | `development/2026-10-10-cloud-edge-protocol.md` | 实施 / 设计 / 复盘（按文内版本） |
| [335 条真实事件后的恢复上下文预算修复](development/2026-10-10-context-budget-recovery.md) | `development/2026-10-10-context-budget-recovery.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Retaining failed GOAL planning evidence](development/2026-10-10-failed-goal-evidence.md) | `development/2026-10-10-failed-goal-evidence.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo feedback isolation after main run 004](development/2026-10-10-gazebo-feedback-isolation.md) | `development/2026-10-10-gazebo-feedback-isolation.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo 抓取接近路径预检修复（2026-10-10）](development/2026-10-10-gazebo-grasp-approach.md) | `development/2026-10-10-gazebo-grasp-approach.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo grasp target identity after an empty-arm approach](development/2026-10-10-gazebo-grasp-identity.md) | `development/2026-10-10-gazebo-grasp-identity.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo navigation: bounded waiting with zero velocity](development/2026-10-10-gazebo-navigation-hold.md) | `development/2026-10-10-gazebo-navigation-hold.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo held-payload rim clearance](development/2026-10-10-gazebo-payload-clearance.md) | `development/2026-10-10-gazebo-payload-clearance.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo sensor readiness clock snapshot correction — 2026-10-10](development/2026-10-10-gazebo-readiness-race.md) | `development/2026-10-10-gazebo-readiness-race.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo survey observation wait and diagnostic evidence](development/2026-10-10-gazebo-survey-readiness.md) | `development/2026-10-10-gazebo-survey-readiness.md` | 实施 / 设计 / 复盘（按文内版本） |
| [GOAL 规划收敛边界修复（2026-10-10）](development/2026-10-10-goal-planning-convergence.md) | `development/2026-10-10-goal-planning-convergence.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Household pickup verb complement](development/2026-10-10-home-pickup-complement.md) | `development/2026-10-10-home-pickup-complement.md` | 实施 / 设计 / 复盘（按文内版本） |
| [单自然语言长程任务与多 Agent 协议验收计划](development/2026-10-10-long-horizon-plan.md) | `development/2026-10-10-long-horizon-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Durable rejected planning attempts](development/2026-10-10-planning-attempt-store.md) | `development/2026-10-10-planning-attempt-store.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-10：长程任务历史下的巡检成本与持久调查去重](development/2026-10-10-recovery-observation-cost.md) | `development/2026-10-10-recovery-observation-cost.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Sensor admission deadline boundary](development/2026-10-10-sensor-admission-deadline.md) | `development/2026-10-10-sensor-admission-deadline.md` | 实施 / 设计 / 复盘（按文内版本） |
| [数字版1.2.0：当前系统逐章审校与学习资料更新](development/2026-10-11-book-current-system-review.md) | `development/2026-10-11-book-current-system-review.md` | 实施 / 设计 / 复盘（按文内版本） |
| [数字版 2.0.0：教材与工程参考版编辑审校](development/2026-10-11-book-textbook-editorial-review.md) | `development/2026-10-11-book-textbook-editorial-review.md` | 实施 / 设计 / 复盘（按文内版本） |
| [v0.7.0 协作机制文档修订记录](development/2026-10-11-collaboration-docs-review.md) | `development/2026-10-11-collaboration-docs-review.md` | 实施 / 设计 / 复盘（按文内版本） |
| [文档分类、学习路线与资料取用整理](development/2026-10-13-documentation-map.md) | `development/2026-10-13-documentation-map.md` | 实施 / 设计 / 复盘（按文内版本） |
| [边缘 Agent 实机测试候选版实施记录](development/2026-10-13-edge-field-candidate.md) | `development/2026-10-13-edge-field-candidate.md` | 实施 / 设计 / 复盘（按文内版本） |
| [云边与 SLAM 模块边界升级记录](development/2026-10-13-modular-extensibility.md) | `development/2026-10-13-modular-extensibility.md` | 实施 / 设计 / 复盘（按文内版本） |
| [SLAM 三维点云语义层实施计划](development/2026-10-13-semantic-cloud-plan.md) | `development/2026-10-13-semantic-cloud-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [点云语义到长程导航：实施计划与评估边界](development/2026-10-13-semantic-execution-plan.md) | `development/2026-10-13-semantic-execution-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [当前文档、产物清理与书籍 2.1.0 审校](development/2026-10-14-book-and-maintenance-review.md) | `development/2026-10-14-book-and-maintenance-review.md` | 实施 / 设计 / 复盘（按文内版本） |
| [产物治理、当前文档与书籍 2.1.0 更新计划](development/2026-10-14-maintenance-and-book-plan.md) | `development/2026-10-14-maintenance-and-book-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [总 README 新手导览更新](development/2026-10-14-readme-onboarding.md) | `development/2026-10-14-readme-onboarding.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2D、地形与 3D 空间导航升级记录](development/2026-10-14-spatial-navigation-plan.md) | `development/2026-10-14-spatial-navigation-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-15 分层目标委托与统一边缘核心升级记录](development/2026-10-15-hierarchical-edge-objectives.md) | `development/2026-10-15-hierarchical-edge-objectives.md` | 实施 / 设计 / 复盘（按文内版本） |
| [单机器人长程鲁棒性与实验留存](development/2026-10-15-single-robot-robustness.md) | `development/2026-10-15-single-robot-robustness.md` | 实施 / 设计 / 复盘（按文内版本） |
| [开发与升级导航](development/README.md) | `development/README.md` | 导航 / 维护 |
| [如何新增一个 Agent](development/adding-an-agent.md) | `development/adding-an-agent.md` | 开发参考 / 方案（核对文内状态） |
| [MoveIt 2 机械臂适配契约](development/arm-moveit-adapter.md) | `development/arm-moveit-adapter.md` | 开发参考 / 方案（核对文内状态） |
| [分支与发布规范](development/branching.md) | `development/branching.md` | 开发参考 / 方案（核对文内状态） |
| [标定与建图的 ROS 方案与精度门禁](development/calibration-slam-ros-plan.md) | `development/calibration-slam-ros-plan.md` | 开发参考 / 方案（核对文内状态） |
| [决策上下文生产者字段字典](development/decision-context-fields.md) | `development/decision-context-fields.md` | 开发参考 / 方案（核对文内状态） |
| [稠密地图：构建、部署与运维](development/dense-map-operations.md) | `development/dense-map-operations.md` | 开发参考 / 方案（核对文内状态） |
| [稠密地图浏览器查看器：升级方案与任务拆解](development/dense-map-viewer-plan.md) | `development/dense-map-viewer-plan.md` | 开发参考 / 方案（核对文内状态） |
| [边缘 Agent 数据交换与云端对接](development/edge-data-exchange.md) | `development/edge-data-exchange.md` | 开发参考 / 方案（核对文内状态） |
| [边缘 Worker 的持久接收、运行契约与退出语义](development/edge-worker-reliability.md) | `development/edge-worker-reliability.md` | 开发参考 / 方案（核对文内状态） |
| [开发者快速上手](development/getting-started.md) | `development/getting-started.md` | 开发参考 / 方案（核对文内状态） |
| [无人车、无人机与足式机器人的边缘接入合同](development/heterogeneous-mobility.md) | `development/heterogeneous-mobility.md` | 开发参考 / 方案（核对文内状态） |
| [家居场景扩充：从 1 个物体到完整任务（实施方案）](development/home-scene-expansion-plan.md) | `development/home-scene-expansion-plan.md` | 开发参考 / 方案（核对文内状态） |
| [MuJoCo 版本与兼容检查](development/mujoco-compatibility.md) | `development/mujoco-compatibility.md` | 开发参考 / 方案（核对文内状态） |
| [躺营自然语言 Agent 评测与改进](development/natural-language-evaluation.md) | `development/natural-language-evaluation.md` | 开发参考 / 方案（核对文内状态） |
| [任务观测证据与历史回看](development/observation-evidence.md) | `development/observation-evidence.md` | 开发参考 / 方案（核对文内状态） |
| [开发原则与代码地图](development/principles.md) | `development/principles.md` | 开发参考 / 方案（核对文内状态） |
| [Real2Sim：用机器人自己的 SLAM 建图生成仿真场景](development/real2sim-from-robot-slam.md) | `development/real2sim-from-robot-slam.md` | 开发参考 / 方案（核对文内状态） |
| [RGB-D 相机：底部相机位置修正（已实施）](development/rgbd-camera-fix.md) | `development/rgbd-camera-fix.md` | 开发参考 / 方案（核对文内状态） |
| [接入不同结构和传感器的机器人](development/robot-adapters.md) | `development/robot-adapters.md` | 开发参考 / 方案（核对文内状态） |
| [整机标定：robot.calibration.v1](development/robot-calibration.md) | `development/robot-calibration.md` | 开发参考 / 方案（核对文内状态） |
| [机器人工具层](development/robot-tool-layer.md) | `development/robot-tool-layer.md` | 开发参考 / 方案（核对文内状态） |
| [ROS 2 RGB-D 接入](development/ros2-rgbd.md) | `development/ros2-rgbd.md` | 开发参考 / 方案（核对文内状态） |
| [RTAB-Map / Nav2 导航与双 RGB-D](development/rtabmap-navigation.md) | `development/rtabmap-navigation.md` | 开发参考 / 方案（核对文内状态） |
| [实机前置工作：标定与建图的前端方案](development/sim2real-onboarding-frontend.md) | `development/sim2real-onboarding-frontend.md` | 开发参考 / 方案（核对文内状态） |
| [单机器人 RGB-D 闭环：代码、数据与恢复](development/single-robot-loop.md) | `development/single-robot-loop.md` | 开发参考 / 方案（核对文内状态） |
| [扩展 SLAM Provider](development/slam-providers.md) | `development/slam-providers.md` | 开发参考 / 方案（核对文内状态） |
| [外部 SE3 测量接入与空间地图包](development/spatial-mapping.md) | `development/spatial-mapping.md` | 开发参考 / 方案（核对文内状态） |
| [统一 2D、地形与 3D 导航](development/spatial-navigation.md) | `development/spatial-navigation.md` | 开发参考 / 方案（核对文内状态） |

<a id="catalog-operations"></a>

## 部署与运维流程

查角色、启动、故障、升级及放行条件。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [部署、运维与现场交付导航](operations/README.md) | `operations/README.md` | 导航 / 维护 |
| [Agent 运行时配置](operations/agent-runtime-config.md) | `operations/agent-runtime-config.md` | 操作 / 接口参考 |
| [产物清理、保留与实验复核](operations/artifact-retention.md) | `operations/artifact-retention.md` | 操作 / 接口参考 |
| [部署目标与代码归属](operations/deployment.md) | `operations/deployment.md` | 操作 / 接口参考 |
| [边缘 Agent 实机测试部署](operations/edge-field-testing.md) | `operations/edge-field-testing.md` | 操作 / 接口参考 |
| [全新部署：新机器 / 新机器人 / 新云服务器](operations/fresh-deployment.md) | `operations/fresh-deployment.md` | 操作 / 接口参考 |
| [XLeRobot 生产就绪判定](operations/production-readiness.md) | `operations/production-readiness.md` | 操作 / 接口参考 |
| [家庭场景发布验收清单](operations/release-checklist.md) | `operations/release-checklist.md` | 操作 / 接口参考 |
| [RoboCasa 双机器人交接与实机迁移](operations/robocasa-handoff.md) | `operations/robocasa-handoff.md` | 操作 / 接口参考 |
| [Physical hardware safety checklist](operations/safety-checklist.md) | `operations/safety-checklist.md` | 操作 / 接口参考 |

<a id="catalog-install"></a>

## 分角色安装

按目标机器选择；旧 Cloud 控制面文档保留为历史安装资料。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [阿里云 Fleet 部署](install/alicloud-cloud.md) | `install/alicloud-cloud.md` | 操作 / 接口参考 |
| [云端安装、启动与恢复（历史归档）](install/cloud.md) | `install/cloud.md` | 历史安装 / 回归入口 |
| [Orin NX：单机器人边缘 Agent 与 Fleet Worker](install/edge-orin.md) | `install/edge-orin.md` | 操作 / 接口参考 |
| [笔记本 Local Agent 安装、配对与恢复](install/local.md) | `install/local.md` | 操作 / 接口参考 |
| [树莓派快捷实机部署](install/robot-pi-quick.md) | `install/robot-pi-quick.md` | 操作 / 接口参考 |
| [树莓派 Robot Edge 安装与硬件准备](install/robot-pi.md) | `install/robot-pi.md` | 操作 / 接口参考 |
| [统一故障排查](install/troubleshooting.md) | `install/troubleshooting.md` | 操作 / 接口参考 |
| [XLeRobot 实验前检查与首次动作](install/xlerobot-experiment.md) | `install/xlerobot-experiment.md` | 操作 / 接口参考 |
| [XLeRobot 集成边界](install/xlerobot-setup.md) | `install/xlerobot-setup.md` | 操作 / 接口参考 |

<a id="catalog-production"></a>

## 运行手册、接口与验收快照

参考文档与历史审计并存；不能把旧状态页当作当前整仓认证。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [躺营 · Tangying Robot AgentOS 部署与运维手册](production/README.md) | `production/README.md` | 导航 / 维护 |
| [角色 Harness 与统一 Docker 镜像：软件验收记录](production/agent-harness-docker-acceptance.md) | `production/agent-harness-docker-acceptance.md` | 验收 / 就绪快照 |
| [全部接口参考](production/api-reference.md) | `production/api-reference.md` | 操作 / 接口参考 |
| [完整系统架构](production/architecture.md) | `production/architecture.md` | 操作 / 接口参考 |
| [云端机群与 Orin NX 边缘升级：软件预认证记录](production/cloud-edge-upgrade-acceptance.md) | `production/cloud-edge-upgrade-acceptance.md` | 验收 / 就绪快照 |
| [配置与安全手册](production/configuration-and-security.md) | `production/configuration-and-security.md` | 操作 / 接口参考 |
| [数据契约与一致性语义](production/data-contracts.md) | `production/data-contracts.md` | 操作 / 接口参考 |
| [部署拓扑、容量与可观测性](production/deployment-and-capacity.md) | `production/deployment-and-capacity.md` | 操作 / 接口参考 |
| [2026-09-26 生产就绪审计与实机验证交接](production/field-readiness-2026-09-26.md) | `production/field-readiness-2026-09-26.md` | 验收 / 就绪快照 |
| [分布式异常与快速排障手册](production/operations-and-failures.md) | `production/operations-and-failures.md` | 操作 / 接口参考 |
| [学习型策略工具与 sim2real 接入手册](production/policy-tools.md) | `production/policy-tools.md` | 操作 / 接口参考 |
| [从零开始快速上手](production/quickstart.md) | `production/quickstart.md` | 操作 / 接口参考 |
| [历史发布验证证据：v0.2.0-rc.2（2026-08-24）](production/release-evidence.md) | `production/release-evidence.md` | 验收 / 就绪快照 |
| [从 RoboCasa 仿真迁移到机器人实机](production/sim-to-real.md) | `production/sim-to-real.md` | 操作 / 接口参考 |
| [单机器人 V1：用户流程与上线准备](production/single-robot-v1.md) | `production/single-robot-v1.md` | 操作 / 接口参考 |
| [测试、签名验收与发布检查](production/testing-and-acceptance.md) | `production/testing-and-acceptance.md` | 验收 / 就绪快照 |
| [V1 架构评估与实机接入检查（2026-09-05）](production/v1-assessment-2026-09-05.md) | `production/v1-assessment-2026-09-05.md` | 验收 / 就绪快照 |
| [V1 当前交付状态](production/v1-release-status.md) | `production/v1-release-status.md` | 验收 / 就绪快照 |

<a id="catalog-sim2real"></a>

## 实体机器人接入

软件接口、离线前置检查和现场物理验收分别核对。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [购买 XLeRobot 后，怎样接入躺营系统](sim2real/README.md) | `sim2real/README.md` | 导航 / 维护 |

<a id="catalog-frontend"></a>

## 工作台与交互

查用户流程、开发诊断及界面资料。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [躺营机器人工作台：用户界面与开发诊断](frontend/console-v1.md) | `frontend/console-v1.md` | 操作 / 接口参考 |

<a id="catalog-experiments"></a>

## 实验、预登记与复验

区分研究对照、工程案例和工具说明；结论以原报告的环境、版本和限制为准。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [语义层升级：物体记忆（map.objects.v1）与工作区可达位姿](experiments/2026-09-14-semantic-object-layer-upgrade.md) | `experiments/2026-09-14-semantic-object-layer-upgrade.md` | 实验 / 验收记录 |
| [P0-1 / P0-2 升级：上机性能遥测 + 目的地改用"上次看到它的地方"](experiments/2026-09-15-latency-and-recall-goal-upgrade.md) | `experiments/2026-09-15-latency-and-recall-goal-upgrade.md` | 实验 / 验收记录 |
| [SLAM 探索：为什么"很多房间进不去"，以及怎么改成能扫完整屋](experiments/2026-09-15-slam-exploration-coverage-upgrade.md) | `experiments/2026-09-15-slam-exploration-coverage-upgrade.md` | 实验 / 验收记录 |
| [三项收口：勘测回望、任务前置摆位、新鲜度成为部署参数](experiments/2026-09-15-survey-lookback-and-pre-position.md) | `experiments/2026-09-15-survey-lookback-and-pre-position.md` | 实验 / 验收记录 |
| [探索模块能不能靠"看得见多少"变好：三个被数字否掉的假设](experiments/2026-09-19-exploration-importance-and-coverage.md) | `experiments/2026-09-19-exploration-importance-and-coverage.md` | 实验 / 验收记录 |
| [机器人自主探索的鲁棒性优化：被安全层拒绝之后怎么办](experiments/2026-09-19-exploration-robustness-experiment.md) | `experiments/2026-09-19-exploration-robustness-experiment.md` | 实验 / 验收记录 |
| [SLAM 地图覆盖率：一次被数据推翻的优化方向](experiments/2026-09-19-slam-coverage-investigation.md) | `experiments/2026-09-19-slam-coverage-investigation.md` | 实验 / 验收记录 |
| [Gazebo 里的自主探索：把"可观测"变成"可驱动"，并量出两个仿真器的差距](experiments/2026-09-20-gazebo-exploration-coverage.md) | `experiments/2026-09-20-gazebo-exploration-coverage.md` | 实验 / 验收记录 |
| [语义 SLAM 地图的表达方式与自然语言导航：原理、对比实验与结论](experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md) | `experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md` | 实验 / 验收记录 |
| [移动机器人自主探索：跨仿真器的覆盖率研究](experiments/2026-09-20-slam-exploration-experiment-report.md) | `experiments/2026-09-20-slam-exploration-experiment-report.md` | 实验 / 验收记录 |
| [Agent 上下文表达与能力评测实验报告](experiments/2026-09-21-agent-context-evaluation.md) | `experiments/2026-09-21-agent-context-evaluation.md` | 实验 / 验收记录 |
| [物理接地验证框架：Gazebo 接触实验与配对回放报告](experiments/2026-09-21-grounded-verification.md) | `experiments/2026-09-21-grounded-verification.md` | 实验 / 验收记录 |
| [Gazebo 默认引擎迁移与分层验收](experiments/2026-09-22-gazebo-default.md) | `experiments/2026-09-22-gazebo-default.md` | 实验 / 验收记录 |
| [Gazebo Harmonic 稳定版本、吸附工位与系统闭环验收](experiments/2026-09-22-gazebo-stable-integration.md) | `experiments/2026-09-22-gazebo-stable-integration.md` | 实验 / 验收记录 |
| [Gazebo 同源 XLeRobot 家庭闭环记录](experiments/2026-09-26-gazebo-xlerobot-home-closure.md) | `experiments/2026-09-26-gazebo-xlerobot-home-closure.md` | 实验 / 验收记录 |
| [统一能力目标升级与闭环验收（2026-09-27）](experiments/2026-09-27-capability-goal-closure.md) | `experiments/2026-09-27-capability-goal-closure.md` | 实验 / 验收记录 |
| [Gazebo SLAM 与语义地点导航验收](experiments/2026-09-27-gazebo-slam-semantic-navigation.md) | `experiments/2026-09-27-gazebo-slam-semantic-navigation.md` | 实验 / 验收记录 |
| [复杂自然语言目标、语义状态与 Gazebo 闭环诊断（2026-09-28）](experiments/2026-09-28-complex-model-led-goals.md) | `experiments/2026-09-28-complex-model-led-goals.md` | 实验 / 验收记录 |
| [Gazebo 自然语言能力目标矩阵与故障回溯（2026-09-28）](experiments/2026-09-28-natural-language-goal-matrix.md) | `experiments/2026-09-28-natural-language-goal-matrix.md` | 实验 / 验收记录 |
| [Gazebo 长程任务、多 Agent 诊断与续跑实测（2026-10-05）](experiments/2026-10-05-long-horizon-recovery.md) | `experiments/2026-10-05-long-horizon-recovery.md` | 实验 / 验收记录 |
| [长程单句任务与多 Agent 协议实测](experiments/2026-10-10-long-horizon-protocol.md) | `experiments/2026-10-10-long-horizon-protocol.md` | 实验 / 验收记录 |
| [边缘 Agent ROS 2 接入与持久执行实验](experiments/2026-10-13-edge-field-candidate.md) | `experiments/2026-10-13-edge-field-candidate.md` | 实验 / 验收记录 |
| [点云语义驱动的长程巡检：2026-10-13 实验记录](experiments/2026-10-13-semantic-execution.md) | `experiments/2026-10-13-semantic-execution.md` | 实验 / 验收记录 |
| [三维点云语义层：历史 Gazebo 地图回放与工程验证](experiments/2026-10-13-semantic-point-cloud.md) | `experiments/2026-10-13-semantic-point-cloud.md` | 实验 / 验收记录 |
| [当前系统评估与追加任务实验协议](experiments/2026-10-13-system-evaluation-protocol.md) | `experiments/2026-10-13-system-evaluation-protocol.md` | 实验协议 / 预登记 |
| [当前系统评估与追加任务实验](experiments/2026-10-13-system-evaluation.md) | `experiments/2026-10-13-system-evaluation.md` | 实验 / 验收记录 |
| [2D、地形与 3D 空间导航实验](experiments/2026-10-14-spatial-navigation.md) | `experiments/2026-10-14-spatial-navigation.md` | 实验 / 验收记录 |
| [2026-10-15 分层目标委托与边缘重规划功能实验](experiments/2026-10-15-hierarchical-edge-objectives.md) | `experiments/2026-10-15-hierarchical-edge-objectives.md` | 实验 / 验收记录 |
| [单机器人长程鲁棒性增量实验协议（2026-10-15）](experiments/2026-10-15-single-robot-robustness-protocol.md) | `experiments/2026-10-15-single-robot-robustness-protocol.md` | 实验 / 验收记录 |
| [单机器人长程执行、故障恢复与研究证据（2026-10-15）](experiments/2026-10-15-single-robot-robustness.md) | `experiments/2026-10-15-single-robot-robustness.md` | 实验 / 验收记录 |
| [实验协议、报告与证据索引](experiments/README.md) | `experiments/README.md` | 导航 / 维护 |
| [2026-10-15：单机器人真实 Gazebo 实验索引](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/README.md) | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo/README.md` | 导航 / 维护 |
| [长程 Gazebo 自然语言任务验收脚本](experiments/long-horizon-harness.md) | `experiments/long-horizon-harness.md` | 验收工具说明 |

<a id="catalog-releases"></a>

## 软件发布记录

记录指定版本的发布身份，不覆盖后续分支改动。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [躺营 v0.2.0](releases/v0.2.0.md) | `releases/v0.2.0.md` | 指定版本发布记录 |
| [躺营 v0.3.0](releases/v0.3.0.md) | `releases/v0.3.0.md` | 指定版本发布记录 |
| [躺营 v0.4.0](releases/v0.4.0.md) | `releases/v0.4.0.md` | 指定版本发布记录 |
| [躺营 v0.5.0](releases/v0.5.0.md) | `releases/v0.5.0.md` | 指定版本发布记录 |
| [躺营 v0.6.0](releases/v0.6.0.md) | `releases/v0.6.0.md` | 指定版本发布记录 |
| [躺营 v0.7.0](releases/v0.7.0.md) | `releases/v0.7.0.md` | 指定版本发布记录 |

<a id="catalog-superpowers"></a>

## 原始设计档案

按原日期理解决策背景；计划项不作为已实现或已验收的证明。

| 文档 | 文件 | 类型 |
| --- | --- | --- |
| [Role-Based Installation and Quickstart Design](superpowers/specs/2026-08-17-role-based-installation-design.md) | `superpowers/specs/2026-08-17-role-based-installation-design.md` | 原始设计档案 |
| [Layered Robot Runtime and Pluggable Middleware Design](superpowers/specs/2026-08-18-layered-runtime-middleware-design.md) | `superpowers/specs/2026-08-18-layered-runtime-middleware-design.md` | 原始设计档案 |
| [Local-First Robot Agent Runtime Design](superpowers/specs/2026-08-18-local-first-runtime-design.md) | `superpowers/specs/2026-08-18-local-first-runtime-design.md` | 原始设计档案 |
| [XLeRobot Observable Simulation and Semantic Tool Training Design](superpowers/specs/2026-08-19-xlerobot-observable-sim-training-design.md) | `superpowers/specs/2026-08-19-xlerobot-observable-sim-training-design.md` | 原始设计档案 |
| [分布式 Robot AgentOS、世界模型与 Harness 设计](superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md) | `superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md` | 原始设计档案 |
| [RoboCasa 双 XLeRobot 与 Harness Agent 设计](superpowers/specs/2026-08-21-robocasa-dual-xlerobot-harness-design.md) | `superpowers/specs/2026-08-21-robocasa-dual-xlerobot-harness-design.md` | 原始设计档案 |
| [RoboCasa 完整场景与双 XLeRobot WebGL 数字孪生设计](superpowers/specs/2026-08-21-robocasa-webgl-digital-twin-design.md) | `superpowers/specs/2026-08-21-robocasa-webgl-digital-twin-design.md` | 原始设计档案 |
| [Versioned Task Experience and Harness Reconciliation](superpowers/specs/2026-08-22-versioned-task-experience-design.md) | `superpowers/specs/2026-08-22-versioned-task-experience-design.md` | 原始设计档案 |
| [Policy-Backed Tools, Recovery, and Sim2Real Production Design](superpowers/specs/2026-08-24-policy-tools-recovery-sim2real-design.md) | `superpowers/specs/2026-08-24-policy-tools-recovery-sim2real-design.md` | 原始设计档案 |
| [异构机器人接入与统一 Agent 契约](superpowers/specs/2026-09-06-heterogeneous-robots-design.md) | `superpowers/specs/2026-09-06-heterogeneous-robots-design.md` | 原始设计档案 |
| [单机器人 RGB-D 闭环与恢复设计](superpowers/specs/2026-09-08-single-robot-rgbd-design.md) | `superpowers/specs/2026-09-08-single-robot-rgbd-design.md` | 原始设计档案 |
| [ADR: 物理写工具闭环契约、语义地图层与恢复分类](superpowers/specs/2026-09-10-closed-loop-semantic-upgrade-adr.md) | `superpowers/specs/2026-09-10-closed-loop-semantic-upgrade-adr.md` | 原始设计档案 |
| [家庭移动操作闭环规格](superpowers/specs/2026-09-10-home-mobile-manipulation.md) | `superpowers/specs/2026-09-10-home-mobile-manipulation.md` | 原始设计档案 |
| [ADR: 面向 LLM 的标准化机器人工具层](superpowers/specs/2026-09-11-standard-robot-tool-layer-adr.md) | `superpowers/specs/2026-09-11-standard-robot-tool-layer-adr.md` | 原始设计档案 |
| [通用移动操作 Agent 系统升级](superpowers/specs/2026-09-13-general-robot-system-design.md) | `superpowers/specs/2026-09-13-general-robot-system-design.md` | 原始设计档案 |
| [云端机群大脑与 Orin NX 边缘 Agent 升级规范（ADR，2026-09-25）](superpowers/specs/2026-09-25-cloud-edge-brain-upgrade-adr.md) | `superpowers/specs/2026-09-25-cloud-edge-brain-upgrade-adr.md` | 原始设计档案 |
| [同一 Agent 核心的云端与边缘 Harness / Docker 升级规范（ADR，2026-09-25）](superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md) | `superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md` | 原始设计档案 |
| [ADR：统一能力驱动的机器人 Agent](superpowers/specs/2026-09-27-unified-capability-agent-architecture-adr.md) | `superpowers/specs/2026-09-27-unified-capability-agent-architecture-adr.md` | 原始设计档案 |

## 配套数据与插图

JSON/CSV 是指定实验或文档的摘要/附件，不能单独代替原报告；图片用于解释或展示，物理完成依据仍须查原始观测及 manifest。

| 文件 | 格式 | 对应目录 |
| --- | --- | --- |
| [2026-10-05-physical-robot-validation.json](development/2026-10-05-physical-robot-validation.json) | JSON | `development` |
| [2026-10-09-long-horizon-context-validation.json](development/2026-10-09-long-horizon-context-validation.json) | JSON | `development` |
| [2026-10-10-long-horizon-validation.json](development/2026-10-10-long-horizon-validation.json) | JSON | `development` |
| [2026-10-13-modular-extensibility.validation.json](development/2026-10-13-modular-extensibility.validation.json) | JSON | `development` |
| [2026-10-14-artifact-cleanup.json](development/2026-10-14-artifact-cleanup.json) | JSON | `development` |
| [2026-10-14-book-and-maintenance-review.json](development/2026-10-14-book-and-maintenance-review.json) | JSON | `development` |
| [2026-09-27-capability-goal-acceptance.json](experiments/2026-09-27-capability-goal-acceptance.json) | JSON | `experiments` |
| [2026-09-27-gazebo-slam-semantic-navigation-acceptance.json](experiments/2026-09-27-gazebo-slam-semantic-navigation-acceptance.json) | JSON | `experiments` |
| [2026-09-27-gazebo-slam-semantic-navigation-hardening-acceptance.json](experiments/2026-09-27-gazebo-slam-semantic-navigation-hardening-acceptance.json) | JSON | `experiments` |
| [2026-09-27-gazebo-xlerobot-home-acceptance.json](experiments/2026-09-27-gazebo-xlerobot-home-acceptance.json) | JSON | `experiments` |
| [2026-09-28-complex-model-led-goals.json](experiments/2026-09-28-complex-model-led-goals.json) | JSON | `experiments` |
| [2026-09-28-natural-language-goal-matrix.json](experiments/2026-09-28-natural-language-goal-matrix.json) | JSON | `experiments` |
| [2026-10-05-long-horizon-acceptance.json](experiments/2026-10-05-long-horizon-acceptance.json) | JSON | `experiments` |
| [2026-10-10-long-horizon-acceptance.json](experiments/2026-10-10-long-horizon-acceptance.json) | JSON | `experiments` |
| [2026-10-13-edge-field-candidate.json](experiments/2026-10-13-edge-field-candidate.json) | JSON | `experiments` |
| [2026-10-13-semantic-execution-visits.csv](experiments/2026-10-13-semantic-execution-visits.csv) | CSV | `experiments` |
| [2026-10-13-semantic-execution.csv](experiments/2026-10-13-semantic-execution.csv) | CSV | `experiments` |
| [2026-10-13-semantic-execution.json](experiments/2026-10-13-semantic-execution.json) | JSON | `experiments` |
| [2026-10-13-semantic-point-cloud.csv](experiments/2026-10-13-semantic-point-cloud.csv) | CSV | `experiments` |
| [2026-10-13-semantic-point-cloud.json](experiments/2026-10-13-semantic-point-cloud.json) | JSON | `experiments` |
| [2026-10-13-system-evaluation.csv](experiments/2026-10-13-system-evaluation.csv) | CSV | `experiments` |
| [2026-10-13-system-evaluation.json](experiments/2026-10-13-system-evaluation.json) | JSON | `experiments` |
| [2026-10-14-spatial-navigation-paths.csv](experiments/2026-10-14-spatial-navigation-paths.csv) | CSV | `experiments` |
| [2026-10-14-spatial-navigation.csv](experiments/2026-10-14-spatial-navigation.csv) | CSV | `experiments` |
| [2026-10-14-spatial-navigation.json](experiments/2026-10-14-spatial-navigation.json) | JSON | `experiments` |
| [context-comparison.png](experiments/assets/2026-09-21-agent-context-evaluation/context-comparison.png) | PNG | `experiments/assets/2026-09-21-agent-context-evaluation` |
| [factorial-stage-comparison.png](experiments/assets/2026-09-21-agent-context-evaluation/factorial-stage-comparison.png) | PNG | `experiments/assets/2026-09-21-agent-context-evaluation` |
| [stage-comparison.png](experiments/assets/2026-09-21-agent-context-evaluation/stage-comparison.png) | PNG | `experiments/assets/2026-09-21-agent-context-evaluation` |
| [comparison.svg](experiments/assets/2026-09-21-grounded-verification/comparison.svg) | SVG | `experiments/assets/2026-09-21-grounded-verification` |
| [2026-10-13-semantic-execution.png](experiments/assets/2026-10-13-semantic-execution.png) | PNG | `experiments/assets` |
| [2026-10-13-semantic-execution.svg](experiments/assets/2026-10-13-semantic-execution.svg) | SVG | `experiments/assets` |
| [debug-attempt01.json](experiments/evidence/2026-10-15-hierarchical-edge/debug-attempt01.json) | JSON | `experiments/evidence/2026-10-15-hierarchical-edge` |
| [debug-attempt02.json](experiments/evidence/2026-10-15-hierarchical-edge/debug-attempt02.json) | JSON | `experiments/evidence/2026-10-15-hierarchical-edge` |
| [functional-tests.jsonl](experiments/evidence/2026-10-15-hierarchical-edge/functional-tests.jsonl) | JSONL | `experiments/evidence/2026-10-15-hierarchical-edge` |
| [manifest.json](experiments/evidence/2026-10-15-hierarchical-edge/manifest.json) | JSON | `experiments/evidence/2026-10-15-hierarchical-edge` |
| [tangying-hierarchical-go-tests.log](experiments/evidence/2026-10-15-hierarchical-edge/tangying-hierarchical-go-tests.log) | LOG | `experiments/evidence/2026-10-15-hierarchical-edge` |
| [tangying-hierarchical-race-tests.log](experiments/evidence/2026-10-15-hierarchical-edge/tangying-hierarchical-race-tests.log) | LOG | `experiments/evidence/2026-10-15-hierarchical-edge` |
| [baseline-host.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/baseline-host.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [baseline-regression-recovery_test.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/baseline-regression-recovery_test.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [book-check-final.log](experiments/evidence/2026-10-15-single-robot-robustness/book-check-final.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [book-check.log](experiments/evidence/2026-10-15-single-robot-robustness/book-check.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [build-final.log](experiments/evidence/2026-10-15-single-robot-robustness/build-final.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [build-final2.log](experiments/evidence/2026-10-15-single-robot-robustness/build-final2.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [build.log](experiments/evidence/2026-10-15-single-robot-robustness/build.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [campaign-attempt01.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/campaign-attempt01.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [campaign-attempt02.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/campaign-attempt02.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [campaign-attempt03-reopen.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/campaign-attempt03-reopen.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [campaign-attempt04-boundaries.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/campaign-attempt04-boundaries.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [campaign-review-final-parser.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/campaign-review-final-parser.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [campaign-review-final-targeted.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/campaign-review-final-targeted.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [campaign-review-planning-config.log](experiments/evidence/2026-10-15-single-robot-robustness/campaign-review-planning-config.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [campaign-review-planning-timeout.log](experiments/evidence/2026-10-15-single-robot-robustness/campaign-review-planning-timeout.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [composite_recovery_test.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/composite-before-source/composite_recovery_test.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness/composite-before-source` |
| [runner.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/composite-before-source/runner.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness/composite-before-source` |
| [evidence-index.json](experiments/evidence/2026-10-15-single-robot-robustness/evidence-index.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign/manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign` |
| [records.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign/records.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign` |
| [run.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign/run.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign` |
| [summary.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign/summary.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign` |
| [tasks.csv](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign/tasks.csv) | CSV | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign` |
| [test-events.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign/test-events.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign` |
| [manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02/manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02` |
| [records.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02/records.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02` |
| [run.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02/run.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02` |
| [summary.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02/summary.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02` |
| [tasks.csv](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02/tasks.csv) | CSV | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02` |
| [test-events.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02/test-events.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-02` |
| [manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03/manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03` |
| [records.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03/records.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03` |
| [run.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03/run.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03` |
| [summary.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03/summary.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03` |
| [tasks.csv](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03/tasks.csv) | CSV | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03` |
| [test-events.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03/test-events.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-03` |
| [manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04/manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04` |
| [records.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04/records.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04` |
| [run.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04/run.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04` |
| [summary.json](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04/summary.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04` |
| [tasks.csv](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04/tasks.csv) | CSV | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04` |
| [test-events.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04/test-events.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/final-campaign-04` |
| [agent-build-final2.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/agent-build-final2.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [agent-build-final3.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/agent-build-final3.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [agent-build-upgrade.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/agent-build-upgrade.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [arrivals.csv](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/arrivals.csv) | CSV | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [controlled-agent-upgrade.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/controlled-agent-upgrade.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [controlled-final-agent-update.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/controlled-final-agent-update.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [controlled-final3-agent-update.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/controlled-final3-agent-update.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [delegated-capabilities.csv](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/delegated-capabilities.csv) | CSV | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [environment-attempt001-failed.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/environment-attempt001-failed.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [environment-inventory.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/environment-inventory.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [fault-recovery-chain.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/fault-recovery-chain.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [faults.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/faults.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [frozen-export_research_index.py](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-export_research_index.py) | PY | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [frozen-revalidate_host_inspection.py](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-revalidate_host_inspection.py) | PY | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [frozen-revalidation-execution.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-revalidation-execution.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [frozen-run_host_inspection.py](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-run_host_inspection.py) | PY | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [console_session.py](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies/console_session.py) | PY | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies` |
| [evaluate_long_horizon.py](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies/evaluate_long_horizon.py) | PY | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies` |
| [frozen-dependencies.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies/frozen-dependencies.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies` |
| [revalidate_long_horizon.py](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies/revalidate_long_horizon.py) | PY | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies` |
| [semantic_execution_evidence.py](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies/semantic_execution_evidence.py) | PY | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo/frozen-verification-dependencies` |
| [handoff.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/handoff.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection01-durable-state-00.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection01-durable-state-00.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection01-durable-state-01.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection01-durable-state-01.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection01-durable-state-index.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection01-durable-state-index.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection01-independent.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection01-independent.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection01-manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection01-manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection01-preregistration.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection01-preregistration.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection01-report.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection01-report.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection01-terminal-task.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection01-terminal-task.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-durable-state-00.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-durable-state-00.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-durable-state-01.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-durable-state-01.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-durable-state-index.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-durable-state-index.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-frozen-dependencies-independent.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-frozen-dependencies-independent.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-independent.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-independent.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-preregistration.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-preregistration.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-report.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-report.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host-inspection02-terminal-task.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host-inspection02-terminal-task.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [host01-pre-update-reconciliation.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/host01-pre-update-reconciliation.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection-preregistration.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection-preregistration.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection01-manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection01-manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection01-report.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection01-report.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection01-run-config.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection01-run-config.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection02-independent.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection02-independent.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection02-manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection02-manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection02-report.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection02-report.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection02-run-config.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection02-run-config.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [inspection02-terminal-task.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/inspection02-terminal-task.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [model-routes-before.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/model-routes-before.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [planning-attempt-adb3213caa1c9f65ef7883053e28fbc0.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/planning-attempt-adb3213caa1c9f65ef7883053e28fbc0.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [preparation01-independent-no-fault.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/preparation01-independent-no-fault.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [preparation01-independent.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/preparation01-independent.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [preparation01-manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/preparation01-manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [preparation01-report.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/preparation01-report.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [preparation01-run-config.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/preparation01-run-config.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [preparation01-terminal-task.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/preparation01-terminal-task.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [raw-evidence-index.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/raw-evidence-index.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [redaction-check.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/redaction-check.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [source-provenance-before-preparation.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/source-provenance-before-preparation.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [steps.csv](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/steps.csv) | CSV | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [summary.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/summary.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [supplemental-manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/supplemental-manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [tasks.csv](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/tasks.csv) | CSV | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [v1-execution.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/v1-execution.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [v2-execution.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/v2-execution.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [verification-provenance.json](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/verification-provenance.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [verify_frozen_host.py](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/verify_frozen_host.py) | PY | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo` |
| [go-docs-publication.log](experiments/evidence/2026-10-15-single-robot-robustness/go-docs-publication.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [go-docs-tests-final.log](experiments/evidence/2026-10-15-single-robot-robustness/go-docs-tests-final.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [go-docs-tests.log](experiments/evidence/2026-10-15-single-robot-robustness/go-docs-tests.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [host-baseline-failure.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/host-baseline-failure.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [host-baseline-metadata.json](experiments/evidence/2026-10-15-single-robot-robustness/host-baseline-metadata.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [inspection-parser-after.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/inspection-parser-after.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [inspection-parser-before.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/inspection-parser-before.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [integration-race-tests-final.log](experiments/evidence/2026-10-15-single-robot-robustness/integration-race-tests-final.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [integration-race-tests.log](experiments/evidence/2026-10-15-single-robot-robustness/integration-race-tests.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [lint-final.log](experiments/evidence/2026-10-15-single-robot-robustness/lint-final.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [lint.log](experiments/evidence/2026-10-15-single-robot-robustness/lint.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [maintained-go-tests-final.log](experiments/evidence/2026-10-15-single-robot-robustness/maintained-go-tests-final.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [maintained-go-tests-final2.log](experiments/evidence/2026-10-15-single-robot-robustness/maintained-go-tests-final2.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [maintained-go-tests-final3.log](experiments/evidence/2026-10-15-single-robot-robustness/maintained-go-tests-final3.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [maintained-go-tests.log](experiments/evidence/2026-10-15-single-robot-robustness/maintained-go-tests.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [master-manifest.json](experiments/evidence/2026-10-15-single-robot-robustness/master-manifest.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [python-docs-book-tests.log](experiments/evidence/2026-10-15-single-robot-robustness/python-docs-book-tests.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [python-docs-publication.log](experiments/evidence/2026-10-15-single-robot-robustness/python-docs-publication.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [python-final-tests.log](experiments/evidence/2026-10-15-single-robot-robustness/python-final-tests.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [python-harness-tests.log](experiments/evidence/2026-10-15-single-robot-robustness/python-harness-tests.log) | LOG | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [completion.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/review-before-source/completion.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness/review-before-source` |
| [completion_test.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/review-before-source/completion_test.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness/review-before-source` |
| [host.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/review-before-source/host.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness/review-before-source` |
| [journal.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/review-before-source/journal.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness/review-before-source` |
| [recovery.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/review-before-source/recovery.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness/review-before-source` |
| [recovery_test.go.txt](experiments/evidence/2026-10-15-single-robot-robustness/review-before-source/recovery_test.go.txt) | TXT | `experiments/evidence/2026-10-15-single-robot-robustness/review-before-source` |
| [software-metrics.json](experiments/evidence/2026-10-15-single-robot-robustness/software-metrics.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-completion-after-fix.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-completion-after-fix.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-completion-before-fix.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-completion-before-fix.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-composite-runner-after.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-after.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-composite-runner-before-metadata.json](experiments/evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-before-metadata.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-composite-runner-before.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-before.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-composite-runner-final-metadata.json](experiments/evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-final-metadata.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-composite-runner-package.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-package.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-composite-runner-race.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-race.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-final-metadata.json](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-final-metadata.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-final-race.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-final-race.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-final-tests.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-final-tests.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-final2-metadata.json](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-final2-metadata.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-final2-race.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-final2-race.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-final2-tests.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-final2-tests.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-review-after.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-review-after.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-review-before-metadata.json](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-review-before-metadata.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-review-before.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-review-before.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [tangying-host-robustness-fixed.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/tangying-host-robustness-fixed.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [validation.json](experiments/evidence/2026-10-15-single-robot-robustness/validation.json) | JSON | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [worker-attempt01.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/worker-attempt01.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [worker-attempt02.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/worker-attempt02.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [worker-fixed01.jsonl](experiments/evidence/2026-10-15-single-robot-robustness/worker-fixed01.jsonl) | JSONL | `experiments/evidence/2026-10-15-single-robot-robustness` |
| [task-replay-desktop.png](frontend/assets/console-ux-20260927/task-replay-desktop.png) | PNG | `frontend/assets/console-ux-20260927` |
| [workspace-desktop.png](frontend/assets/console-ux-20260927/workspace-desktop.png) | PNG | `frontend/assets/console-ux-20260927` |
| [workspace-mobile.png](frontend/assets/console-ux-20260927/workspace-mobile.png) | PNG | `frontend/assets/console-ux-20260927` |
| [workspace-tablet.png](frontend/assets/console-ux-20260927/workspace-tablet.png) | PNG | `frontend/assets/console-ux-20260927` |
| [captures.json](images/furnished-home/captures.json) | JSON | `images/furnished-home` |
| [completed-cup-transfer.png](images/furnished-home/completed-cup-transfer.png) | PNG | `images/furnished-home` |
| [kitchen.png](images/furnished-home/kitchen.png) | PNG | `images/furnished-home` |
| [panorama.png](images/furnished-home/panorama.png) | PNG | `images/furnished-home` |
| [slam-keyframe-001-depth.png](images/furnished-home/slam-keyframe-001-depth.png) | PNG | `images/furnished-home` |
| [slam-keyframe-001-rgb.jpg](images/furnished-home/slam-keyframe-001-rgb.jpg) | JPG | `images/furnished-home` |
