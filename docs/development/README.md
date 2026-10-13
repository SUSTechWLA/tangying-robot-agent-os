# 开发与升级导航

空间导航增量（2026-10-14）：[统一 2D / 地形 / 3D 接入](spatial-navigation.md) → [注册测量与地图包](spatial-mapping.md) → [实施计划](2026-10-14-spatial-navigation-plan.md) → [实际实验](../experiments/2026-10-14-spatial-navigation.md)。涉及无人机高度、足式支撑、外部 SLAM 接入或跨坐标版本时优先核对这些合同。

核对日期：2026-10-13。这里按开发者的工作顺序组织资料：**了解架构 → 定位模块 → 设计变更 → 测试验收 → 发布交付**。日常值守另看[运维入口](../operations/README.md)，实验复现和论文取证看[实验索引](../experiments/README.md)，全部资料的分类见[文档总导航](../README.md)。

边缘设备开发优先看[实机测试部署](../operations/edge-field-testing.md)、[ROS 2 异构运动契约](heterogeneous-mobility.md)、[跨 Agent 数据协议](edge-data-exchange.md)和[Worker 可靠执行](edge-worker-reliability.md)。本轮实现、仿真与未测项汇总于[边缘升级记录](2026-10-13-edge-field-candidate.md)。

正式软件发布、当前工作分支和书籍是三种身份。软件发布见 [v0.7.0](../releases/v0.7.0.md)；当前检出的增量看 Git 提交与[变更日志](../../CHANGELOG.md)；[数字版 2.0.0 教材](../../book/README.md)的案例源码固定到 `11db0adb`，适合建立原理，不自动包含后续点云语义执行升级。记录自己的分支和提交后再比较文档中的行为。

## 1. 先建立系统全貌

| 目的 | 阅读顺序 | 阅读后的产出 |
| --- | --- | --- |
| 第一次参与开发 | [当前开发上手](getting-started.md) → [开发原则与源码地图](principles.md) → [一条真实任务的完整链路](../guides/natural-language-task-lifecycle.md) | 能将自然语言、批准计划、机器人命令和动作后证据对应起来 |
| 理解软件架构和权威边界 | [生产架构](../production/architecture.md) → [为什么分布式](../architecture/why-distributed.md) → [生命周期对象](../architecture/lifecycle-objects.md) | 能区分 Task、Runtime journal、世界观测和 UI 投影分别由谁维护 |
| 修改长任务、多 Agent 或云边协议 | [长程协作实现导读](../architecture/long-horizon-collaboration.md) → [多 Agent 运行时](../architecture/multi-agent-runtime.md) → [上下文实现记录](2026-10-09-long-horizon-context.md) | 能画出执行、诊断、恢复、持久记录和消息之间的因果链 |
| 接入机器人或传感器 | [硬件接入全流程](../guides/hardware-agent-integration.md) → [适配器开发](robot-adapters.md) → [标定契约](robot-calibration.md) | 能列出新设备实际支持的能力、观测来源、停止行为与现场验收缺口 |

上表的原理和接口说明各有核对日期。涉及最新语义能力时继续读本页第 3 节；涉及精确字段时核对源码与测试，历史图示不能赋予新接口或执行权限。

## 2. 按问题定位模块与测试

这是一张精选入口表，不代替完整源码地图。先找到发生变化的权威层，再沿相邻边界检查影响。

