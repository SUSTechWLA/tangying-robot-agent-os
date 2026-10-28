# 附录 C · 案例与实验的来源索引

## C.1 使用实验的规则

本附录选择与教学主线相关的原记录，不重新测量历史数据。软件契约测试验证规定输入/分支；模型测量验证指定决策集；仿真检验指定物理场景；真实设备记录只覆盖相应本体和现场。负责人放行是有范围的决定，不是另一种自动统计证据等级。

引用时写明日期、代码、模型、任务/数据身份、指标分母和限制。原制品无法取得时，只能检查公开报告及代码；不能声称独立重验完整像素或数据库。更细的旧统计、原表与失败记录可经 1.2.0 档案恢复。

## C.2 长程单自然语言任务，2026-10-10

来源：[完整报告](../../docs/experiments/2026-10-10-long-horizon-protocol.md)、[验收摘要](../../docs/experiments/2026-10-10-long-horizon-acceptance.json)、[Harness](../../docs/experiments/long-horizon-harness.md)。教学拆解见第 19 章。

| 身份/指标 | 原记录 |
| --- | --- |
| 任务/版本 | task-d65a1e20a528b66a4dc8e4bf，冻结 revision 1 |
| 环境/长度 | Gazebo 家庭场景；1159.459 秒，7 阶段、23 子步骤、6 抵达 |
| 地图 | 235 帧，227 配准，2 回环，72972 点，23.458 米 |
| 恢复 | 一次派发前只读 UNAVAILABLE；原失败→Ops→Recovery→独立复验→原命令一次重读 |
| 上下文 | 11 managed 记录，7 实际模型请求，4 压缩；冷分页实际 0 次 |
| 事件/制品 | 41 个 v1 事件，本次无身份冲突；1171 个原文件及 SHA 引用 |
| 干预/末尾 | 操作员暂停；原最后导航 ready=false；后采 ready=true 独立保存 |

这是单机器人仿真及真实规划模型调用，当前 Ops/Recovery 主要为确定性角色。已保留前轮失败；单次最终成功不能用于统计整个系统可靠性。分页覆盖来自另行软件测试，不能由“有归档”推出本次模型实际分页。

## C.3 统一能力目标闭环，2026-09-27

来源：[闭环报告](../../docs/experiments/2026-09-27-capability-goal-closure.md)、[验收摘要](../../docs/experiments/2026-09-27-capability-goal-acceptance.json)。Local + Gazebo，实际 GOAL 模型规划；task-77d1b15939d54145bedd36ef，约 11 分钟 34 秒。

主链 calibration.run → mapping.build(survey) → semantic.resolve(厨房) → robot.task；4 个 CAPABILITY_VERIFIED，到达判定 CONFIRMED。原 SLAM 记录为 230 帧、220 配准、2 回环、72382 点、23.492 米。标定、地图 revision 和观测身份以原摘要为准。

本例用于学习能力合同、制品读回与导航后置条件；它没有完成第 19 章的同杯抓放任务，也不能替代云边协议或实体测量。

## C.4 证据门禁与上下文研究，2026-09-21

| 原资料 | 可用于学习 | 解释限制 |
| --- | --- | --- |
| [Grounded verification](../../docs/experiments/2026-09-21-grounded-verification.md) | 回执、证据基线与独立判据的比较设计 | 指定场景和模型条件，不是所有验证器证明 |
| [Agent context evaluation](../../docs/experiments/2026-09-21-agent-context-evaluation.md) | 信息完整性、表示、阶段和统计单位 | 不把 JSON 与缺信息摘要的差异全归因于格式 |

上下文第一轮有 480 个标注案例，留出 test 240；另有 160 条行为轨迹。旧摘要和完整事实表达在该行为沙箱中都最终完成，差别主要在推进轮数等效率指标。不能写成“完整事实提高最终成功率”。第二轮按环节比较表达，不能从某一环节优势推出所有阶段都应采用同一格式。

