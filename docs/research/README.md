# 研究与论文资料导航

本页按“研究问题 → 方法与实现 → 实验与失败 → 数据复核 → 下一轮实验”组织资料，供选题、写论文、设计对照和复现实验使用。核对日期：2026-10-13。各次增量的实验记录单独索引，不重新核定历史结果。

第一次阅读可先看[书籍第 12 章：评测与训练](../../book/chapters/ch12-evaluation-training.md)，再选择下表中的研究问题。查全量实验记录用[实验目录](../experiments/README.md)，查教学案例用[书籍附录 C](../../book/appendix/C-experiment-index.md)，查系统日常使用与升级用[文档总入口](../README.md)。

## 1. 先选研究问题，再读实现

| 研究问题 | 方法与实现入口 | 对应实验与阅读重点 |
| --- | --- | --- |
| 自然语言计划为何可能“执行成功但目标错误”？ | [统一能力目标](../guides/unified-capability-goals.md)、[闭环契约](../../book/chapters/ch03-closed-loop-contract.md) | [10-13 系统评价](../experiments/2026-10-13-system-evaluation.md)：L01 多做一轮抓放；单命令幂等与用户语义正确是不同问题 |
| 多 Agent 如何定位故障，并有限度地继续任务？ | [长程协作架构](../architecture/long-horizon-collaboration.md)、[Agent 事件](../architecture/agent-events.md)、[恢复 Agent](../architecture/recovery-agent.md) | [10-10 长程实验](../experiments/2026-10-10-long-horizon-protocol.md)、[10-13 五站巡检](../experiments/2026-10-13-semantic-execution.md)：核对完整恢复因果链与原命令重试次数 |
| 长上下文如何保持事实、身份与版本一致？ | [书籍第 18 章](../../book/chapters/ch18-long-horizon-context.md)、[上下文实现记录](../development/2026-10-09-long-horizon-context.md) | [上下文表达对照](../experiments/2026-09-21-agent-context-evaluation.md)与最新长程实测分开读；区分信息完整性、表达形式、预算压缩和实际归档回读 |
| 三维点云上的语义如何成为可执行目标？ | [点云使用指南](../guides/semantic-point-cloud.md)、[语义执行协议](../architecture/semantic-execution-protocol.md) | 先读[历史地图服务回放](../experiments/2026-10-13-semantic-point-cloud.md)，再读[五站实跑](../experiments/2026-10-13-semantic-execution.md)：标注持久化、查询候选和实际到达是三个不同证据层次 |
| 物理完成如何避免仅依赖工具自报？ | [监督与验证](../architecture/supervision-verification.md)、[书籍第 11 章](../../book/chapters/ch11-observability-recovery.md) | [接地验证实验](../experiments/2026-09-21-grounded-verification.md)：共享 Gazebo 轨迹的配对验证，注意在线动作与离线策略估计的区别 |
| 建图、语义表达和探索策略各自贡献多少？ | [世界模型](../../book/chapters/ch04-world-model.md)、[语义地图表达实验](../experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md) | [探索主报告](../experiments/2026-09-20-slam-exploration-experiment-report.md)、[被否定的探索假设](../experiments/2026-09-19-exploration-importance-and-coverage.md)：先核对地图完整性和覆盖率分母 |
| 云边协议与异构适配能支持什么协作？ | [分布式 AgentOS](../architecture/distributed-agentos.md)、[多机器人架构](../architecture/multi-robot.md)、[云边验收](../production/cloud-edge-upgrade-acceptance.md) | [双机交接指南](../operations/robocasa-handoff.md)、[异构边界契约测试](../../tests/contract/test_heterogeneous_runtime_boundary.py)：分清协议夹具、具体双机流程与实体异构机群 |

## 2. 证据类型决定论文能说什么

同一报告可能包含多个类型。不要按标题中“真实”“完整”“验收”几个字自动确定证据强度；应读取实际后端、来源、真值、执行次数和判据。这里的真实 Gazebo 指运行 Gazebo/ROS/控制与传感器进程，仍然属于仿真。