| 要修改的问题 | 实现入口 | 接口或实现说明 | 首选回归入口 |
| --- | --- | --- | --- |
| 自然语言如何选择能力、约束提案 | [能力规划器](../../internal/capabilityagent/planner.go)、[提案 Schema](../../internal/capabilityagent/proposal_contract.go) | [统一能力目标](../guides/unified-capability-goals.md) | [规划测试](../../internal/capabilityagent/planner_test.go)、[提案契约](../../internal/capabilityagent/proposal_contract_test.go) |
| 任务版本、审批、更新与事件 | [任务服务](../../tasks/service.go)、[任务上下文](../../tasks/context.go) | [API](../production/api-reference.md)、[数据契约](../production/data-contracts.md) | [版本更新](../../tasks/service_revision_test.go)、[决策上下文](../../tasks/decision_context_test.go) |
| 长历史压缩、冷归档、模型上下文预算 | [预算投影](../../core/agentcontext/managed.go)、[任务阶段记忆](../../tasks/context_memory.go) | [上下文实现](2026-10-09-long-horizon-context.md)、[预算故障与修复](2026-10-10-context-budget-recovery.md) | [上下文验收](../../core/agentcontext/managed_acceptance_test.go)、[规划上下文](../../internal/capabilityagent/planning_context_test.go) |
| 多 Agent 通讯、诊断与受控恢复 | [运行时协调](../../agentruntime/orchestrator.go)、[恢复 Agent](../../agentruntime/recoveryagent.go)、[只读续跑](../../internal/capabilityagent/read_recovery.go) | [Agent 事件](../architecture/agent-events.md)、[交互协议](2026-10-10-agent-interaction-protocol.md) | [协调测试](../../agentruntime/orchestrator_test.go)、[恢复绑定守卫](../../internal/capabilityagent/read_recovery_guards_test.go) |
| Local 执行、grounding 与完成判据 | [Runner](../../edge/agent/runner.go)、[机器人客户端](../../edge/robotclient/client.go) | [执行原则](principles.md)、[观察证据](observation-evidence.md) | [执行闭环](../../edge/agent/closure_test.go)、[目标落地](../../edge/robotclient/grounding_test.go) |
| 云边交接、重连和重复完成回执 | [云端完成协调](../../fleet/coordinator/completion.go)、[边缘上下文](../../edge/worker/context.go) | [执行上下文契约](../../core/contextcontract/README.md)、[云边完成协议](2026-10-10-cloud-edge-protocol.md) | [云端完成](../../fleet/coordinator/completion_test.go)、[边缘回执重放](../../edge/worker/completion_replay_test.go) |
| Runtime、安全、持久命令与新机器人 | [服务入口](../../robot/gateway/tangying_robot_gateway/service.py)、[Safety](../../robot/gateway/tangying_robot_gateway/safety.py)、[journal](../../robot/gateway/tangying_robot_gateway/journal.py) | [适配器开发](robot-adapters.md)、[机器人工具层](robot-tool-layer.md) | [Gateway 服务](../../robot/gateway/tests/test_service.py)、[异构跨语言契约](../../tests/contract/test_heterogeneous_runtime_boundary.py) |
| SLAM、地图启用、定位与导航 | [机器人工作流](../../robot/gateway/tangying_robot_gateway/robot_workflow.py)、[ROS 导航包](../../robot/ros2_ws/src/tangying_navigation/) | [RTAB-Map / Nav2](rtabmap-navigation.md)、[SLAM 语义导航](../guides/slam-semantic-navigation.md) | [Gazebo 工作流](../../robot/gateway/tests/test_gazebo_workflow.py)、[导航包测试](../../robot/ros2_ws/src/tangying_navigation/test/) |
| 三维点云标注、候选和执行绑定 | [语义服务](../../robot/gateway/tangying_robot_gateway/semantic_cloud_services.py)、[语义路线](../../internal/capabilityagent/semantic_route.go)、[持久 grounding](../../edge/agent/semantic_grounding.go) | [操作指南](../guides/semantic-point-cloud.md)、[执行协议](../architecture/semantic-execution-protocol.md) | [运行时绑定](../../robot/gateway/tests/test_semantic_execution_binding.py)、[恢复检查点](../../edge/agent/semantic_grounding_test.go)、[独立验收器](../../tests/install/test_semantic_execution_evidence.py) |
| 控制台呈现、接口和回放 | [Local API](../../console/server.go)、[浏览器 API](../../web/app.js)、[交互入口](../../web/console_ui.js) | [用户指南](../guides/user-console.md)、[前端开发与诊断](../frontend/console-v1.md) | `make test-web`；布局和交互变更补浏览器验证 |

扩展云边架构或建图算法前，先读[模块化与扩展性评价](../architecture/modular-extensibility.md)与[SLAM Provider 接入](slam-providers.md)。本轮已拆出的共享值在 `core/fleetcontract` / `core/eventstore`，共享执行校验在 `orchestration/handoff`，Redis 取任务实现在 `edge/tasksource`。

## 3. 当前点云语义升级从哪里读

推荐按以下顺序，将“会查询地图”和“完成物理任务”分开学习：

1. [点云语义操作指南](../guides/semantic-point-cloud.md)：地图身份、三维标注、版本/CAS、查询、候选与自然语言路线的操作入口。
2. [语义执行协议](../architecture/semantic-execution-protocol.md)：目标 ID 和版本如何冻结，逐站候选如何绑定，Runtime 如何核验，以及到位和对象确认的区别。
3. [点云语义层实施记录](2026-10-13-semantic-cloud-plan.md)与[执行升级实施记录](2026-10-13-semantic-execution-plan.md)：为什么选择当前方案、如何分阶段接入、遇到哪些问题。
4. [地图回放实验](../experiments/2026-10-13-semantic-point-cloud.md)与[五站 Gazebo 执行实验](../experiments/2026-10-13-semantic-execution.md)：前者检验保存点云的语义层，后者记录实际导航、故障恢复、失败修复与版本冲突负例。

当前执行路径为：自然语言 → 已查询的语义目标 → 冻结任务版本 → 每站新鲜定位与可达候选 → 原有 Runtime/Nav2 → 动作后的到位或对象证据。三维语义目标仍使用地面二维导航；`arrival` 不等于识别出对象。开放词汇全图识别、自动多视角实例融合、标签随闭环优化自动迁移、跨机器人地图对齐和真机长程完成不在这轮已验证范围内。

