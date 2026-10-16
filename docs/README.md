# 文档导航：学习、开发、运维与研究

最新探索增量（2026-10-17）：[首次扫描实现](development/2026-10-17-exploration-first-scan.md) → [操作指南](guides/robot-service-workflow.md#首次进入未知场景主动扫描与探索) → [实验与全部尝试](experiments/2026-10-17-exploration-first-scan.md) → [书籍补充](../book/updates/2026-10-17-exploration-first-scan.md)。当前为平面本体主动观察和在线 frontier 增强，不代表三维自主探索或实机认证。

当前文档核对到实现 `821659df`（2026-10-16）；教材入口为[数字版 2.2.0](../book/README.md)。最新云边架构：[分层目标委托](architecture/hierarchical-edge-objectives.md) → [接口与配置](guides/delegated-edge-goals.md) → [代码变化](development/2026-10-15-hierarchical-edge-objectives.md) → [功能测试与证据](experiments/2026-10-15-hierarchical-edge-objectives.md)。Local 与 Worker 现在共用边缘目标核心，区别于历史已规划节点模式。

从你的工作目标选择入口。四条路线负责推荐顺序，[全量分类目录](catalog.md)负责查找所有文档、实验数据表与配图，[文档维护规则](maintenance.md)说明新增资料该放哪里。

| 你要做什么 | 从这里开始 | 最终得到什么 |
| --- | --- | --- |
| **学习系统，建立完整认识** | [学习路线](learning/README.md) | 理解任务、Agent、Runtime、世界模型、云边与证据的关系；按背景选择章节和实践 |
| **开发功能、升级项目、接入机器人** | [开发与升级](development/README.md) | 找到模块、接口、源码、变更流程和相应测试 |
| **安装部署、日常运维、排障与发布** | [部署与运维](operations/README.md) | 按角色准备环境，检查标定/地图/服务，定位异常并核对恢复条件 |
| **设计实验、复现结果、写论文** | [研究与论文](research/README.md) | 从研究问题找到方法、实验、数据及可支持的论断，设计下一轮实验 |

## 新人第一小时

先建立概念，不要求第一小时内完成依赖安装、建图和机器人运动。

1. **5分钟：认清范围。** 阅读[项目介绍](../README.md)与本页“版本与证据”。默认学习场景是一台机器人在 Gazebo 家庭环境完成任务。
2. **15分钟：跟随一个任务。** 阅读[一句话到机器人执行](guides/natural-language-task-lifecycle.md)，区分原句、计划、批准、命令和完成证据。
3. **15分钟：画出系统边界。** 阅读[书籍第2章](../book/chapters/ch02-architecture-evolution.md)和[第3章](../book/chapters/ch03-closed-loop-contract.md)，画出谁规划、谁执行、谁确认结果。
4. **15分钟：理解协作和恢复。** 阅读[长程协作导读](architecture/long-horizon-collaboration.md)，追踪一次 Task → Ops → Recovery → 执行器的因果链。
5. **10分钟：选下一步。** 按[学习路线](learning/README.md)选择章节与检查产物；需要实际运行时从[全新部署](operations/fresh-deployment.md)进入。

## 高频查询

| 问题 | 操作或实现入口 | 对应证据或进一步阅读 |
| --- | --- | --- |
| 边缘 Agent 怎样接 ROS 2 真机并与云端/其他设备通信？ | [边缘测试部署](operations/edge-field-testing.md)、[异构运动契约](development/heterogeneous-mobility.md)、[数据交换](development/edge-data-exchange.md) | [升级与验证记录](development/2026-10-13-edge-field-candidate.md)；软件候选与现场放行分别判断 |
| 一句话如何变成可执行任务？ | [任务生命周期](guides/natural-language-task-lifecycle.md)、[统一能力目标](guides/unified-capability-goals.md) | [模型复杂任务实验](experiments/2026-09-28-complex-model-led-goals.md) |
| 多 Agent 如何通信、保持上下文一致？ | [协作总览](architecture/long-horizon-collaboration.md)、[事件协议](development/2026-10-10-agent-interaction-protocol.md)、[长上下文](development/2026-10-09-long-horizon-context.md) | [长程协议实测](experiments/2026-10-10-long-horizon-protocol.md) |
| 系统如何扩展到云边、异构机器人和多种 SLAM？ | [模块化评价与路线](architecture/modular-extensibility.md)、[SLAM Provider](development/slam-providers.md) | [边界升级与验证记录](development/2026-10-13-modular-extensibility.md) |
| 云端大脑与边缘怎样交接？ | [分层目标合同](architecture/hierarchical-edge-objectives.md)、[API 接入](guides/delegated-edge-goals.md)、[目标运维](operations/delegated-goal-operations.md) | [目标委托测试](experiments/2026-10-15-hierarchical-edge-objectives.md)与[鲁棒性证据](experiments/2026-10-15-single-robot-robustness.md) |
| 点云语义怎样驱动长程导航？ | [点云语义操作](guides/semantic-point-cloud.md)、[目标绑定与执行协议](architecture/semantic-execution-protocol.md) | [五站任务、失败和修复](experiments/2026-10-13-semantic-execution.md) |
| 标定、建图或地图显示有问题？ | [注册服务工作流](guides/robot-service-workflow.md)、[关键帧检查](guides/slam-keyframe-inspection.md)、[地图运维](development/dense-map-operations.md) | [SLAM 与语义导航实测](experiments/2026-09-27-gazebo-slam-semantic-navigation.md) |
| 怎样接入异构机器人？ | [完整接入流程](guides/hardware-agent-integration.md)、[适配器开发](development/robot-adapters.md) | [实机前置判定](operations/production-readiness.md)；协议测试与实体认证分别看 |
| 任务失败、暂停或结果未知怎么办？ | [运维导航](operations/README.md)、[异常处理手册](production/operations-and-failures.md) | [长程验收工具](experiments/long-horizon-harness.md)、[历史失败报告](experiments/2026-10-13-system-evaluation.md) |
| 要查 API、数据结构和工具合同？ | [HTTP/gRPC参考](production/api-reference.md)、[数据契约](production/data-contracts.md)、[Runtime不变量](architecture/protocols.md) | [开发模块地图](development/README.md) |
| 要判断当前能证明什么？ | [研究证据地图](research/README.md)、[实验报告索引](experiments/README.md) | 同时核对提交、场景、模型、地图、验收判据和未测项 |

## 版本与证据：先确认你读的是哪一种资料

| 资料 | 用法 | 不能直接推导的结论 |
| --- | --- | --- |
| 当前操作与接口指南 | 按所在 Git 提交使用；核对设备角色、配置与能力目录 | 指南里出现一个接口，不代表所有适配器都已实现 |
| [书籍2.2.0](../book/README.md) | 系统学习和课程参考；实现复核固定到 `821659df` | 纳入分层目标、共享边缘宿主、点云/异构空间通道和恢复；实验仍按各次原提交与二进制引用，见[学习路线](learning/README.md#书籍与系统增量如何一起读) |
| [软件发布记录](releases/v0.7.0.md)与[Changelog](../CHANGELOG.md) | 区分正式发布和 Unreleased 增量；分支规则见[发布规范](development/branching.md) | 功能分支上的实验通过，不等于已合入 main 或完成发布 |
| 带日期的实施、审计和实验记录 | 回溯当时的假设、失败、修复及环境；按原版本引用 | 历史成功、旧状态页或设计清单不覆盖今天的全部代码 |
| 原始观测与机器可读结果 | 根据 manifest、身份和原始字节复验；数据入口见[研究路线](research/README.md) | 仿真、回放、软件合同和真机证据不能互相替代 |

当前点云长程记录验证的是人工标注区域上的单机器人、多软件 Agent、二维地面巡检；开放词汇全图识别、跨机器人地图配准和真实硬件仍需独立验证。详细范围以[原实验](experiments/2026-10-13-semantic-execution.md)为准。

## 仓库地图：每类东西放在哪

| 位置 | 内容 | 推荐入口 |
| --- | --- | --- |
| `book/` | 教材、章节、习题、课程、工程模板与固定版次 | [书籍](../book/README.md) |
| `docs/learning/`、`docs/research/` | 跨目录的学习与论文阅读路线 | [学习](learning/README.md) · [研究](research/README.md) |
| `docs/guides/`、`docs/install/`、`docs/sim2real/`、`docs/frontend/` | 使用、安装、接入和工作台说明 | [运维导航](operations/README.md) |
| `docs/architecture/`、`docs/development/` | 架构/协议、开发参考、实施和复盘记录 | [开发导航](development/README.md) |
| `docs/operations/`、`docs/production/` | 操作流程、接口手册、配置、验收与历史就绪快照 | [运维导航](operations/README.md) · [手册索引](production/README.md) |
| `docs/experiments/` | 研究报告、工程验收、预登记、失败样本及 JSON/CSV 摘要 | [研究导航](research/README.md) · [全部实验](experiments/README.md) |
| `docs/releases/`、`docs/superpowers/specs/` | 发布身份与原始设计档案 | [分类目录](catalog.md)；设计档案不等于交付证明 |
| `artifacts/` | 地图、图片、日志、基准和证据包，多数不入 Git | 从对应实验报告查路径及摘要；本地文件未必在远程仓库存在 |
| `artifacts/marketing/` | 对外文章、配图和素材出处 | [宣传材料](../artifacts/marketing/README.md)，不作为系统能力或实验结论的依据 |
| 代码目录、`proto/`、`scripts/`、`tests/` | 实现、跨语言契约、运行工具及验证 | [源码地图](development/principles.md)、[开发导航](development/README.md) |

原文件路径与历史记录保持稳定。按主题找资料用四条路线，按文件名找资料用[全量目录](catalog.md)，新增与修订按[维护规则](maintenance.md)执行。

最新 2D/地形/3D 能力见 [空间导航](development/spatial-navigation.md)和[注册测量建图](development/spatial-mapping.md)，三类 ROS 2 任务的范围与失败见 [实验报告](experiments/2026-10-14-spatial-navigation.md)。产物清理、论文原始证据保留见 [运维规则](operations/artifact-retention.md)。