这些研究不等同于当前 32768 字节 managed view 或实际冷分页的运行测量。第 12、18 章分别讨论评测方法和当前机制。

## C.5 多机、异构与部署证据

| 来源 | 覆盖对象 | 不支持的更强结论 |
| --- | --- | --- |
| [双机交接流程](../../docs/operations/robocasa-handoff.md) | 规定物料/交接区、共享资源与按序双机流程 | 通用异构最优匹配、任意并行任务 |
| [tests/contract/test_heterogeneous_runtime_boundary.py](../../tests/contract/test_heterogeneous_runtime_boundary.py) | 两类 Profile、Python gRPC 与 Go 客户端的实际协议夹具 | 两类实机已合作抓放 |
| [云边验收](../../docs/production/cloud-edge-upgrade-acceptance.md) | 规定协议、存储、容器和故障分支 | 全局事务、生产 HA 或无限离线执行 |
| [现场准备](../../docs/production/field-readiness-2026-09-26.md) | 设备接入与待验证项 | 当前全部实体现场已经放行 |

## C.6 新实验登记模板

记录：问题/假设；代码与书籍版次；设备/场景/模型/种子；配置/数据 SHA；统计单位与分母；独立判据；故障点；完整原输出；介入和排除；结果与不确定性；覆盖范围；原制品及摘要索引。新记录另存，并和旧实验比较，不覆盖旧报告。

## C.7 边缘与空间增量的证据索引

| 记录 | 已验证的范围 | 原始数据与限制 |
| --- | --- | --- |
| [2026-10-13 边缘候选](../../docs/experiments/2026-10-13-edge-field-candidate.md) | 持久合同/通知、三类 ROS 2 协议、单机恢复、双 Worker 交接及数据交换 | fixture、MuJoCo 和软件检查分别记录；实体未验收 |
| [2026-10-14 空间导航](../../docs/experiments/2026-10-14-spatial-navigation.md) | 注册测量采集/包重放、三类各 11 段任务、完整定位与回执、拒绝条件 | 实际 DDS/gRPC，控制器/几何记录式；无动力学与 SLAM 精度 |
| [空间逐次任务](../../docs/experiments/2026-10-14-spatial-navigation.csv) | 全部 11 次尝试、失败与最终 run5 | 不同修复/条件不合为可靠性比例 |
| [空间逐段路径](../../docs/experiments/2026-10-14-spatial-navigation-paths.csv) | 33 段 task/command/plan/map/deployment/到达观测关联 | geometricLengthM 不是实体里程或速度 |

Git 保留 JSON/CSV 与摘要，原始实验目录在本机。缺原图、数据库或二进制时只说明摘要检查范围。[产物清理记录](../../docs/development/2026-10-14-artifact-cleanup.json)逐文件登记临时删除，实验原始数据和失败仍保留。

## C.8 分层目标与单机器人长程，2026-10-15

来源：[架构验收](../../docs/experiments/2026-10-15-hierarchical-edge-objectives.md)、[预定实验方案](../../docs/experiments/2026-10-15-single-robot-robustness-protocol.md)、[完整鲁棒性报告](../../docs/experiments/2026-10-15-single-robot-robustness.md)。软件原始输出与实际 Gazebo 分开保存，master manifest 固定 160 份文件、21,543,481 字节；`.gitattributes` 保留原 CSV CRLF，不修改其字节。