## 4. 设计变更时怎样使用历史资料

文件名含日期、`plan`、`spec` 或 `ADR` 不能单独决定其状态：有的计划已有完成记录，有的“当前差距”只描述当时的基线。按下面四类使用，而不是把整个 `development/` 当现行手册。

| 类别 | 代表入口 | 使用方式 |
| --- | --- | --- |
| 当前接口与操作说明 | [适配器开发](robot-adapters.md)、[工具层](robot-tool-layer.md)、[观察证据](observation-evidence.md)、[语义执行协议](../architecture/semantic-execution-protocol.md) | 实施前核对字段和对应测试；行为改变时同步更新 |
| 已实现的变更与修复记录 | [长上下文](2026-10-09-long-horizon-context.md)、[云边完成回执](2026-10-10-cloud-edge-protocol.md)、[点云执行升级](2026-10-13-semantic-execution-plan.md) | 了解动机、故障和回归门禁；实测结论继续追到实验报告和源码身份 |
| 历史审计与目标设计 | [早期统一能力 ADR](../superpowers/specs/2026-09-27-unified-capability-agent-architecture-adr.md)、[当时能力审计](2026-09-27-agent-capability-architecture-review.md)、[稠密地图查看器方案](dense-map-viewer-plan.md) | 用于理解取舍；其中“尚未实现”“当前缺口”只属于原基线，不能覆盖后续指南 |
| 仍需独立验证的扩展方向 | [Real2Sim 方案](real2sim-from-robot-slam.md)、[标定与 ROS 精度方案](calibration-slam-ros-plan.md)、[语义协议的未实现范围](../architecture/semantic-execution-protocol.md#8-异构接入与未实现范围) | 提取研究假设和验收任务；推荐门槛与候选依赖不等于已经接线、测试或现场认证 |

升级前写清六件事：变更针对什么问题；当前基线是什么；谁维护权威状态；哪些身份、权限和完成判据必须保留；如何兼容已有记录；什么实验会证明改进或否定方案。新增 Agent 先读[扩展 Agent](adding-an-agent.md)，新增机器人先走适配器契约，不在通用规划器中添加厂商分支。

历史计划和实验保留原文、时间、失败与证据。新结果另写更新记录并连接旧文；不要删掉失败尝试，也不要修改旧摘要让它匹配新算法。

## 5. 测试、验收和发布

验证应从改动边界开始，再扩展到其影响的执行链。命令在仓库根目录运行；完整环境要求见[开发上手](getting-started.md)。

| 阶段 | 工作与入口 | 应保留的结果 |
| --- | --- | --- |
| 局部回归 | 修改包的 Go/Python 测试；涉及并发和恢复时补相应 `go test -race` | 实际命令、通过/失败/跳过、日志位置 |
| 边界校验 | 跨语言/异构合同；协议变更执行 `make generate-check`；依赖方向见 [tests/architecture](../../tests/architecture/) | 版本、字段和生成代码一致性，错误身份/旧版本的拒绝结果 |
| 实际任务验收 | [Gazebo 指南](../guides/gazebo-default.md)、[长程验收器](../experiments/long-horizon-harness.md)、[系统 Eval](../architecture/agent-evaluation-system.md) | 原始任务、环境、模型配置、批准计划、命令 journal、观测与失败过程；输出使用新目录 |
| 文档核对 | `go test ./tests/docs`；`.venv/bin/python -m pytest -q tests/docs`；改出版源时另跑 `make book-check` | 链接和命令合同结果；更新了说明不等于重跑机器人实验 |
| 发布候选 | [分支规范](branching.md) → [测试与验收](../production/testing-and-acceptance.md) → [发布清单](../operations/release-checklist.md) | 当前提交的完整 CI、变更日志、兼容/回滚说明、未覆盖项和发布身份 |

`make test`、`make lint`、构建和协议生成是发布候选的完整检查入口。部分测试依赖 ROS、Docker、独立 RoboCasa 环境或设备；未运行与跳过必须分别记录。单元测试、地图回放、实际 Gazebo、双机器人交接和实体现场的结论不能互相替代。

提交前确认代码、当前指南、配置/API 和测试一致；运行 `git diff --check`，排除密钥、会话凭据和运行数据。论文需要的数据结构与证据保留规则从[实验索引](../experiments/README.md)继续；部署、升级执行与回滚从[运维入口](../operations/README.md)继续。

本轮维护与出版见 [实施计划](2026-10-14-maintenance-and-book-plan.md)，清理清单见 [产物记录](2026-10-14-artifact-cleanup.json)。当前参考文档已补齐空间通道和持久合同，与历史实施报告分别使用。
