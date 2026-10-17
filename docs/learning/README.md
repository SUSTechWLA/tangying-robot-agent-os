# 学习路线：从系统全貌到可检查实践

研究异构三维探索时，阅读[教材补充](../../book/updates/2026-10-17-heterogeneous-spatial-exploration.md) → [当前模块与边界](../development/2026-10-17-heterogeneous-spatial-exploration.md) → [配对消融协议](../experiments/2026-10-17-spatial-exploration-protocol.md) → [实际软件结果与图表](../experiments/2026-10-18-spatial-exploration.md)。先区分路径、观察、覆盖、分工和真实控制，再设计可解释的量化实验。

首次进入未知环境时，补读[探索学习补充](../../book/updates/2026-10-17-exploration-first-scan.md)与[当前操作指南](../guides/robot-service-workflow.md#首次进入未知场景主动扫描与探索)：区分初始观察、前沿规划、局部控制、地图发布与全环境覆盖。

当前教材为[数字版 2.2.0](../../book/README.md)，实现复核固定到 `821659df`，已将“云端粗目标、边缘自主细化”纳入正文。先读第 2 章职责，再读第 6–9、17–19 章协作、恢复、云边和长任务；结合[当前架构](../architecture/hierarchical-edge-objectives.md)与[功能测试](../experiments/2026-10-15-hierarchical-edge-objectives.md)阅读。原 2.1.0 及[当时补充章](../../book/updates/2026-10-15-hierarchical-edge-objectives.md)保留历史身份。

[返回文档导航](../README.md) · [开发升级](../development/README.md) · [部署运维](../operations/README.md) · [研究论文](../research/README.md)

面向软件工程师、机器人集成者、研究者和课程读者。先用书籍建立共同概念，再用当前指南、源码与实验记录核对具体实现。只阅读不需要模型密钥、Docker 或机器人。

了解最新模块边界时，补读[云边与异构扩展性评价](../architecture/modular-extensibility.md)：包含职责图、已实现/未实现矩阵、通信与上下文权威，以及后续验收路线。

学习空间能力时，按[2D、地形与 3D 导航](../development/spatial-navigation.md) → [注册点云建图](../development/spatial-mapping.md) → [本轮实验记录](../experiments/2026-10-14-spatial-navigation.md)阅读。它们对应书籍第 4/5/10 章的当前实现，区分点云表面、自由空间、地面支撑、三维定位与真实控制。

## 一、共同主线

| 阶段 | 推荐顺序 | 读完应能解释或提交 |
| --- | --- | --- |
| 1. 一个任务的全貌 | [项目介绍](../../README.md) → [分层目标架构](../architecture/hierarchical-edge-objectives.md) → [真实任务生命周期](../guides/natural-language-task-lifecycle.md) → [第2章：架构](../../book/chapters/ch02-architecture-evolution.md) | 画出用户、计划、批准、执行器、Runtime、机器人和证据的关系 |
| 2. 物理任务何时算完成 | [第1章：物理约束](../../book/chapters/ch01-physical-constraints.md) → [第3章：闭环](../../book/chapters/ch03-closed-loop-contract.md) → [第4章：世界模型](../../book/chapters/ch04-world-model.md) | 区分工具返回成功、任务状态完成和物理后置条件；指出观测时间、来源与坐标的作用 |
| 3. Agent 怎样协作 | [第6章：多Agent](../../book/chapters/ch06-multi-agent-runtime.md) → [第8章：分布式故障](../../book/chapters/ch08-distributed-fault-assumptions.md) → [第17章：云边](../../book/chapters/ch17-cloud-edge-agent-harness.md) | 画出消息、持久账本、任务修订、claim/fence 与完成回执；说明谁有动作权限 |
| 4. 长程任务怎样继续 | [第18章：上下文](../../book/chapters/ch18-long-horizon-context.md) → [第19章：完整案例](../../book/chapters/ch19-long-horizon-walkthrough.md) → [协作实现导读](../architecture/long-horizon-collaboration.md) | 追踪原事件、诊断、恢复计划和复验；说明哪些历史能归档、哪些约束必须保留 |
| 5. 地图怎样变成行动 | [第5章：能力层](../../book/chapters/ch05-tool-layer.md) → [SLAM语义导航](../guides/slam-semantic-navigation.md) → [点云语义操作](../guides/semantic-point-cloud.md) → [语义执行协议](../architecture/semantic-execution-protocol.md) | 区分几何、人工标签、对象识别、可达候选、实际导航与到位验证；解释旧地图为何不能直接续跑 |
| 6. 怎样判断和改进系统 | [第12章：评测](../../book/chapters/ch12-evaluation-training.md) → [最新单机器人实验](../experiments/2026-10-15-single-robot-robustness.md) → [点云任务实验](../experiments/2026-10-13-semantic-execution.md) → [研究路线](../research/README.md) | 写出可支持的结论、未测变量及下一轮对照；保留失败，明确统计单位 |

术语查[附录D](../../book/appendix/D-glossary-and-assumptions.md)，理论与外部参考查[附录E](../../book/appendix/E-references.md)，源码查[附录A](../../book/appendix/A-source-map.md)与[当前开发地图](../development/README.md)。

## 二、按已有背景分流

| 背景或目标 | 优先内容 | 接下来做什么 |
| --- | --- | --- |
| 软件/后端/Agent工程师 | [能力5](../../book/chapters/ch05-tool-layer.md)、[通讯6](../../book/chapters/ch06-multi-agent-runtime.md)、[规划7](../../book/chapters/ch07-orchestration.md)、[一致性8](../../book/chapters/ch08-distributed-fault-assumptions.md)、[上下文18](../../book/chapters/ch18-long-horizon-context.md) | 进入[开发升级](../development/README.md)，完成一个带拒绝路径和恢复验证的增量 |
| 机器人/感知/控制工程师 | [物理1](../../book/chapters/ch01-physical-constraints.md)、[观测4](../../book/chapters/ch04-world-model.md)、[接入10](../../book/chapters/ch10-sim2real.md)、[部署13](../../book/chapters/ch13-install-deploy-ops.md) | 按[异构接入指南](../guides/hardware-agent-integration.md)整理 Profile、驱动、标定、停止和验证证据 |
| 科研与论文作者 | [闭环3](../../book/chapters/ch03-closed-loop-contract.md)、[故障8](../../book/chapters/ch08-distributed-fault-assumptions.md)、[评测12](../../book/chapters/ch12-evaluation-training.md)、[边界15](../../book/chapters/ch15-future-directions.md)、[长任务19](../../book/chapters/ch19-long-horizon-walkthrough.md) | 从[研究问题与证据地图](../research/README.md)选一个可证伪问题，先写预登记 |
| 系统集成与运维 | [单机9](../../book/chapters/ch09-local-single-machine.md)、[部署13](../../book/chapters/ch13-install-deploy-ops.md)、[云边17](../../book/chapters/ch17-cloud-edge-agent-harness.md) | 进入[运维导航](../operations/README.md)，确认部署角色、数据目录、权限和故障处理顺序 |
| 大学课程或系统自学 | [完整书籍](../../book/README.md) → [16周课程](../../book/appendix/G-course-design.md) | 配合[57题指导](../../book/appendix/I-exercise-guide.md)和[十二组实验](../../book/appendix/F-learning-labs.md)，按产物验收学习效果 |

## 三、实践按环境分级

| 级别 | 入口 | 检查产物与边界 |
| --- | --- | --- |
| 纯阅读 | 上述主线、[工程模板](../../book/appendix/H-engineering-playbook.md) | 职责图、一次任务身份表、失败处理表；不声称运行过系统 |
| 软件契约与回放 | [附录F实验1–5](../../book/appendix/F-learning-labs.md) | 运行对应测试，解释输入和拒绝判据；fixture或回放不等于物理运动 |
| 单机器人Gazebo | [全新部署](../operations/fresh-deployment.md) → [Gazebo默认入口](../guides/gazebo-default.md) → [工作台](../guides/user-console.md) | 保留场景、地图、标定、计划、批准和动作后观测；服务启动不等于任务通过 |
| 长程任务与故障 | [长程Harness](../experiments/long-horizon-harness.md) → [点云五站案例](../experiments/2026-10-13-semantic-execution.md) | 区分新运行与离线复验；使用新证据目录，保留完整失败与命令身份 |
| 双机或实体设备 | [双机流程](../operations/robocasa-handoff.md)、[实机接入](../guides/hardware-agent-integration.md) | 独立确认环境和放行条件；单机或协议测试结果不能代替多机/实机验收 |

安装需要的时间取决于依赖、镜像和资源，学习路线不把下载、标定与建图压缩成固定时长。准备执行前以当前操作指南为准。

## 书籍与系统增量如何一起读

书籍数字版 2.2.0 的实现复核固定到 `821659df`，正文与合订本纳入分层目标、共享 Host、目标检查点、有限上下文投影、点云语义和 ROS 2 空间通道。软件版本、书籍版号和实验二进制是不同身份；原实验按原提交/环境引用，正文更新不表示重新运行实验。

| 书中的概念 | 当前操作参考 | 复核范围 |
| --- | --- | --- |
| 第2/7/9/17章的分层目标 | [目标 API](../guides/delegated-edge-goals.md)、[目标运维](../operations/delegated-goal-operations.md)、[功能测试](../experiments/2026-10-15-hierarchical-edge-objectives.md) | 云端批准包络，Local/Worker 共享 Host 细化；旧工作台表单仍是冻结计划路径，模型端点不决定控制权 |
| 第6/8/11/18/19章的目标恢复 | [鲁棒性实现](../development/2026-10-15-single-robot-robustness.md)、[实际报告](../experiments/2026-10-15-single-robot-robustness.md) | 区分确认补认、只读完成重试、报告补传与未知物理效果；软件长步骤链不能替代真实模型/实机暴露量 |
| 第4/5章的世界与能力 | [点云语义指南](../guides/semantic-point-cloud.md)、[执行协议](../architecture/semantic-execution-protocol.md) | 增加版本化三维标注、逐段可达目标、签名绑定、检查点与感知复核 |
| 第8/9/10/17章的设备执行、异构与云边 | [ROS 2 接入](../development/heterogeneous-mobility.md)、[持久通信](../development/edge-data-exchange.md)、[边缘实测](../experiments/2026-10-13-edge-field-candidate.md) | 增加完整 Profile 跨重启准入、Worker 持久通知、ROS 2 动作/观测/停止合同与独立数据交换；区分记录式控制器、MuJoCo 和未做的实体测试 |
| 第6/11/18/19章的协作、恢复和上下文 | [2026-10-13五站实验](../experiments/2026-10-13-semantic-execution.md) | 保留上下文目录重复、定位过时的失败及修复；完成一次只读故障后的整任务恢复 |
| 第4/10章的空间通道 | [注册测量建图](../development/spatial-mapping.md)、[空间导航](../development/spatial-navigation.md)、[最新实验](../experiments/2026-10-14-spatial-navigation.md) | 外部 SE3 配准、三态体素、地形/空中规划、定位/路径回执；未测动力学与实体 |
| 第12章的实验方法 | [系统评价](../experiments/2026-10-13-system-evaluation.md)、[点云历史回放](../experiments/2026-10-13-semantic-point-cloud.md)、[研究导航](../research/README.md) | 三类实验有不同来源和统计单位，不能合并成统一成功率 |
| 第13/14章的运行与开发 | [运维导航](../operations/README.md)、[开发导航](../development/README.md) | 按实际检出提交查最新指南；发布身份与书籍版号分别记录 |

完成主线后，用一个问题检查理解：同一个目标名，在地图版本、任务修订或机器人身份改变后，哪些历史仍能引用，哪些执行依据必须重新取得？答案应能指向契约、源码与实际反例。