| 证据单位 | 结果与身份 | 可支持的结论与限制 |
| --- | --- | --- |
| final-campaign-04 软件夹具 | 27 项顶层测试/含子测试 58 项通过，23 条任务和 980 条调用/步骤记录 | 规定故障与持久分支；脚本规划/工具，不是实际 LLM 或实体控制 |
| 同一个持久软件机器人五项任务 | 32/48/64/96/128 次确认写，共 368 次；6 次明确拒绝后重规划，0 次重复开始 | 目标跨度、剩余规划和配额保持；不把五项任务当独立机器人样本 |
| 12 项完成收据丢响应任务 | 每项 1 次开始、0 次重复，6 次提交前/6 次提交后丢失 | HTTP 客户端应用边界注入，不是公网/TCP 故障覆盖 |
| Gazebo preparation01 | 4 能力，493.295 秒 Harness，建图回执 23.47155 米 | 建图 Provider 统计；Agent 逐内部建图动作独立 RGB-D 未验收 |
| Gazebo inspection01/02 | 先创建规划超时、0 运动；修复后原句成功，528.093 秒 Harness | 成功含 7 能力、18 子步骤、5 次跨房移动与一次只读故障自动恢复 |
| Gazebo host-inspection01/02 | 先父 Pending 误判、0 物理派发；修复后同一精确目标的新任务成功 | Host02 4 子能力、10 子步骤、250.158 秒 Harness；父/子账本及新鲜 RGB-D 独立复验 |

Gazebo 仅一个移动机器人和 episode 1016；世界/Runtime 未重置，Agent 在任务边界更新，失败任务保留。五条尝试实际涉及四个二进制 SHA，不能混算 3/5 成功率。Host02 的地图身份/定位谓词并非巡检证明的全部，独立核验还检查房间顺序、命令、到位和命令后采集。到位残差相对任务目标，不是 SLAM 绝对精度；巡检实走里程未测。

[Gazebo 证据与冻结重验说明](../../docs/experiments/evidence/2026-10-15-single-robot-robustness/gazebo/README.md)列出原任务、二进制、模型、失败和原大制品位置。缺本地图像/数据库时只能检查分发范围。本版整理已有记录，没有新增仿真运动、抓放、无人机、足式或实机证据。软件 `sourceChangedDuringRun=true` 的旧尝试继续保留但不作最终代码证据；最终来源摘要以 final-campaign-04 原记录为准。

## C.9 本版新增实现的原报告入口

| 原记录 | 用于哪一项教学问题 | 范围 |
| --- | --- | --- |
| [空间覆盖规划](../../docs/experiments/2026-10-18-spatial-coverage-upgrade.md) | 第 4、12、15 章：规划覆盖与已完成覆盖如何分开 | 软件闭环、配对消融；不代表任意全场景已覆盖 |
| [Gazebo 现场闭环](../../docs/experiments/2026-10-18-gazebo-field-validation.md) | 第 10 章：DDS、控制器、物理观测分别证明什么 | 仿真刚体、lidar、停止和避碰；非实机、非无线自治 |
| [机器人工作台](../../docs/development/2026-10-20-edge-robot-workspaces.md) | 第 9 章：独立任务库、切换与离线状态 | 本地软件与浏览器验证，按原记录核对覆盖 |

本版两个 Python 小实验用于课堂解释，不加入上述历史任务分母。教材与出版系统的软件核验另见开发记录 `2026-10-21-book-teaching-redesign.md`；它不改变历史报告的原始结果。

## C.10 本轮边缘可靠性软件实验，2026-10-28

[原报告](../../docs/experiments/2026-10-27-edge-brain-reliability/README.md)采用固定六个测试、每个重复十次，60/60 顶层、110/110 leaf 通过。实际任务记录是 10 个 32 步完成，及 10 个第 25 写未知的目标待核对（各确认 24 步）；两类结局分别保留，重启均 0 次重复写。每轮来源哈希一致，原始 Go JSON 与运行脚本均归档。目录 10-27 是计划日期，真实运行日为 10-28。

这是确定性进程内 Provider、脚本决策器和临时 SQLite 的合同实验，无 Gazebo、ROS 2 运动或外部模型。不能用 60/60 测试断言推出机器人现场成功率 100%，也不能把 10 个 unknown 当成任务成功。模型 usage 的 HTTP fixture 测试只证明采集语义，不证明真实 GPU 缓存收益。受影响包 race 与入口构建见[本轮实现记录](../../docs/development/2026-10-28-edge-brain-reliability.md)。