| 类型 | 本仓库中的例子 | 可以支撑 | 不能直接支撑 |
| --- | --- | --- | --- |
| 合成契约/单元测试 | 身份错配、陈旧时间戳、CAS 冲突、签名绑定、几何边界测试 | 规定输入与分支符合实现契约 | 机器人成功率、实际识别精度、实体安全性 |
| 合成案例 + 实际模型测量 | 09-21 上下文实验 | 指定案例分布、模型端点与预算下的决策差异 | 当前生产 Agent 准确率，新增 Gazebo/真机完成率 |
| 历史数据回放 | 10-13 点云服务回放；统一评测底座历史导入 | 对保留数据的服务行为、裁决差异和证据一致性 | 新的机器人执行次数；回放不会增加独立样本数 |
| 实际 Gazebo 执行 | 10-13 系统评价、五站巡检 | 对应场景、实现版本与干预下的仿真行为 | 未测场景泛化、真实机器人定位精度或生产可用性 |
| Gazebo 轨迹 + 离线策略回放 | 09-21 接地验证 | 同一物理轨迹上验证器和恢复策略的配对分析 | 九组策略各自独立在线完成了全部任务 |
| 实体机器人实验 | 需提供明确设备、现场、标定与原始记录；[现场准备清单](../production/field-readiness-2026-09-26.md)本身不构成实验 | 仅覆盖实测设备与工况 | 用准备清单、接口或仿真结果代替实体数据 |

### 已有实验精选

| 主题与来源 | 证据类型和核对过的事实 | 可支撑的论断 | 引用时必须同时带出的限制 |
| --- | --- | --- | --- |
| [10-13 边缘 ROS 2 与持久执行](../experiments/2026-10-13-edge-field-candidate.md) | 实际 ROS 2 DDS/gRPC 记录式控制器、MuJoCo 暂停恢复/未知结果与双 Worker 交接、两进程数据交换、真实 Redis | 单 Agent 持久恢复、不同 Profile 接线、数据连续性与失败拒绝边界 | 没有实体设备、实际飞行/步态或公网实验；不同阶段修复后复验不能混成同版本成功率 |
| [10-13 系统评价](../experiments/2026-10-13-system-evaluation.md) | 9 项请求/任务；含批准前条件错误、实际 Gazebo 执行、边界拒绝与软件回归 | Task 自报成功仍需独立语义验收；转发前只读故障可经多角色链有界恢复 | L01 验收失败，修复后只追加短程 P01；不同任务不能混成一个成功率，完整 L01 未重跑 |
| [10-13 点云语义层](../experiments/2026-10-13-semantic-point-cloud.md) | 71,980 点历史 Gazebo 地图；12 个软件判据通过；没有新运动任务 | 标注、查询、版本冲突、过期和篡改可形成可追溯服务契约 | 受控时钟和记录位姿；人工选区，不是开放词汇识别、全图理解或实际导航成功 |
| [10-13 五站语义巡检](../experiments/2026-10-13-semantic-execution.md) | E03 实际 Gazebo 五站完成，任务时间 242.262 秒；一次只读故障自动恢复；N02 版本冲突物理派发 0 | 人工点云区域可通过冻结目标、边缘 grounding、运行守卫与动作后观测接通执行 | 最终成功任务 n=1；P01/E02 失败后版本改变，不能按 1/3 或 100% 报可靠性；`arrival` 不等于 `object` 确认 |
| [10-10 长程任务](../experiments/2026-10-10-long-horizon-protocol.md) | 单机器人 Gazebo，实际 GOAL 模型；暂停、恢复、上下文与原始证据留存 | 规定家庭任务的多阶段执行和身份连续性 | 单次最终成功不证明普适可靠性；与 10-13 的不同任务分开记载 |
| [09-21 上下文表达](../experiments/2026-09-21-agent-context-evaluation.md) | 首轮 480 个标注案例、test 240；另有 160 条行为沙箱轨迹，真实模型响应 | 指定实验中完整事实的价值与格式/成本取舍 | 旧摘要缺信息，增益不能全归因于 JSON；行为组最终完成率均为 100%，不能写成成功率提高 |
| [09-21 接地验证](../experiments/2026-09-21-grounded-verification.md) | 150 个 Gazebo 任务实例、210 条动作轨迹；九组共享轨迹回放 | 对独立真值的验证准确性、假确认与 UNKNOWN 权衡 | 九组不是九套在线运行；任务成功率/恢复成本部分为回放估计；不能宣称 GVF 全面优于 LLM |
| [09-20 语义地图表达](../experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md) | 静态审计、真值栅格对照、自然语言测试与仿真闭环并存 | 地图完整性会改变不同语义表达方案的适用性 | 完整地图上的优势在欠巡检图上反转；分节引用，不把静态审计当现场测量 |

