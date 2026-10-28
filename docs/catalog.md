# 全量分类目录

[文档导航](README.md) · [学习](learning/README.md) · [开发](development/README.md) · [运维](operations/README.md) · [研究](research/README.md)

本页由 `python3 scripts/build_docs_catalog.py` 从原文件标题和路径生成。分类表示用途，不自动认证内容仍然有效；以文内版本、源码和对应证据判断。新增资料后重新生成，使用 `--check` 检查是否遗漏。

覆盖 `docs/` 中除本页外的 **290 份 Markdown** 与 **1128 份配套文件**；书籍通过[书籍目录](../book/README.md)进入，未重复列出历史版次和生成合订本。

## 分类总览

| 分类 | 文档数 | 阅读原则 |
| --- | ---: | --- |
| [导航与维护](#catalog-navigation) | 4 | 四条路线选择阅读顺序；本目录保留每份资料的原路径。 |
| [使用指南](#catalog-guides) | 15 | 查操作与功能；旧仿真案例按页面注明的引擎和版本阅读。 |
| [架构与协议](#catalog-architecture) | 30 | 查原理、组件边界和协议；设计设想及历史快照不等于当前交付。 |
| [开发参考与演进记录](#catalog-development) | 115 | 先读开发导航；带日期记录回答当时如何设计、实现和修复。 |
| [部署与运维流程](#catalog-operations) | 14 | 查角色、启动、故障、升级及放行条件。 |
| [分角色安装](#catalog-install) | 9 | 按目标机器选择；旧 Cloud 控制面文档保留为历史安装资料。 |
| [运行手册、接口与验收快照](#catalog-production) | 20 | 参考文档与历史审计并存；不能把旧状态页当作当前整仓认证。 |
| [实体机器人接入](#catalog-sim2real) | 1 | 软件接口、离线前置检查和现场物理验收分别核对。 |
| [工作台与交互](#catalog-frontend) | 1 | 查用户流程、开发诊断及界面资料。 |
| [实验、预登记与复验](#catalog-experiments) | 57 | 区分研究对照、工程案例和工具说明；结论以原报告的环境、版本和限制为准。 |
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
| [边缘 AgentOS：消息、上下文与 Harness 实现说明](architecture/edge-agent-communication-and-context.md) | `architecture/edge-agent-communication-and-context.md` | 架构 / 协议 / 设计参考 |
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
| [最新分层架构、全套参考文档与书籍 2.2.0 审校](development/2026-10-16-architecture-book-refresh.md) | `development/2026-10-16-architecture-book-refresh.md` | 实施 / 设计 / 复盘（按文内版本） |
| [首次进入未知场景的探索扫描升级记录](development/2026-10-17-exploration-first-scan.md) | `development/2026-10-17-exploration-first-scan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [异构三维探索与可复现实验：设计和变更记录](development/2026-10-17-heterogeneous-spatial-exploration.md) | `development/2026-10-17-heterogeneous-spatial-exploration.md` | 实施 / 设计 / 复盘（按文内版本） |
| [边缘 AgentOS 通信、上下文与上线就绪审计](development/2026-10-18-edge-communication-readiness.md) | `development/2026-10-18-edge-communication-readiness.md` | 实施 / 设计 / 复盘（按文内版本） |
| [异构边缘 Agent、协作数据与运维就绪升级](development/2026-10-18-edge-swarm-readiness.md) | `development/2026-10-18-edge-swarm-readiness.md` | 实施 / 设计 / 复盘（按文内版本） |
| [Gazebo 物理闭环：控制、测量、停止、标定与多机避碰](development/2026-10-18-gazebo-field-validation.md) | `development/2026-10-18-gazebo-field-validation.md` | 实施 / 设计 / 复盘（按文内版本） |
| [机器人工作台视觉与交互更新（2026-10-18）](development/2026-10-18-mission-console-ui.md) | `development/2026-10-18-mission-console-ui.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-18：异构边缘 Agent 的持久收件与有作用域的协作建议](development/2026-10-18-scoped-peer-cooperation.md) | `development/2026-10-18-scoped-peer-cooperation.md` | 实施 / 设计 / 复盘（按文内版本） |
| [已知地图三维覆盖与多层地面导航：设计和变更记录](development/2026-10-18-spatial-coverage-upgrade.md) | `development/2026-10-18-spatial-coverage-upgrade.md` | 实施 / 设计 / 复盘（按文内版本） |
| [边缘机器人工作空间与任务回溯升级](development/2026-10-20-edge-robot-workspaces.md) | `development/2026-10-20-edge-robot-workspaces.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-21 · 教材 3.0.0：架构推导、源码与故障实验](development/2026-10-21-book-teaching-redesign.md) | `development/2026-10-21-book-teaching-redesign.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-22 · 教材 3.1.0：跨域协议、模型输入、上下文与一致性](development/2026-10-22-book-protocol-context-cache.md) | `development/2026-10-22-book-protocol-context-cache.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-27 · 边缘大脑与教材协同升级计划](development/2026-10-27-edge-brain-book-plan.md) | `development/2026-10-27-edge-brain-book-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-28 · 边缘大脑可靠性与教材 3.2.0](development/2026-10-28-edge-brain-reliability.md) | `development/2026-10-28-edge-brain-reliability.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-28 · 边缘长任务与协作状态扩展计划](development/2026-10-28-edge-scalability-plan.md) | `development/2026-10-28-edge-scalability-plan.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-28 · 持续运行、模型成本与规划经验升级](development/2026-10-28-edge-scalability.md) | `development/2026-10-28-edge-scalability.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-28 边缘系统能力与实机接入评估](development/2026-10-28-system-upgrade-assessment.md) | `development/2026-10-28-system-upgrade-assessment.md` | 实施 / 设计 / 复盘（按文内版本） |
| [2026-10-28 任务复测后的边缘故障修复](development/2026-10-28-task-validation-fixes.md) | `development/2026-10-28-task-validation-fixes.md` | 实施 / 设计 / 复盘（按文内版本） |
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
| [从测量估计 SE3：标定、跨机注册与拒绝条件](development/measured-se3-registration.md) | `development/measured-se3-registration.md` | 开发参考 / 方案（核对文内状态） |
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
| [本机磁盘清理与 Docker 恢复记录](operations/2026-10-18-local-disk-cleanup.md) | `operations/2026-10-18-local-disk-cleanup.md` | 操作 / 接口参考 |
| [部署、运维与现场交付导航](operations/README.md) | `operations/README.md` | 导航 / 维护 |
| [Agent 运行时配置](operations/agent-runtime-config.md) | `operations/agent-runtime-config.md` | 操作 / 接口参考 |
| [产物清理、保留与实验复核](operations/artifact-retention.md) | `operations/artifact-retention.md` | 操作 / 接口参考 |
| [目标委托的部署、续跑与升级](operations/delegated-goal-operations.md) | `operations/delegated-goal-operations.md` | 操作 / 接口参考 |
| [部署目标与代码归属](operations/deployment.md) | `operations/deployment.md` | 操作 / 接口参考 |
| [边缘 Agent 实机测试部署](operations/edge-field-testing.md) | `operations/edge-field-testing.md` | 操作 / 接口参考 |
| [边缘机器人工作空间：切换、回溯与部署](operations/edge-robot-workspaces.md) | `operations/edge-robot-workspaces.md` | 操作 / 接口参考 |
| [全新部署：新机器 / 新机器人 / 新云服务器](operations/fresh-deployment.md) | `operations/fresh-deployment.md` | 操作 / 接口参考 |
| [XLeRobot 生产就绪判定](operations/production-readiness.md) | `operations/production-readiness.md` | 操作 / 接口参考 |
| [家庭场景发布验收清单](operations/release-checklist.md) | `operations/release-checklist.md` | 操作 / 接口参考 |
| [RoboCasa 双机器人交接与实机迁移](operations/robocasa-handoff.md) | `operations/robocasa-handoff.md` | 操作 / 接口参考 |
| [DJI、Unitree 与 AGV 的 ROS 2 设备绑定](operations/ros2-device-bindings.md) | `operations/ros2-device-bindings.md` | 操作 / 接口参考 |
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
| [边缘模型请求、缓存提示与失败记录](production/edge-brain-model-context.md) | `production/edge-brain-model-context.md` | 操作 / 接口参考 |
| [2026-09-26 生产就绪审计与实机验证交接](production/field-readiness-2026-09-26.md) | `production/field-readiness-2026-09-26.md` | 验收 / 就绪快照 |
| [模型成本与任务规划经验](production/model-cost-and-recipes.md) | `production/model-cost-and-recipes.md` | 操作 / 接口参考 |
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
| [首次未知场景探索扫描：预登记与验收协议](experiments/2026-10-17-exploration-first-scan-protocol.md) | `experiments/2026-10-17-exploration-first-scan-protocol.md` | 实验 / 验收记录 |
| [首次未知场景探索：Gazebo 工程实验记录](experiments/2026-10-17-exploration-first-scan.md) | `experiments/2026-10-17-exploration-first-scan.md` | 实验 / 验收记录 |
| [异构三维探索：配对消融、覆盖指标与回放协议](experiments/2026-10-17-spatial-exploration-protocol.md) | `experiments/2026-10-17-spatial-exploration-protocol.md` | 实验 / 验收记录 |
| [异构单机器人 Agent ROS 重复验收](experiments/2026-10-18-edge-agent-ros-campaign.md) | `experiments/2026-10-18-edge-agent-ros-campaign.md` | 实验 / 验收记录 |
| [异构边缘 Agent 与分布式协作分层验收](experiments/2026-10-18-edge-swarm-readiness.md) | `experiments/2026-10-18-edge-swarm-readiness.md` | 实验 / 验收记录 |
| [Gazebo 经典控制与边缘任务全链路实验](experiments/2026-10-18-gazebo-field-validation.md) | `experiments/2026-10-18-gazebo-field-validation.md` | 实验 / 验收记录 |
| [已知覆盖、多层导航与扩展探索消融：实际软件实验记录](experiments/2026-10-18-spatial-coverage-upgrade.md) | `experiments/2026-10-18-spatial-coverage-upgrade.md` | 实验 / 验收记录 |
| [异构三维探索与配对消融：实际软件实验记录](experiments/2026-10-18-spatial-exploration.md) | `experiments/2026-10-18-spatial-exploration.md` | 实验 / 验收记录 |
| [边缘大脑重复故障实验（2026-10-28）](experiments/2026-10-27-edge-brain-reliability/README.md) | `experiments/2026-10-27-edge-brain-reliability/README.md` | 导航 / 维护 |
| [执行前范围核对](experiments/2026-10-27-edge-brain-reliability/scope-review.md) | `experiments/2026-10-27-edge-brain-reliability/scope-review.md` | 实验 / 验收记录 |
| [2026-10-28 边缘持续运行与成本实验](experiments/2026-10-28-edge-scalability/README.md) | `experiments/2026-10-28-edge-scalability/README.md` | 导航 / 维护 |
| [Edge long-task journal performance evidence](experiments/2026-10-28-edge-scalability/performance/README.md) | `experiments/2026-10-28-edge-scalability/performance/README.md` | 导航 / 维护 |
| [2026-10-28 边缘任务复测与故障修复](experiments/2026-10-28-edge-task-validation/README.md) | `experiments/2026-10-28-edge-task-validation/README.md` | 导航 / 维护 |
| [Model cost and recipe integration validation — 2026-10-28](experiments/2026-10-28-edge-task-validation/cost/README.md) | `experiments/2026-10-28-edge-task-validation/cost/README.md` | 导航 / 维护 |
| [Gazebo 三机器人任务与停止证据](experiments/2026-10-28-edge-task-validation/gazebo/README.md) | `experiments/2026-10-28-edge-task-validation/gazebo/README.md` | 导航 / 维护 |
| [通信与云边故障验收（2026-10-28，Asia/Shanghai）](experiments/2026-10-28-edge-task-validation/peer/README.md) | `experiments/2026-10-28-edge-task-validation/peer/README.md` | 导航 / 维护 |
| [实验协议、报告与证据索引](experiments/README.md) | `experiments/README.md` | 导航 / 维护 |
| [2026-10-15：单机器人真实 Gazebo 实验索引](experiments/evidence/2026-10-15-single-robot-robustness/gazebo/README.md) | `experiments/evidence/2026-10-15-single-robot-robustness/gazebo/README.md` | 导航 / 维护 |
| [首次探索扫描：脱敏证据包](experiments/evidence/2026-10-17-exploration-first-scan/README.md) | `experiments/evidence/2026-10-17-exploration-first-scan/README.md` | 导航 / 维护 |
| [通信与上下文审计证据](experiments/evidence/2026-10-18-communication-audit/README.md) | `experiments/evidence/2026-10-18-communication-audit/README.md` | 导航 / 维护 |
| [2026-10-18 异构边缘验收证据包](experiments/evidence/2026-10-18-edge-swarm-readiness/README.md) | `experiments/evidence/2026-10-18-edge-swarm-readiness/README.md` | 导航 / 维护 |
| [Gazebo 物理闭环证据（2026-10-18）](experiments/evidence/2026-10-18-gazebo-field-validation/README.md) | `experiments/evidence/2026-10-18-gazebo-field-validation/README.md` | 导航 / 维护 |
| [本轮研究证据与复验](experiments/evidence/2026-10-18-spatial-coverage-upgrade/README.md) | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/README.md` | 导航 / 维护 |
| [工程验证原始结果与修复](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/README.md) | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/README.md` | 导航 / 维护 |
| [冻结软件实验与验证证据](experiments/evidence/2026-10-18-spatial-exploration/README.md) | `experiments/evidence/2026-10-18-spatial-exploration/README.md` | 导航 / 维护 |
| [边缘工作空间软件验收证据](experiments/evidence/2026-10-20-edge-workspaces/README.md) | `experiments/evidence/2026-10-20-edge-workspaces/README.md` | 导航 / 维护 |
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
| [2026-10-16-architecture-book-refresh.json](development/2026-10-16-architecture-book-refresh.json) | JSON | `development` |
| [context-projection.json](development/evidence/2026-10-21-book-teaching-redesign/context-projection.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign` |
| [device.json](development/evidence/2026-10-21-book-teaching-redesign/lost-reply/guarded/device.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign/lost-reply/guarded` |
| [trace.json](development/evidence/2026-10-21-book-teaching-redesign/lost-reply/guarded/trace.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign/lost-reply/guarded` |
| [device.json](development/evidence/2026-10-21-book-teaching-redesign/lost-reply/naive/device.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign/lost-reply/naive` |
| [trace.json](development/evidence/2026-10-21-book-teaching-redesign/lost-reply/naive/trace.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign/lost-reply/naive` |
| [device.json](development/evidence/2026-10-21-book-teaching-redesign/lost-reply/reconciled/device.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign/lost-reply/reconciled` |
| [trace.json](development/evidence/2026-10-21-book-teaching-redesign/lost-reply/reconciled/trace.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign/lost-reply/reconciled` |
| [summary.json](development/evidence/2026-10-21-book-teaching-redesign/lost-reply/summary.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign/lost-reply` |
| [publication-and-docs-tests.txt](development/evidence/2026-10-21-book-teaching-redesign/publication-and-docs-tests.txt) | TXT | `development/evidence/2026-10-21-book-teaching-redesign` |
| [software-checks.json](development/evidence/2026-10-21-book-teaching-redesign/software-checks.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign` |
| [validation-manifest.json](development/evidence/2026-10-21-book-teaching-redesign/validation-manifest.json) | JSON | `development/evidence/2026-10-21-book-teaching-redesign` |
| [book-3.1.0-editorial-check.json](development/evidence/book-3.1.0-editorial-check.json) | JSON | `development/evidence` |
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
| [2026-10-17-exploration-first-scan.csv](experiments/2026-10-17-exploration-first-scan.csv) | CSV | `experiments` |
| [2026-10-17-exploration-first-scan.json](experiments/2026-10-17-exploration-first-scan.json) | JSON | `experiments` |
| [2026-10-18-edge-agent-ros-campaign.csv](experiments/2026-10-18-edge-agent-ros-campaign.csv) | CSV | `experiments` |
| [2026-10-18-edge-agent-ros-campaign.json](experiments/2026-10-18-edge-agent-ros-campaign.json) | JSON | `experiments` |
| [2026-10-18-edge-swarm-readiness.json](experiments/2026-10-18-edge-swarm-readiness.json) | JSON | `experiments` |
| [2026-10-18-spatial-coverage-upgrade.csv](experiments/2026-10-18-spatial-coverage-upgrade.csv) | CSV | `experiments` |
| [2026-10-18-spatial-coverage-upgrade.json](experiments/2026-10-18-spatial-coverage-upgrade.json) | JSON | `experiments` |
| [2026-10-18-spatial-exploration.csv](experiments/2026-10-18-spatial-exploration.csv) | CSV | `experiments` |
| [2026-10-18-spatial-exploration.json](experiments/2026-10-18-spatial-exploration.json) | JSON | `experiments` |
| [affected-packages.json](experiments/2026-10-27-edge-brain-checks/affected-packages.json) | JSON | `experiments/2026-10-27-edge-brain-checks` |
| [affected-packages.log](experiments/2026-10-27-edge-brain-checks/affected-packages.log) | LOG | `experiments/2026-10-27-edge-brain-checks` |
| [builds.json](experiments/2026-10-27-edge-brain-checks/builds.json) | JSON | `experiments/2026-10-27-edge-brain-checks` |
| [publication.json](experiments/2026-10-27-edge-brain-checks/publication.json) | JSON | `experiments/2026-10-27-edge-brain-checks` |
| [manifest.json](experiments/2026-10-27-edge-brain-reliability/manifest.json) | JSON | `experiments/2026-10-27-edge-brain-reliability` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-01/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-01` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-01/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-01` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-02/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-02` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-02/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-02` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-03/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-03` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-03/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-03` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-04/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-04` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-04/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-04` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-05/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-05` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-05/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-05` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-06/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-06` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-06/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-06` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-07/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-07` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-07/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-07` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-08/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-08` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-08/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-08` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-09/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-09` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-09/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-09` |
| [go-test-events.jsonl](experiments/2026-10-27-edge-brain-reliability/run-10/go-test-events.jsonl) | JSONL | `experiments/2026-10-27-edge-brain-reliability/run-10` |
| [result.json](experiments/2026-10-27-edge-brain-reliability/run-10/result.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/run-10` |
| [run.json](experiments/2026-10-27-edge-brain-reliability/run.json) | JSON | `experiments/2026-10-27-edge-brain-reliability` |
| [runner-source.py.txt](experiments/2026-10-27-edge-brain-reliability/runner-source.py.txt) | TXT | `experiments/2026-10-27-edge-brain-reliability` |
| [ff3fd15edef6b1e4f3642887db58c4909cb7333c68233cebc476740ed0651519.json](experiments/2026-10-27-edge-brain-reliability/sources/ff3fd15edef6b1e4f3642887db58c4909cb7333c68233cebc476740ed0651519.json) | JSON | `experiments/2026-10-27-edge-brain-reliability/sources` |
| [summary.json](experiments/2026-10-27-edge-brain-reliability/summary.json) | JSON | `experiments/2026-10-27-edge-brain-reliability` |
| [archive-sha256.json](experiments/2026-10-28-edge-scalability/archive-sha256.json) | JSON | `experiments/2026-10-28-edge-scalability` |
| [builds-final.json](experiments/2026-10-28-edge-scalability/builds-final.json) | JSON | `experiments/2026-10-28-edge-scalability` |
| [builds.json](experiments/2026-10-28-edge-scalability/builds.json) | JSON | `experiments/2026-10-28-edge-scalability` |
| [catalog-build.txt](experiments/2026-10-28-edge-scalability/checks/catalog-build.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks` |
| [catalog-check.txt](experiments/2026-10-28-edge-scalability/checks/catalog-check.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks` |
| [summary.json](experiments/2026-10-28-edge-scalability/checks/summary.json) | JSON | `experiments/2026-10-28-edge-scalability/checks` |
| [book-check.txt](experiments/2026-10-28-edge-scalability/checks-final/book-check.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [catalog-build.txt](experiments/2026-10-28-edge-scalability/checks-final/catalog-build.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [catalog-check.txt](experiments/2026-10-28-edge-scalability/checks-final/catalog-check.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [diff-check.txt](experiments/2026-10-28-edge-scalability/checks-final/diff-check.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [go-docs.txt](experiments/2026-10-28-edge-scalability/checks-final/go-docs.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [gofmt.txt](experiments/2026-10-28-edge-scalability/checks-final/gofmt.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [python-docs.txt](experiments/2026-10-28-edge-scalability/checks-final/python-docs.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [ruff.txt](experiments/2026-10-28-edge-scalability/checks-final/ruff.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [summary.json](experiments/2026-10-28-edge-scalability/checks-final/summary.json) | JSON | `experiments/2026-10-28-edge-scalability/checks-final` |
| [usage-report.txt](experiments/2026-10-28-edge-scalability/checks-final/usage-report.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-final` |
| [book-check.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/book-check.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [catalog-build.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/catalog-build.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [catalog-check.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/catalog-check.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [diff-check.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/diff-check.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [go-docs.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/go-docs.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [gofmt.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/gofmt.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [python-docs.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/python-docs.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [ruff.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/ruff.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [summary.json](experiments/2026-10-28-edge-scalability/checks-readme-count/summary.json) | JSON | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [usage-report.txt](experiments/2026-10-28-edge-scalability/checks-readme-count/usage-report.txt) | TXT | `experiments/2026-10-28-edge-scalability/checks-readme-count` |
| [final-precision-fix.json](experiments/2026-10-28-edge-scalability/child-writer/final-precision-fix.json) | JSON | `experiments/2026-10-28-edge-scalability/child-writer` |
| [full-campaign.jsonl](experiments/2026-10-28-edge-scalability/child-writer/full-campaign.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/child-writer` |
| [summary.json](experiments/2026-10-28-edge-scalability/child-writer/summary.json) | JSON | `experiments/2026-10-28-edge-scalability/child-writer` |
| [after-profile-run.txt](experiments/2026-10-28-edge-scalability/performance/after-profile-run.txt) | TXT | `experiments/2026-10-28-edge-scalability/performance` |
| [after-profile.txt](experiments/2026-10-28-edge-scalability/performance/after-profile.txt) | TXT | `experiments/2026-10-28-edge-scalability/performance` |
| [after-source-sha256.json](experiments/2026-10-28-edge-scalability/performance/after-source-sha256.json) | JSON | `experiments/2026-10-28-edge-scalability/performance` |
| [after.cpu](experiments/2026-10-28-edge-scalability/performance/after.cpu) | CPU | `experiments/2026-10-28-edge-scalability/performance` |
| [after.jsonl](experiments/2026-10-28-edge-scalability/performance/after.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/performance` |
| [append-benchmark.txt](experiments/2026-10-28-edge-scalability/performance/append-benchmark.txt) | TXT | `experiments/2026-10-28-edge-scalability/performance` |
| [artifact-manifest.json](experiments/2026-10-28-edge-scalability/performance/artifact-manifest.json) | JSON | `experiments/2026-10-28-edge-scalability/performance` |
| [before-profile.txt](experiments/2026-10-28-edge-scalability/performance/before-profile.txt) | TXT | `experiments/2026-10-28-edge-scalability/performance` |
| [before-source-sha256.json](experiments/2026-10-28-edge-scalability/performance/before-source-sha256.json) | JSON | `experiments/2026-10-28-edge-scalability/performance` |
| [before.cpu](experiments/2026-10-28-edge-scalability/performance/before.cpu) | CPU | `experiments/2026-10-28-edge-scalability/performance` |
| [before.jsonl](experiments/2026-10-28-edge-scalability/performance/before.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/performance` |
| [campaign-128-build-interrupted.jsonl](experiments/2026-10-28-edge-scalability/performance/campaign-128-build-interrupted.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/performance` |
| [campaign-128-race.jsonl](experiments/2026-10-28-edge-scalability/performance/campaign-128-race.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/performance` |
| [campaign-race.jsonl](experiments/2026-10-28-edge-scalability/performance/campaign-race.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/performance` |
| [commands.txt](experiments/2026-10-28-edge-scalability/performance/commands.txt) | TXT | `experiments/2026-10-28-edge-scalability/performance` |
| [comparison-summary.json](experiments/2026-10-28-edge-scalability/performance/comparison-summary.json) | JSON | `experiments/2026-10-28-edge-scalability/performance` |
| [paired-1-after.log](experiments/2026-10-28-edge-scalability/performance/paired-1-after.log) | LOG | `experiments/2026-10-28-edge-scalability/performance` |
| [paired-1-before.log](experiments/2026-10-28-edge-scalability/performance/paired-1-before.log) | LOG | `experiments/2026-10-28-edge-scalability/performance` |
| [paired-2-after.log](experiments/2026-10-28-edge-scalability/performance/paired-2-after.log) | LOG | `experiments/2026-10-28-edge-scalability/performance` |
| [paired-2-before.log](experiments/2026-10-28-edge-scalability/performance/paired-2-before.log) | LOG | `experiments/2026-10-28-edge-scalability/performance` |
| [paired-3-after.log](experiments/2026-10-28-edge-scalability/performance/paired-3-after.log) | LOG | `experiments/2026-10-28-edge-scalability/performance` |
| [paired-3-before.log](experiments/2026-10-28-edge-scalability/performance/paired-3-before.log) | LOG | `experiments/2026-10-28-edge-scalability/performance` |
| [paired-results.json](experiments/2026-10-28-edge-scalability/performance/paired-results.json) | JSON | `experiments/2026-10-28-edge-scalability/performance` |
| [regressions-race.txt](experiments/2026-10-28-edge-scalability/performance/regressions-race.txt) | TXT | `experiments/2026-10-28-edge-scalability/performance` |
| [summary.json](experiments/2026-10-28-edge-scalability/performance/summary.json) | JSON | `experiments/2026-10-28-edge-scalability/performance` |
| [task-summary.json](experiments/2026-10-28-edge-scalability/task-summary.json) | JSON | `experiments/2026-10-28-edge-scalability` |
| [affected-packages.jsonl](experiments/2026-10-28-edge-scalability/verification/affected-packages.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/verification` |
| [long-campaign.jsonl](experiments/2026-10-28-edge-scalability/verification/long-campaign.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/verification` |
| [runner-source.py.txt](experiments/2026-10-28-edge-scalability/verification/runner-source.py.txt) | TXT | `experiments/2026-10-28-edge-scalability/verification` |
| [sha256.json](experiments/2026-10-28-edge-scalability/verification/sha256.json) | JSON | `experiments/2026-10-28-edge-scalability/verification` |
| [source-sha256.json](experiments/2026-10-28-edge-scalability/verification/source-sha256.json) | JSON | `experiments/2026-10-28-edge-scalability/verification` |
| [summary.json](experiments/2026-10-28-edge-scalability/verification/summary.json) | JSON | `experiments/2026-10-28-edge-scalability/verification` |
| [affected-packages.jsonl](experiments/2026-10-28-edge-scalability/verification-final/affected-packages.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/verification-final` |
| [long-campaign.jsonl](experiments/2026-10-28-edge-scalability/verification-final/long-campaign.jsonl) | JSONL | `experiments/2026-10-28-edge-scalability/verification-final` |
| [runner-source.py.txt](experiments/2026-10-28-edge-scalability/verification-final/runner-source.py.txt) | TXT | `experiments/2026-10-28-edge-scalability/verification-final` |
| [sha256.json](experiments/2026-10-28-edge-scalability/verification-final/sha256.json) | JSON | `experiments/2026-10-28-edge-scalability/verification-final` |
| [source-sha256.json](experiments/2026-10-28-edge-scalability/verification-final/source-sha256.json) | JSON | `experiments/2026-10-28-edge-scalability/verification-final` |
| [summary.json](experiments/2026-10-28-edge-scalability/verification-final/summary.json) | JSON | `experiments/2026-10-28-edge-scalability/verification-final` |
| [archive-sha256.json](experiments/2026-10-28-edge-task-validation/archive-sha256.json) | JSON | `experiments/2026-10-28-edge-task-validation` |
| [book-check.txt](experiments/2026-10-28-edge-task-validation/checks-final/book-check.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [catalog-build.txt](experiments/2026-10-28-edge-task-validation/checks-final/catalog-build.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [catalog-check.txt](experiments/2026-10-28-edge-task-validation/checks-final/catalog-check.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [diff-check.txt](experiments/2026-10-28-edge-task-validation/checks-final/diff-check.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [gazebo-archive.txt](experiments/2026-10-28-edge-task-validation/checks-final/gazebo-archive.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [go-docs.txt](experiments/2026-10-28-edge-task-validation/checks-final/go-docs.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [gofmt.txt](experiments/2026-10-28-edge-task-validation/checks-final/gofmt.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [peer-archive.txt](experiments/2026-10-28-edge-task-validation/checks-final/peer-archive.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [python-docs.txt](experiments/2026-10-28-edge-task-validation/checks-final/python-docs.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [ruff.txt](experiments/2026-10-28-edge-task-validation/checks-final/ruff.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [staged-diff-check.txt](experiments/2026-10-28-edge-task-validation/checks-final/staged-diff-check.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [summary.json](experiments/2026-10-28-edge-task-validation/checks-final/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/checks-final` |
| [book-check.txt](experiments/2026-10-28-edge-task-validation/checks-initial/book-check.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [catalog-build.txt](experiments/2026-10-28-edge-task-validation/checks-initial/catalog-build.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [catalog-check.txt](experiments/2026-10-28-edge-task-validation/checks-initial/catalog-check.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [diff-check.txt](experiments/2026-10-28-edge-task-validation/checks-initial/diff-check.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [gazebo-archive.txt](experiments/2026-10-28-edge-task-validation/checks-initial/gazebo-archive.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [go-docs.txt](experiments/2026-10-28-edge-task-validation/checks-initial/go-docs.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [gofmt.txt](experiments/2026-10-28-edge-task-validation/checks-initial/gofmt.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [peer-archive.txt](experiments/2026-10-28-edge-task-validation/checks-initial/peer-archive.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [python-docs.txt.gz](experiments/2026-10-28-edge-task-validation/checks-initial/python-docs.txt.gz) | GZ | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [ruff.txt](experiments/2026-10-28-edge-task-validation/checks-initial/ruff.txt) | TXT | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [summary.json](experiments/2026-10-28-edge-task-validation/checks-initial/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/checks-initial` |
| [MANIFEST.json](experiments/2026-10-28-edge-task-validation/cost/MANIFEST.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost` |
| [affected-race-test.log](experiments/2026-10-28-edge-task-validation/cost/affected-race-test.log) | LOG | `experiments/2026-10-28-edge-task-validation/cost` |
| [cases.json](experiments/2026-10-28-edge-task-validation/cost/after/cases.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost/after` |
| [model-usage.jsonl](experiments/2026-10-28-edge-task-validation/cost/after/model-usage.jsonl) | JSONL | `experiments/2026-10-28-edge-task-validation/cost/after` |
| [summary.json](experiments/2026-10-28-edge-task-validation/cost/after/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost/after` |
| [usage-report.json](experiments/2026-10-28-edge-task-validation/cost/after/usage-report.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost/after` |
| [after-source.json](experiments/2026-10-28-edge-task-validation/cost/after-source.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost` |
| [baseline-source.json](experiments/2026-10-28-edge-task-validation/cost/baseline-source.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost` |
| [baseline-test.log](experiments/2026-10-28-edge-task-validation/cost/baseline-test.log) | LOG | `experiments/2026-10-28-edge-task-validation/cost` |
| [cases.json](experiments/2026-10-28-edge-task-validation/cost/before/cases.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost/before` |
| [model-usage.jsonl](experiments/2026-10-28-edge-task-validation/cost/before/model-usage.jsonl) | JSONL | `experiments/2026-10-28-edge-task-validation/cost/before` |
| [summary.json](experiments/2026-10-28-edge-task-validation/cost/before/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost/before` |
| [usage-report.json](experiments/2026-10-28-edge-task-validation/cost/before/usage-report.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost/before` |
| [commands.json](experiments/2026-10-28-edge-task-validation/cost/commands.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost` |
| [comparison.json](experiments/2026-10-28-edge-task-validation/cost/comparison.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost` |
| [final-source-after.json](experiments/2026-10-28-edge-task-validation/cost/final-source-after.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost` |
| [final-source-before.json](experiments/2026-10-28-edge-task-validation/cost/final-source-before.json) | JSON | `experiments/2026-10-28-edge-task-validation/cost` |
| [final-targeted.jsonl](experiments/2026-10-28-edge-task-validation/cost/final-targeted.jsonl) | JSONL | `experiments/2026-10-28-edge-task-validation/cost` |
| [loop-baseline-test.log](experiments/2026-10-28-edge-task-validation/cost/loop-baseline-test.log) | LOG | `experiments/2026-10-28-edge-task-validation/cost` |
| [llmdecider.go.txt](experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/actionloop/llmdecider.go.txt) | TXT | `experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/actionloop` |
| [loop.go.txt](experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/actionloop/loop.go.txt) | TXT | `experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/actionloop` |
| [usage_log.go.txt](experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/actionloop/usage_log.go.txt) | TXT | `experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/actionloop` |
| [usage_log_test.go.txt](experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/actionloop/usage_log_test.go.txt) | TXT | `experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/actionloop` |
| [cost_campaign_test.go.txt](experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/capabilityagent/cost_campaign_test.go.txt) | TXT | `experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/capabilityagent` |
| [planner.go.txt](experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/capabilityagent/planner.go.txt) | TXT | `experiments/2026-10-28-edge-task-validation/cost/sources/after/internal/capabilityagent` |
| [cost_campaign_test.go.txt](experiments/2026-10-28-edge-task-validation/cost/sources/before/cost_campaign_test.go.txt) | TXT | `experiments/2026-10-28-edge-task-validation/cost/sources/before` |
| [environment.json](experiments/2026-10-28-edge-task-validation/gazebo/environment.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo` |
| [frozen-source-manifest.json](experiments/2026-10-28-edge-task-validation/gazebo/frozen-source-manifest.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo` |
| [patch-manifest.json](experiments/2026-10-28-edge-task-validation/gazebo/patch-manifest.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo` |
| [evidence.tar.gz](experiments/2026-10-28-edge-task-validation/gazebo/public/evidence.tar.gz) | GZ | `experiments/2026-10-28-edge-task-validation/gazebo/public` |
| [manifest.json](experiments/2026-10-28-edge-task-validation/gazebo/public/manifest.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/public` |
| [redactions.json](experiments/2026-10-28-edge-task-validation/gazebo/public/redactions.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/public` |
| [verify.py](experiments/2026-10-28-edge-task-validation/gazebo/public/verify.py) | PY | `experiments/2026-10-28-edge-task-validation/gazebo/public` |
| [restoration.json](experiments/2026-10-28-edge-task-validation/gazebo/restoration.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo` |
| [run-env.sh](experiments/2026-10-28-edge-task-validation/gazebo/runner-source/run-env.sh) | SH | `experiments/2026-10-28-edge-task-validation/gazebo/runner-source` |
| [run-field-002.py](experiments/2026-10-28-edge-task-validation/gazebo/runner-source/run-field-002.py) | PY | `experiments/2026-10-28-edge-task-validation/gazebo/runner-source` |
| [run-field.py](experiments/2026-10-28-edge-task-validation/gazebo/runner-source/run-field.py) | PY | `experiments/2026-10-28-edge-task-validation/gazebo/runner-source` |
| [run-stops-003.py](experiments/2026-10-28-edge-task-validation/gazebo/runner-source/run-stops-003.py) | PY | `experiments/2026-10-28-edge-task-validation/gazebo/runner-source` |
| [stop-regression.txt](experiments/2026-10-28-edge-task-validation/gazebo/stop-regression.txt) | TXT | `experiments/2026-10-28-edge-task-validation/gazebo` |
| [orchestration.json](experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/orchestration.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence` |
| [input-manifest.json](experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/analysis/input-manifest.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/analysis` |
| [metrics.json](experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/analysis/metrics.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/analysis` |
| [summary.json](experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/campaign-001/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/campaign-001` |
| [orchestration.json](experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/orchestration.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002` |
| [summary.json](experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/stops-001/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-002/stops-001` |
| [orchestration.json](experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-003-stops/orchestration.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-003-stops` |
| [summary.json](experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-003-stops/stops-001/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/gazebo/summaries/evidence/run-003-stops/stops-001` |
| [pytest.txt](experiments/2026-10-28-edge-task-validation/harness/pytest.txt) | TXT | `experiments/2026-10-28-edge-task-validation/harness` |
| [results.xml](experiments/2026-10-28-edge-task-validation/harness/results.xml) | XML | `experiments/2026-10-28-edge-task-validation/harness` |
| [summary.json](experiments/2026-10-28-edge-task-validation/harness/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/harness` |
| [SHA256SUMS](experiments/2026-10-28-edge-task-validation/peer/SHA256SUMS) |  | `experiments/2026-10-28-edge-task-validation/peer` |
| [evidence-manifest.json](experiments/2026-10-28-edge-task-validation/peer/evidence-manifest.json) | JSON | `experiments/2026-10-28-edge-task-validation/peer` |
| [exchange-changes.patch.gz](experiments/2026-10-28-edge-task-validation/peer/exchange-changes.patch.gz) | GZ | `experiments/2026-10-28-edge-task-validation/peer` |
| [raw-evidence.tar.gz](experiments/2026-10-28-edge-task-validation/peer/raw-evidence.tar.gz) | GZ | `experiments/2026-10-28-edge-task-validation/peer` |
| [summary.json](experiments/2026-10-28-edge-task-validation/peer/summary.json) | JSON | `experiments/2026-10-28-edge-task-validation/peer` |
| [verify_evidence.py](experiments/2026-10-28-edge-task-validation/peer/verify_evidence.py) | PY | `experiments/2026-10-28-edge-task-validation/peer` |
| [metadata.json](experiments/2026-10-28-edge-task-validation/verification/go-final/metadata.json) | JSON | `experiments/2026-10-28-edge-task-validation/verification/go-final` |
| [output.txt](experiments/2026-10-28-edge-task-validation/verification/go-final/output.txt) | TXT | `experiments/2026-10-28-edge-task-validation/verification/go-final` |
| [result.json](experiments/2026-10-28-edge-task-validation/verification/go-final/result.json) | JSON | `experiments/2026-10-28-edge-task-validation/verification/go-final` |
| [source-after.json](experiments/2026-10-28-edge-task-validation/verification/go-final/source-after.json) | JSON | `experiments/2026-10-28-edge-task-validation/verification/go-final` |
| [source-before.json](experiments/2026-10-28-edge-task-validation/verification/go-final/source-before.json) | JSON | `experiments/2026-10-28-edge-task-validation/verification/go-final` |
| [metadata.json](experiments/2026-10-28-edge-task-validation/verification/python-final/metadata.json) | JSON | `experiments/2026-10-28-edge-task-validation/verification/python-final` |
| [output.txt](experiments/2026-10-28-edge-task-validation/verification/python-final/output.txt) | TXT | `experiments/2026-10-28-edge-task-validation/verification/python-final` |
| [result.json](experiments/2026-10-28-edge-task-validation/verification/python-final/result.json) | JSON | `experiments/2026-10-28-edge-task-validation/verification/python-final` |
| [results.xml](experiments/2026-10-28-edge-task-validation/verification/python-final/results.xml) | XML | `experiments/2026-10-28-edge-task-validation/verification/python-final` |
| [source-after.json](experiments/2026-10-28-edge-task-validation/verification/python-final/source-after.json) | JSON | `experiments/2026-10-28-edge-task-validation/verification/python-final` |
| [source-before.json](experiments/2026-10-28-edge-task-validation/verification/python-final/source-before.json) | JSON | `experiments/2026-10-28-edge-task-validation/verification/python-final` |
| [run.py](experiments/2026-10-28-edge-task-validation/verification/run.py) | PY | `experiments/2026-10-28-edge-task-validation/verification` |
| [context-comparison.png](experiments/assets/2026-09-21-agent-context-evaluation/context-comparison.png) | PNG | `experiments/assets/2026-09-21-agent-context-evaluation` |
| [factorial-stage-comparison.png](experiments/assets/2026-09-21-agent-context-evaluation/factorial-stage-comparison.png) | PNG | `experiments/assets/2026-09-21-agent-context-evaluation` |
| [stage-comparison.png](experiments/assets/2026-09-21-agent-context-evaluation/stage-comparison.png) | PNG | `experiments/assets/2026-09-21-agent-context-evaluation` |
| [comparison.svg](experiments/assets/2026-09-21-grounded-verification/comparison.svg) | SVG | `experiments/assets/2026-09-21-grounded-verification` |
| [2026-10-13-semantic-execution.png](experiments/assets/2026-10-13-semantic-execution.png) | PNG | `experiments/assets` |
| [2026-10-13-semantic-execution.svg](experiments/assets/2026-10-13-semantic-execution.svg) | SVG | `experiments/assets` |
| [first-scan.png](experiments/assets/2026-10-17-exploration-first-scan/first-scan.png) | PNG | `experiments/assets/2026-10-17-exploration-first-scan` |
| [first-scan.svg](experiments/assets/2026-10-17-exploration-first-scan/first-scan.svg) | SVG | `experiments/assets/2026-10-17-exploration-first-scan` |
| [plot-source.py](experiments/assets/2026-10-17-exploration-first-scan/plot-source.py) | PY | `experiments/assets/2026-10-17-exploration-first-scan` |
| [provenance.json](experiments/assets/2026-10-17-exploration-first-scan/provenance.json) | JSON | `experiments/assets/2026-10-17-exploration-first-scan` |
| [registered-odometry.csv](experiments/assets/2026-10-17-exploration-first-scan/registered-odometry.csv) | CSV | `experiments/assets/2026-10-17-exploration-first-scan` |
| [first-scan.png](experiments/assets/2026-10-17-exploration-first-scan-four-runs/first-scan.png) | PNG | `experiments/assets/2026-10-17-exploration-first-scan-four-runs` |
| [first-scan.svg](experiments/assets/2026-10-17-exploration-first-scan-four-runs/first-scan.svg) | SVG | `experiments/assets/2026-10-17-exploration-first-scan-four-runs` |
| [provenance.json](experiments/assets/2026-10-17-exploration-first-scan-four-runs/provenance.json) | JSON | `experiments/assets/2026-10-17-exploration-first-scan-four-runs` |
| [registered-odometry.csv](experiments/assets/2026-10-17-exploration-first-scan-four-runs/registered-odometry.csv) | CSV | `experiments/assets/2026-10-17-exploration-first-scan-four-runs` |
| [paired-exploration.png](experiments/assets/2026-10-18-spatial-coverage-upgrade/development/paired-exploration.png) | PNG | `experiments/assets/2026-10-18-spatial-coverage-upgrade/development` |
| [paired-exploration.svg](experiments/assets/2026-10-18-spatial-coverage-upgrade/development/paired-exploration.svg) | SVG | `experiments/assets/2026-10-18-spatial-coverage-upgrade/development` |
| [plotted-values.csv](experiments/assets/2026-10-18-spatial-coverage-upgrade/development/plotted-values.csv) | CSV | `experiments/assets/2026-10-18-spatial-coverage-upgrade/development` |
| [provenance.json](experiments/assets/2026-10-18-spatial-coverage-upgrade/development/provenance.json) | JSON | `experiments/assets/2026-10-18-spatial-coverage-upgrade/development` |
| [paired-exploration.png](experiments/assets/2026-10-18-spatial-coverage-upgrade/holdout/paired-exploration.png) | PNG | `experiments/assets/2026-10-18-spatial-coverage-upgrade/holdout` |
| [paired-exploration.svg](experiments/assets/2026-10-18-spatial-coverage-upgrade/holdout/paired-exploration.svg) | SVG | `experiments/assets/2026-10-18-spatial-coverage-upgrade/holdout` |
| [plotted-values.csv](experiments/assets/2026-10-18-spatial-coverage-upgrade/holdout/plotted-values.csv) | CSV | `experiments/assets/2026-10-18-spatial-coverage-upgrade/holdout` |
| [provenance.json](experiments/assets/2026-10-18-spatial-coverage-upgrade/holdout/provenance.json) | JSON | `experiments/assets/2026-10-18-spatial-coverage-upgrade/holdout` |
| [paired-exploration.png](experiments/assets/2026-10-18-spatial-exploration/paired-exploration.png) | PNG | `experiments/assets/2026-10-18-spatial-exploration` |
| [paired-exploration.svg](experiments/assets/2026-10-18-spatial-exploration/paired-exploration.svg) | SVG | `experiments/assets/2026-10-18-spatial-exploration` |
| [plotted-values.csv](experiments/assets/2026-10-18-spatial-exploration/plotted-values.csv) | CSV | `experiments/assets/2026-10-18-spatial-exploration` |
| [provenance.json](experiments/assets/2026-10-18-spatial-exploration/provenance.json) | JSON | `experiments/assets/2026-10-18-spatial-exploration` |
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
| [bounded-steps.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/001/bounded-steps.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/001` |
| [environment-postrun.json](experiments/evidence/2026-10-17-exploration-first-scan/001/environment-postrun.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001` |
| [environment-preregistration.json](experiments/evidence/2026-10-17-exploration-first-scan/001/environment-preregistration.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001` |
| [external-raw.json](experiments/evidence/2026-10-17-exploration-first-scan/001/external-raw.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001` |
| [compose.json](experiments/evidence/2026-10-17-exploration-first-scan/001/gazebo/compose.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/gazebo` |
| [calls.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/calls.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [environment.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/environment.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [final-state.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/final-state.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [initial-state.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/initial-state.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [map-artifact-checks.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-artifact-checks.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [lod0.bin](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud/lod0.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud` |
| [lod1.bin](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud/lod1.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud` |
| [lod2.bin](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud/lod2.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud` |
| [lod3.bin](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud/lod3.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud` |
| [lod4.bin](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud/lod4.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/cloud` |
| [occupancy.png](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/grid/occupancy.png) | PNG | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/grid` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package` |
| [map.pgm](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/navigation/map.pgm) | PGM | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/navigation` |
| [map.yaml](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/navigation/map.yaml) | YAML | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/navigation` |
| [source.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/navigation/source.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/navigation` |
| [objects.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/objects.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package` |
| [semantics.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/semantics.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package` |
| [slam-keyframes.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/slam-keyframes.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package` |
| [slam-session.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/slam-session.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package` |
| [trajectory.geojson](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package/trajectory.geojson) | GEOJSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/map-package` |
| [navigation-final.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/navigation-final.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [preregistration.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/preregistration.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [progress.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/progress.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [report.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/report.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [runtime-info.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/runtime-info.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [service-catalog.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/service-catalog.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [source-after.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/source-after.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [source-before.json](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/source-before.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [source-dirty.patch](experiments/evidence/2026-10-17-exploration-first-scan/001/runtime/source-dirty.patch) | PATCH | `experiments/evidence/2026-10-17-exploration-first-scan/001/runtime` |
| [source-dirty.patch](experiments/evidence/2026-10-17-exploration-first-scan/001/source-dirty.patch) | PATCH | `experiments/evidence/2026-10-17-exploration-first-scan/001` |
| [source-manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/001/source-manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/001` |
| [bounded-steps.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/002/bounded-steps.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/002` |
| [environment-postrun.json](experiments/evidence/2026-10-17-exploration-first-scan/002/environment-postrun.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002` |
| [environment-preregistration.json](experiments/evidence/2026-10-17-exploration-first-scan/002/environment-preregistration.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002` |
| [external-raw.json](experiments/evidence/2026-10-17-exploration-first-scan/002/external-raw.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002` |
| [compose.json](experiments/evidence/2026-10-17-exploration-first-scan/002/gazebo/compose.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/gazebo` |
| [calls.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/calls.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [environment.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/environment.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [final-state.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/final-state.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [initial-state.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/initial-state.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [map-artifact-checks.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-artifact-checks.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [lod0.bin](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud/lod0.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud` |
| [lod1.bin](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud/lod1.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud` |
| [lod2.bin](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud/lod2.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud` |
| [lod3.bin](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud/lod3.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud` |
| [lod4.bin](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud/lod4.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/cloud` |
| [occupancy.png](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/grid/occupancy.png) | PNG | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/grid` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package` |
| [map.pgm](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/navigation/map.pgm) | PGM | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/navigation` |
| [map.yaml](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/navigation/map.yaml) | YAML | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/navigation` |
| [source.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/navigation/source.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/navigation` |
| [objects.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/objects.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package` |
| [semantics.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/semantics.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package` |
| [slam-keyframes.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/slam-keyframes.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package` |
| [slam-session.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/slam-session.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package` |
| [trajectory.geojson](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package/trajectory.geojson) | GEOJSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/map-package` |
| [navigation-final.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/navigation-final.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [preregistration.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/preregistration.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [progress.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/progress.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [report.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/report.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [runtime-info.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/runtime-info.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [service-catalog.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/service-catalog.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [source-after.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/source-after.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [source-before.json](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/source-before.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [source-dirty.patch](experiments/evidence/2026-10-17-exploration-first-scan/002/runtime/source-dirty.patch) | PATCH | `experiments/evidence/2026-10-17-exploration-first-scan/002/runtime` |
| [source-dirty.patch](experiments/evidence/2026-10-17-exploration-first-scan/002/source-dirty.patch) | PATCH | `experiments/evidence/2026-10-17-exploration-first-scan/002` |
| [source-manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/002/source-manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/002` |
| [bounded-steps.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/003/bounded-steps.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/003` |
| [environment-postrun.json](experiments/evidence/2026-10-17-exploration-first-scan/003/environment-postrun.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003` |
| [environment-preregistration.json](experiments/evidence/2026-10-17-exploration-first-scan/003/environment-preregistration.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003` |
| [external-raw.json](experiments/evidence/2026-10-17-exploration-first-scan/003/external-raw.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003` |
| [compose.json](experiments/evidence/2026-10-17-exploration-first-scan/003/gazebo/compose.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/gazebo` |
| [calls.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/calls.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [environment.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/environment.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [final-state.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/final-state.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [initial-state.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/initial-state.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [map-artifact-checks.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-artifact-checks.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [lod0.bin](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud/lod0.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud` |
| [lod1.bin](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud/lod1.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud` |
| [lod2.bin](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud/lod2.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud` |
| [lod3.bin](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud/lod3.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud` |
| [lod4.bin](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud/lod4.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/cloud` |
| [occupancy.png](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/grid/occupancy.png) | PNG | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/grid` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package` |
| [map.pgm](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/navigation/map.pgm) | PGM | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/navigation` |
| [map.yaml](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/navigation/map.yaml) | YAML | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/navigation` |
| [source.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/navigation/source.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/navigation` |
| [objects.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/objects.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package` |
| [semantics.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/semantics.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package` |
| [slam-keyframes.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/slam-keyframes.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package` |
| [slam-session.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/slam-session.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package` |
| [trajectory.geojson](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package/trajectory.geojson) | GEOJSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/map-package` |
| [navigation-final.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/navigation-final.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [preregistration.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/preregistration.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [progress.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/progress.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [report.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/report.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [runtime-info.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/runtime-info.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [service-catalog.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/service-catalog.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [source-after.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/source-after.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [source-before.json](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/source-before.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [source-dirty.patch](experiments/evidence/2026-10-17-exploration-first-scan/003/runtime/source-dirty.patch) | PATCH | `experiments/evidence/2026-10-17-exploration-first-scan/003/runtime` |
| [source-dirty.patch](experiments/evidence/2026-10-17-exploration-first-scan/003/source-dirty.patch) | PATCH | `experiments/evidence/2026-10-17-exploration-first-scan/003` |
| [source-manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/003/source-manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/003` |
| [bounded-steps.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/004/bounded-steps.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/004` |
| [environment-postrun.json](experiments/evidence/2026-10-17-exploration-first-scan/004/environment-postrun.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004` |
| [environment-preregistration.json](experiments/evidence/2026-10-17-exploration-first-scan/004/environment-preregistration.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004` |
| [external-raw.json](experiments/evidence/2026-10-17-exploration-first-scan/004/external-raw.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004` |
| [compose.json](experiments/evidence/2026-10-17-exploration-first-scan/004/gazebo/compose.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/gazebo` |
| [calls.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/calls.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [environment.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/environment.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [final-state.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/final-state.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [initial-state.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/initial-state.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [map-artifact-checks.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-artifact-checks.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [lod0.bin](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud/lod0.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud` |
| [lod1.bin](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud/lod1.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud` |
| [lod2.bin](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud/lod2.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud` |
| [lod3.bin](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud/lod3.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud` |
| [lod4.bin](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud/lod4.bin) | BIN | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/cloud` |
| [occupancy.png](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/grid/occupancy.png) | PNG | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/grid` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package` |
| [map.pgm](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/navigation/map.pgm) | PGM | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/navigation` |
| [map.yaml](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/navigation/map.yaml) | YAML | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/navigation` |
| [source.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/navigation/source.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/navigation` |
| [objects.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/objects.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package` |
| [semantics.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/semantics.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package` |
| [slam-keyframes.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/slam-keyframes.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package` |
| [slam-session.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/slam-session.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package` |
| [trajectory.geojson](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package/trajectory.geojson) | GEOJSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/map-package` |
| [navigation-final.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/navigation-final.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [preregistration.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/preregistration.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [progress.jsonl](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/progress.jsonl) | JSONL | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [report.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/report.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [runtime-info.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/runtime-info.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [service-catalog.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/service-catalog.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [source-after.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/source-after.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [source-before.json](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/source-before.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [source-dirty.patch](experiments/evidence/2026-10-17-exploration-first-scan/004/runtime/source-dirty.patch) | PATCH | `experiments/evidence/2026-10-17-exploration-first-scan/004/runtime` |
| [source-dirty.patch](experiments/evidence/2026-10-17-exploration-first-scan/004/source-dirty.patch) | PATCH | `experiments/evidence/2026-10-17-exploration-first-scan/004` |
| [source-manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/004/source-manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/004` |
| [environment-restored.json](experiments/evidence/2026-10-17-exploration-first-scan/environment-restored.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan` |
| [manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan` |
| [motion-stop-checks.json](experiments/evidence/2026-10-17-exploration-first-scan/motion-stop-checks.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan` |
| [full-gate.log](experiments/evidence/2026-10-17-exploration-first-scan/post004-software/full-gate.log) | LOG | `experiments/evidence/2026-10-17-exploration-first-scan/post004-software` |
| [patch-from-004.diff](experiments/evidence/2026-10-17-exploration-first-scan/post004-software/patch-from-004.diff) | DIFF | `experiments/evidence/2026-10-17-exploration-first-scan/post004-software` |
| [source-manifest.json](experiments/evidence/2026-10-17-exploration-first-scan/post004-software/source-manifest.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/post004-software` |
| [validation.json](experiments/evidence/2026-10-17-exploration-first-scan/post004-software/validation.json) | JSON | `experiments/evidence/2026-10-17-exploration-first-scan/post004-software` |
| [context-replay.jsonl.gz](experiments/evidence/2026-10-18-communication-audit/context-replay.jsonl.gz) | GZ | `experiments/evidence/2026-10-18-communication-audit` |
| [go.jsonl.gz](experiments/evidence/2026-10-18-communication-audit/go.jsonl.gz) | GZ | `experiments/evidence/2026-10-18-communication-audit` |
| [harness.jsonl.gz](experiments/evidence/2026-10-18-communication-audit/harness.jsonl.gz) | GZ | `experiments/evidence/2026-10-18-communication-audit` |
| [host-race-selection.json](experiments/evidence/2026-10-18-communication-audit/host-race-selection.json) | JSON | `experiments/evidence/2026-10-18-communication-audit` |
| [host-race.jsonl.gz](experiments/evidence/2026-10-18-communication-audit/host-race.jsonl.gz) | GZ | `experiments/evidence/2026-10-18-communication-audit` |
| [interop.log](experiments/evidence/2026-10-18-communication-audit/interop.log) | LOG | `experiments/evidence/2026-10-18-communication-audit` |
| [manifest.json](experiments/evidence/2026-10-18-communication-audit/manifest.json) | JSON | `experiments/evidence/2026-10-18-communication-audit` |
| [process-cloud.log](experiments/evidence/2026-10-18-communication-audit/process-cloud.log) | LOG | `experiments/evidence/2026-10-18-communication-audit` |
| [process-events.json](experiments/evidence/2026-10-18-communication-audit/process-events.json) | JSON | `experiments/evidence/2026-10-18-communication-audit` |
| [process-frozen-build-source.json](experiments/evidence/2026-10-18-communication-audit/process-frozen-build-source.json) | JSON | `experiments/evidence/2026-10-18-communication-audit` |
| [process-legged.log](experiments/evidence/2026-10-18-communication-audit/process-legged.log) | LOG | `experiments/evidence/2026-10-18-communication-audit` |
| [process-planning-advice.jsonl](experiments/evidence/2026-10-18-communication-audit/process-planning-advice.jsonl) | JSONL | `experiments/evidence/2026-10-18-communication-audit` |
| [process-summary.json](experiments/evidence/2026-10-18-communication-audit/process-summary.json) | JSON | `experiments/evidence/2026-10-18-communication-audit` |
| [process-uav.log](experiments/evidence/2026-10-18-communication-audit/process-uav.log) | LOG | `experiments/evidence/2026-10-18-communication-audit` |
| [process-ugv.log](experiments/evidence/2026-10-18-communication-audit/process-ugv.log) | LOG | `experiments/evidence/2026-10-18-communication-audit` |
| [process.log](experiments/evidence/2026-10-18-communication-audit/process.log) | LOG | `experiments/evidence/2026-10-18-communication-audit` |
| [python.log](experiments/evidence/2026-10-18-communication-audit/python.log) | LOG | `experiments/evidence/2026-10-18-communication-audit` |
| [race.jsonl.gz](experiments/evidence/2026-10-18-communication-audit/race.jsonl.gz) | GZ | `experiments/evidence/2026-10-18-communication-audit` |
| [summary.json](experiments/evidence/2026-10-18-communication-audit/summary.json) | JSON | `experiments/evidence/2026-10-18-communication-audit` |
| [archive-members.json](experiments/evidence/2026-10-18-edge-swarm-readiness/archive-members.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [attempts.json](experiments/evidence/2026-10-18-edge-swarm-readiness/attempts.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [container-restoration.json](experiments/evidence/2026-10-18-edge-swarm-readiness/container-restoration.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [credential-audit.json](experiments/evidence/2026-10-18-edge-swarm-readiness/credential-audit.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [export-receipt.json](experiments/evidence/2026-10-18-edge-swarm-readiness/export-receipt.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [final-validation.json](experiments/evidence/2026-10-18-edge-swarm-readiness/final-validation.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [master-manifest.json](experiments/evidence/2026-10-18-edge-swarm-readiness/master-manifest.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [post-review-members.json](experiments/evidence/2026-10-18-edge-swarm-readiness/post-review-members.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [post-review.tar.gz](experiments/evidence/2026-10-18-edge-swarm-readiness/post-review.tar.gz) | GZ | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [raw-evidence.tar.gz](experiments/evidence/2026-10-18-edge-swarm-readiness/raw-evidence.tar.gz) | GZ | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [redactions.json](experiments/evidence/2026-10-18-edge-swarm-readiness/redactions.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [ros-observation-audit.json](experiments/evidence/2026-10-18-edge-swarm-readiness/ros-observation-audit.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [ros-summary.json](experiments/evidence/2026-10-18-edge-swarm-readiness/ros-summary.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [ros-task-metrics.csv](experiments/evidence/2026-10-18-edge-swarm-readiness/ros-task-metrics.csv) | CSV | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [software-summary.json](experiments/evidence/2026-10-18-edge-swarm-readiness/software-summary.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [trio-rerun-comparison.json](experiments/evidence/2026-10-18-edge-swarm-readiness/trio-rerun-comparison.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [trio-summary.json](experiments/evidence/2026-10-18-edge-swarm-readiness/trio-summary.json) | JSON | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [verify.py](experiments/evidence/2026-10-18-edge-swarm-readiness/verify.py) | PY | `experiments/evidence/2026-10-18-edge-swarm-readiness` |
| [attempt-ledger.json](experiments/evidence/2026-10-18-gazebo-field-validation/attempt-ledger.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [evidence.tar.gz](experiments/evidence/2026-10-18-gazebo-field-validation/bundle/evidence.tar.gz) | GZ | `experiments/evidence/2026-10-18-gazebo-field-validation/bundle` |
| [manifest.json](experiments/evidence/2026-10-18-gazebo-field-validation/bundle/manifest.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation/bundle` |
| [redactions.json](experiments/evidence/2026-10-18-gazebo-field-validation/bundle/redactions.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation/bundle` |
| [verify.py](experiments/evidence/2026-10-18-gazebo-field-validation/bundle/verify.py) | PY | `experiments/evidence/2026-10-18-gazebo-field-validation/bundle` |
| [campaign-007-metrics.json](experiments/evidence/2026-10-18-gazebo-field-validation/campaign-007-metrics.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [campaign-008-metrics.json](experiments/evidence/2026-10-18-gazebo-field-validation/campaign-008-metrics.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [container-restoration.json](experiments/evidence/2026-10-18-gazebo-field-validation/container-restoration.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [credential-audit-summary.json](experiments/evidence/2026-10-18-gazebo-field-validation/credential-audit-summary.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [readable-files-manifest.json](experiments/evidence/2026-10-18-gazebo-field-validation/readable-files-manifest.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [stopping-metrics-final.json](experiments/evidence/2026-10-18-gazebo-field-validation/stopping-metrics-final.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [stops-004-summary.json](experiments/evidence/2026-10-18-gazebo-field-validation/stops-004-summary.json) | JSON | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [trajectory-008.csv](experiments/evidence/2026-10-18-gazebo-field-validation/trajectory-008.csv) | CSV | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [trajectory-008.png](experiments/evidence/2026-10-18-gazebo-field-validation/trajectory-008.png) | PNG | `experiments/evidence/2026-10-18-gazebo-field-validation` |
| [bundle-verification.log](experiments/evidence/2026-10-18-gazebo-field-validation/validation/bundle-verification.log) | LOG | `experiments/evidence/2026-10-18-gazebo-field-validation/validation` |
| [final-docs.log](experiments/evidence/2026-10-18-gazebo-field-validation/validation/final-docs.log) | LOG | `experiments/evidence/2026-10-18-gazebo-field-validation/validation` |
| [final-lint.log](experiments/evidence/2026-10-18-gazebo-field-validation/validation/final-lint.log) | LOG | `experiments/evidence/2026-10-18-gazebo-field-validation/validation` |
| [final-targeted.log](experiments/evidence/2026-10-18-gazebo-field-validation/validation/final-targeted.log) | LOG | `experiments/evidence/2026-10-18-gazebo-field-validation/validation` |
| [method.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/method.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development` |
| [paired-differences.csv](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/paired-differences.csv) | CSV | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development` |
| [paired-differences.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/paired-differences.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development` |
| [paired-effects.csv](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/paired-effects.csv) | CSV | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development` |
| [provenance.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/provenance.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development` |
| [analyze_spatial_study.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/source/analyze_spatial_study.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/source` |
| [spatial_exploration_benchmark.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/source/spatial_exploration_benchmark.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/source` |
| [summary.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/summary.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development` |
| [variants.csv](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development/variants.csv) | CSV | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/development` |
| [method.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/method.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout` |
| [paired-differences.csv](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/paired-differences.csv) | CSV | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout` |
| [paired-differences.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/paired-differences.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout` |
| [paired-effects.csv](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/paired-effects.csv) | CSV | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout` |
| [provenance.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/provenance.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout` |
| [analyze_spatial_study.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/source/analyze_spatial_study.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/source` |
| [spatial_exploration_benchmark.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/source/spatial_exploration_benchmark.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/source` |
| [summary.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/summary.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout` |
| [variants.csv](experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout/variants.csv) | CSV | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/analysis/holdout` |
| [archive-members.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/archive-members.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors` |
| [plan.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/plan.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors` |
| [request.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/request.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors` |
| [spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/robot/gateway/tangying_robot_gateway/spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/robot/gateway/tangying_robot_gateway` |
| [spatial_exploration.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/robot/gateway/tangying_robot_gateway/spatial_exploration.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/robot/gateway/tangying_robot_gateway` |
| [spatial_mission.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/robot/gateway/tangying_robot_gateway/spatial_mission.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/robot/gateway/tangying_robot_gateway` |
| [spatial_navigation.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/robot/gateway/tangying_robot_gateway/spatial_navigation.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/robot/gateway/tangying_robot_gateway` |
| [plan_spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/scripts/plan_spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/disconnected-floors/source/scripts` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage` |
| [plan.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/plan.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage` |
| [request.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/request.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage` |
| [spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/robot/gateway/tangying_robot_gateway/spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/robot/gateway/tangying_robot_gateway` |
| [spatial_exploration.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/robot/gateway/tangying_robot_gateway/spatial_exploration.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/robot/gateway/tangying_robot_gateway` |
| [spatial_mission.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/robot/gateway/tangying_robot_gateway/spatial_mission.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/robot/gateway/tangying_robot_gateway` |
| [spatial_navigation.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/robot/gateway/tangying_robot_gateway/spatial_navigation.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/robot/gateway/tangying_robot_gateway` |
| [plan_spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/scripts/plan_spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/full-room-coverage/source/scripts` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp` |
| [plan.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/plan.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp` |
| [request.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/request.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp` |
| [spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/robot/gateway/tangying_robot_gateway/spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/robot/gateway/tangying_robot_gateway` |
| [spatial_exploration.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/robot/gateway/tangying_robot_gateway/spatial_exploration.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/robot/gateway/tangying_robot_gateway` |
| [spatial_mission.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/robot/gateway/tangying_robot_gateway/spatial_mission.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/robot/gateway/tangying_robot_gateway` |
| [spatial_navigation.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/robot/gateway/tangying_robot_gateway/spatial_navigation.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/robot/gateway/tangying_robot_gateway` |
| [plan_spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/scripts/plan_spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/layered-ramp/source/scripts` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground` |
| [plan.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/plan.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground` |
| [request.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/request.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground` |
| [spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/robot/gateway/tangying_robot_gateway/spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/robot/gateway/tangying_robot_gateway` |
| [spatial_exploration.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/robot/gateway/tangying_robot_gateway/spatial_exploration.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/robot/gateway/tangying_robot_gateway` |
| [spatial_mission.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/robot/gateway/tangying_robot_gateway/spatial_mission.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/robot/gateway/tangying_robot_gateway` |
| [spatial_navigation.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/robot/gateway/tangying_robot_gateway/spatial_navigation.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/robot/gateway/tangying_robot_gateway` |
| [plan_spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/scripts/plan_spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/mixed-air-ground/source/scripts` |
| [results.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans/results.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/coverage-plans` |
| [captures.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/captures.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [commands.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/commands.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [reports.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/reports.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [request.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/request.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/robot/gateway/tangying_robot_gateway/spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/robot/gateway/tangying_robot_gateway` |
| [spatial_exploration.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/robot/gateway/tangying_robot_gateway/spatial_exploration.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/robot/gateway/tangying_robot_gateway` |
| [spatial_mission.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/robot/gateway/tangying_robot_gateway/spatial_mission.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/robot/gateway/tangying_robot_gateway` |
| [spatial_navigation.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/robot/gateway/tangying_robot_gateway/spatial_navigation.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/robot/gateway/tangying_robot_gateway` |
| [run_spatial_coverage_fixture.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/scripts/run_spatial_coverage_fixture.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/source/scripts` |
| [summary.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal/summary.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/normal` |
| [captures.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/captures.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [commands.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/commands.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [reports.jsonl](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/reports.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [request.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/request.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [spatial_coverage.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/robot/gateway/tangying_robot_gateway/spatial_coverage.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/robot/gateway/tangying_robot_gateway` |
| [spatial_exploration.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/robot/gateway/tangying_robot_gateway/spatial_exploration.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/robot/gateway/tangying_robot_gateway` |
| [spatial_mission.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/robot/gateway/tangying_robot_gateway/spatial_mission.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/robot/gateway/tangying_robot_gateway` |
| [spatial_navigation.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/robot/gateway/tangying_robot_gateway/spatial_navigation.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/robot/gateway/tangying_robot_gateway` |
| [run_spatial_coverage_fixture.py](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/scripts/run_spatial_coverage_fixture.py) | PY | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/source/scripts` |
| [summary.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery/summary.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/fixture/recovery` |
| [master-manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/master-manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade` |
| [raw-evidence.tar.gz](experiments/evidence/2026-10-18-spatial-coverage-upgrade/raw-evidence.tar.gz) | GZ | `experiments/evidence/2026-10-18-spatial-coverage-upgrade` |
| [config.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development/config.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development` |
| [environment.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development/environment.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development` |
| [episodes.csv](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development/episodes.csv) | CSV | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development` |
| [preregistration.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development/preregistration.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development` |
| [summary.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development/summary.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/development` |
| [execution-context.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/execution-context.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study` |
| [config.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout/config.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout` |
| [environment.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout/environment.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout` |
| [episodes.csv](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout/episodes.csv) | CSV | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout` |
| [preregistration.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout/preregistration.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout` |
| [summary.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout/summary.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout` |
| [holdout-frozen-config.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/holdout-frozen-config.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study` |
| [selection-freeze.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/selection-freeze.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study` |
| [selection-protocol.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/study/selection-protocol.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/study` |
| [build.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/build.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [docs-final.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/docs-final.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [docs-final.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/docs-final.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [final-focused.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/final-focused.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [frozen-vs-final-source.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/frozen-vs-final-source.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [generate.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/generate.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [go-build-status.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/go-build-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [go.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/go.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [lint-attempt02.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/lint-attempt02.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [lint.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/lint.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [python-all.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/python-all.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [python-status.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/python-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [readme-regression-attempt02.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/readme-regression-attempt02.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [readme-regression.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/readme-regression.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [reproduction-status.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/reproduction-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [resolved-checks.json](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/resolved-checks.json) | JSON | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [runtime-boundary.log](experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation/runtime-boundary.log) | LOG | `experiments/evidence/2026-10-18-spatial-coverage-upgrade/validation` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [config.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/config.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign` |
| [environment.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/environment.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign` |
| [episodes.csv](experiments/evidence/2026-10-18-spatial-exploration/campaign/episodes.csv) | CSV | `experiments/evidence/2026-10-18-spatial-exploration/campaign` |
| [input-config.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/input-config.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [spatial_exploration.py](experiments/evidence/2026-10-18-spatial-exploration/campaign/source/robot/gateway/tangying_robot_gateway/spatial_exploration.py) | PY | `experiments/evidence/2026-10-18-spatial-exploration/campaign/source/robot/gateway/tangying_robot_gateway` |
| [spatial_mission.py](experiments/evidence/2026-10-18-spatial-exploration/campaign/source/robot/gateway/tangying_robot_gateway/spatial_mission.py) | PY | `experiments/evidence/2026-10-18-spatial-exploration/campaign/source/robot/gateway/tangying_robot_gateway` |
| [spatial_navigation.py](experiments/evidence/2026-10-18-spatial-exploration/campaign/source/robot/gateway/tangying_robot_gateway/spatial_navigation.py) | PY | `experiments/evidence/2026-10-18-spatial-exploration/campaign/source/robot/gateway/tangying_robot_gateway` |
| [spatial_exploration_benchmark.py](experiments/evidence/2026-10-18-spatial-exploration/campaign/source/scripts/spatial_exploration_benchmark.py) | PY | `experiments/evidence/2026-10-18-spatial-exploration/campaign/source/scripts` |
| [summary.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/summary.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [master-manifest.json](experiments/evidence/2026-10-18-spatial-exploration/master-manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-19--no_visibility_gain` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/branching_occlusion--seed-7--no_visibility_gain` |
| [config.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/config.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign` |
| [environment.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/environment.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign` |
| [episodes.csv](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/episodes.csv) | CSV | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign` |
| [input-config.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/input-config.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign` |
| [manifest.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/manifest.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-19--no_visibility_gain` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/recovery_disconnect--seed-7--no_visibility_gain` |
| [spatial_exploration.py](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/source/robot/gateway/tangying_robot_gateway/spatial_exploration.py) | PY | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/source/robot/gateway/tangying_robot_gateway` |
| [spatial_mission.py](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/source/robot/gateway/tangying_robot_gateway/spatial_mission.py) | PY | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/source/robot/gateway/tangying_robot_gateway` |
| [spatial_navigation.py](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/source/robot/gateway/tangying_robot_gateway/spatial_navigation.py) | PY | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/source/robot/gateway/tangying_robot_gateway` |
| [spatial_exploration_benchmark.py](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/source/scripts/spatial_exploration_benchmark.py) | PY | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/source/scripts` |
| [summary.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/summary.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-19--no_visibility_gain` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--baseline` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--nearest_frontier` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_coordination` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_global_reposition` |
| [coordination.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain/coordination.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [coverage-ledger.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain/coverage-ledger.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [evaluator-truth.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain/evaluator-truth.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [events.jsonl](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain/events.jsonl) | JSONL | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [metrics.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain/metrics.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-campaign/vertical_complementarity--seed-7--no_visibility_gain` |
| [paired-exploration.png](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-figure/paired-exploration.png) | PNG | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-figure` |
| [paired-exploration.svg](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-figure/paired-exploration.svg) | SVG | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-figure` |
| [plotted-values.csv](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-figure/plotted-values.csv) | CSV | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-figure` |
| [provenance.json](experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-figure/provenance.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/pre-byte-capacity-figure` |
| [additional-status.json](experiments/evidence/2026-10-18-spatial-exploration/validation/additional-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [benchmark-tests.log](experiments/evidence/2026-10-18-spatial-exploration/validation/benchmark-tests.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [book.log](experiments/evidence/2026-10-18-spatial-exploration/validation/book.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [build.log](experiments/evidence/2026-10-18-spatial-exploration/validation/build.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [campaign-002-execution-context.json](experiments/evidence/2026-10-18-spatial-exploration/validation/campaign-002-execution-context.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [campaign-002-status.json](experiments/evidence/2026-10-18-spatial-exploration/validation/campaign-002-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [campaign-002.log](experiments/evidence/2026-10-18-spatial-exploration/validation/campaign-002.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [campaign-execution-context.json](experiments/evidence/2026-10-18-spatial-exploration/validation/campaign-execution-context.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [campaign-status.json](experiments/evidence/2026-10-18-spatial-exploration/validation/campaign-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [campaign.log](experiments/evidence/2026-10-18-spatial-exploration/validation/campaign.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [final-focused.log](experiments/evidence/2026-10-18-spatial-exploration/validation/final-focused.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [final-lint-attempt02.log](experiments/evidence/2026-10-18-spatial-exploration/validation/final-lint-attempt02.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [final-lint.log](experiments/evidence/2026-10-18-spatial-exploration/validation/final-lint.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [generate.log](experiments/evidence/2026-10-18-spatial-exploration/validation/generate.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [go-build-status.json](experiments/evidence/2026-10-18-spatial-exploration/validation/go-build-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [go.log](experiments/evidence/2026-10-18-spatial-exploration/validation/go.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [lint.log](experiments/evidence/2026-10-18-spatial-exploration/validation/lint.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [python-main.log](experiments/evidence/2026-10-18-spatial-exploration/validation/python-main.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [python-status.json](experiments/evidence/2026-10-18-spatial-exploration/validation/python-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [replay-002.log](experiments/evidence/2026-10-18-spatial-exploration/validation/replay-002.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [replay.log](experiments/evidence/2026-10-18-spatial-exploration/validation/replay.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [runtime-boundary.log](experiments/evidence/2026-10-18-spatial-exploration/validation/runtime-boundary.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [web-status.json](experiments/evidence/2026-10-18-spatial-exploration/validation/web-status.json) | JSON | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [web.log](experiments/evidence/2026-10-18-spatial-exploration/validation/web.log) | LOG | `experiments/evidence/2026-10-18-spatial-exploration/validation` |
| [SHA256SUMS](experiments/evidence/2026-10-20-edge-workspaces/SHA256SUMS) |  | `experiments/evidence/2026-10-20-edge-workspaces` |
| [go-race.jsonl.gz](experiments/evidence/2026-10-20-edge-workspaces/go-race.jsonl.gz) | GZ | `experiments/evidence/2026-10-20-edge-workspaces` |
| [go.jsonl.gz](experiments/evidence/2026-10-20-edge-workspaces/go.jsonl.gz) | GZ | `experiments/evidence/2026-10-20-edge-workspaces` |
| [python.log.gz](experiments/evidence/2026-10-20-edge-workspaces/python.log.gz) | GZ | `experiments/evidence/2026-10-20-edge-workspaces` |
| [source-manifest.json](experiments/evidence/2026-10-20-edge-workspaces/source-manifest.json) | JSON | `experiments/evidence/2026-10-20-edge-workspaces` |
| [summary.json](experiments/evidence/2026-10-20-edge-workspaces/summary.json) | JSON | `experiments/evidence/2026-10-20-edge-workspaces` |
| [web.log.gz](experiments/evidence/2026-10-20-edge-workspaces/web.log.gz) | GZ | `experiments/evidence/2026-10-20-edge-workspaces` |
| [task-replay-desktop.png](frontend/assets/console-ux-20260927/task-replay-desktop.png) | PNG | `frontend/assets/console-ux-20260927` |
| [workspace-desktop.png](frontend/assets/console-ux-20260927/workspace-desktop.png) | PNG | `frontend/assets/console-ux-20260927` |
| [workspace-mobile.png](frontend/assets/console-ux-20260927/workspace-mobile.png) | PNG | `frontend/assets/console-ux-20260927` |
| [workspace-tablet.png](frontend/assets/console-ux-20260927/workspace-tablet.png) | PNG | `frontend/assets/console-ux-20260927` |
| [browser-checks.json](frontend/assets/edge-workspaces-20261020/browser-checks.json) | JSON | `frontend/assets/edge-workspaces-20261020` |
| [desktop-devices.png](frontend/assets/edge-workspaces-20261020/desktop-devices.png) | PNG | `frontend/assets/edge-workspaces-20261020` |
| [desktop-task-history.png](frontend/assets/edge-workspaces-20261020/desktop-task-history.png) | PNG | `frontend/assets/edge-workspaces-20261020` |
| [diagnostics-mobile.png](frontend/assets/mission-console-20261018/diagnostics-mobile.png) | PNG | `frontend/assets/mission-console-20261018` |
| [history-desktop.png](frontend/assets/mission-console-20261018/history-desktop.png) | PNG | `frontend/assets/mission-console-20261018` |
| [responsive-checks.json](frontend/assets/mission-console-20261018/responsive-checks.json) | JSON | `frontend/assets/mission-console-20261018` |
| [workspace-desktop.png](frontend/assets/mission-console-20261018/workspace-desktop.png) | PNG | `frontend/assets/mission-console-20261018` |
| [workspace-mobile.png](frontend/assets/mission-console-20261018/workspace-mobile.png) | PNG | `frontend/assets/mission-console-20261018` |
| [workspace-tablet.png](frontend/assets/mission-console-20261018/workspace-tablet.png) | PNG | `frontend/assets/mission-console-20261018` |
| [captures.json](images/furnished-home/captures.json) | JSON | `images/furnished-home` |
| [completed-cup-transfer.png](images/furnished-home/completed-cup-transfer.png) | PNG | `images/furnished-home` |
| [kitchen.png](images/furnished-home/kitchen.png) | PNG | `images/furnished-home` |
| [panorama.png](images/furnished-home/panorama.png) | PNG | `images/furnished-home` |
| [slam-keyframe-001-depth.png](images/furnished-home/slam-keyframe-001-depth.png) | PNG | `images/furnished-home` |
| [slam-keyframe-001-rgb.jpg](images/furnished-home/slam-keyframe-001-rgb.jpg) | JPG | `images/furnished-home` |