最新五站实验的 `arrival` 仅证明到达观察位并取得新鲜观测，`semanticConfirmed=false`。`object` 还需要注册检测类别、几何关联、来源与新鲜度支持的唯一目标复核；接口和单元测试不构成此次任务对象识别的实测结果。候选路径长度不是实走里程，到位误差不是 SLAM 绝对精度。

## 3. 数据、版本和回溯入口

| 要找的内容 | 入口 | 使用方法 |
| --- | --- | --- |
| 系统评价逐项结果 | [报告](../experiments/2026-10-13-system-evaluation.md) · [协议及修正](../experiments/2026-10-13-system-evaluation-protocol.md) · [JSON](../experiments/2026-10-13-system-evaluation.json) · [CSV](../experiments/2026-10-13-system-evaluation.csv) | 同时保留 R01/N01 条件错误、L01 失败与 P01 新复验，按任务 ID 引用 |
| 点云历史回放 | [报告](../experiments/2026-10-13-semantic-point-cloud.md) · [JSON](../experiments/2026-10-13-semantic-point-cloud.json) · [CSV](../experiments/2026-10-13-semantic-point-cloud.csv) | 区分主回放与最终前端补充审查；软件案例不计入机器人任务分母 |
| 点云到执行 | [报告](../experiments/2026-10-13-semantic-execution.md) · [JSON](../experiments/2026-10-13-semantic-execution.json) · [尝试 CSV](../experiments/2026-10-13-semantic-execution.csv) · [逐站 CSV](../experiments/2026-10-13-semantic-execution-visits.csv) | 分别引用 P01/E02/E03 与 N01/N02；局部规划性能复播另计，不混成系统加速比 |
| 新任务采集与已有任务复验 | [Harness 说明](../experiments/long-horizon-harness.md) · [执行脚本](../../scripts/evaluate_long_horizon.py) · [离线复验脚本](../../scripts/revalidate_long_horizon.py) | 执行脚本会批准仿真运动；离线复验不启动机器人。历史重验优先用证据包内冻结的检验器 |
| 统一研究数据契约 | [评测体系](../architecture/agent-evaluation-system.md) · [Run Schema](../../orchestration/eval/system_eval/run.schema.json) · [指标与能力目录](../../orchestration/eval/system_eval/catalog.json) | 用计划单元全集、版本/来源、测量与未测字段表示 run；历史导入 `fresh_draw=false` |
| 变更为何发生 | [开发记录目录](../development/README.md) · [语义执行计划](../development/2026-10-13-semantic-execution-plan.md) | 用于解释设计选择、缺陷与修复；实际结果仍引用实验报告 |

原始任务包通常包括原句与预算、草案和批准计划、不可变 revision、所有尝试、事件账本、命令回执、动作后观测/图像、模型请求、上下文投影、故障日志、`report.json` 和 SHA-256 `manifest.json`；实际内容取决于任务到达的阶段。创建失败应保留规划 attempt 与 HTTP 原响应，不能因为没有 task ID 就从试验记录中消失。

**源码仓库不是完整数据集。** 三份 10-13 报告的原始图像、数据库、地图与证据归档主要在本地；报告给出了本地路径、摘要及范围。只有 Git 报告时可以审阅结论，不能宣称已独立重验像素或数据库。正式发表前另行托管可共享数据、冻结检验器和源码快照，保留报告中的 SHA；删除凭据和敏感信息应形成明确的数据发布版本。

一次复核按以下顺序进行：

1. 确认报告日期、被测 commit/dirty diff、源码与二进制摘要、模型实际响应身份、地图/标定/语义版本、场景与 episode。
2. 获取报告指向的原始包，验证归档及逐文件摘要；缺文件就记录缺证据，不凭表格重建“原始数据”。
3. 用冻结检验器写入新的输出文件，保留原报告和 manifest；区分文件一致性、执行证据一致性与判据正确性。五站验收没有 HMAC 私钥，明确记录 `signatureIndependentlyVerified=false`。
4. 核对分母、失败/超时/取消/拒绝/人工介入、复位条件及预算。修复后的新任务、新实现和新 episode 必须另记，不能覆盖原失败。
5. 最后生成论文表格。空值与未测量保持原意；工程单测数、模型调用数、子步骤数、独立任务数和独立场景数分别报告。

## 4. 评测与后训练资料的阅读顺序

先读[统一 Eval 体系](../architecture/agent-evaluation-system.md)：目前它提供离线 Run 契约、历史导入、比较与研究门禁；完整执行调度、自动归因、训练导出及发布自动化不能因设计图中存在就视为全部落地。`demo` 能成功产出 FAIL 报告，不等于候选通过门禁。

再读[TrainAgent 评估](../architecture/train-agent-assessment.md)、[后训练流水线](../architecture/post-training-pipeline.md)和[编排后训练](../architecture/orchestration-post-training.md)，理解训练与独立判卷的职责边界。这些文章包含当时实现及账本快照，例如“64 条任务、15 个不同请求、0 条可用正样本”；这些数字不是当前系统的新统计，旧文的“现在”应按原版本解释。开始新训练前，应对当前数据重新导出、检查去重/切分与证据资格，不能直接沿用历史数量或把任务 SUCCEEDED 当训练标签。

论文叙述应分别回答：方法被实现了什么、在什么数据上被测了什么、相对哪个对照改善了什么、哪些结论尚未得到支持。不要用“有模块”“接口可扩展”替代新实验结果。

## 5. 下一轮实验预登记清单

以[系统评价协议](../experiments/2026-10-13-system-evaluation-protocol.md)和[书籍附录 C 模板](../../book/appendix/C-experiment-index.md#c6-新实验登记模板)为起点，在运行前建立新的日期协议。以下是建议的字段，不是已经执行或登记的新实验：

| 登记项 | 运行前需要固定的内容 |
| --- | --- |
| 研究问题与假设 | 目标论断、可证伪条件、主/次指标；探索性结果与确认性结果分开 |
| 实验单位与分母 | 场景、任务族、语言变化、机器人、重复次数及独立簇；样本量与停止规则，不在出现成功后停止 |
| 基线与候选 | 具体变更组件，代码/模型/schema/提示/验证器版本；无法单独控制的改动明确作为整套配置比较 |
| 环境与复位 | 地图、标定、语义版本、对象初态、episode、硬件/仿真配置与背景负载；是否复用历史地图 |
| 任务与预算 | 原句/结构化约束、目标顺序、允许行为、超时、模型上下文/请求预算、重试和人工介入政策 |
| 独立真值与判据 | 目标终态、禁止项、动作后证据、UNKNOWN/拒绝的分类；真值不由待测模型自报 |
| 故障与版本变化 | 注入位置、次数、是否产生副作用、是否丢回执、地图/语义/坐标更新、网络与进程重启的具体时点 |
| 数据与分析 | 原始文件、哈希、模型请求、动作/观测/恢复因果链；配对/随机化、排除理由与不确定性报告 |
| 保存与发表 | 不覆盖的输出路径、冻结验收器、数据托管位置、凭据排除与共享许可 |

建议优先补齐三类证据：固定同一实现后的多场景重复五站实验；明确区分 `arrival` 与 `object` 的对象语义复核对照；上下文压缩、真实归档回读与跨版本冲突的独立故障切片。实体机器人与异构机群另立实验，不与当前单机器人仿真拼接为同一个成功率。小时级/天级持续任务也需要新的预算与暴露量指标，不能从五站任务外推。

新增 [2D/地形/3D 空间实验](../experiments/2026-10-14-spatial-navigation.md)，含全部 11 次自然语言尝试、最终三类各 11 段路径和真实 DDS 采集；不测 SLAM 精度、动力学、实机或长期可靠性。[产物保留规则](../operations/artifact-retention.md)说明论文复核需要的原始数据。
