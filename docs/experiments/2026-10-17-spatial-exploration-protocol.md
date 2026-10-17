# 异构三维探索：配对消融、覆盖指标与回放协议

编写起始日期：2026-10-17；正式矩阵前接口冻结口径更新：2026-10-18。类型：实验协议。本文定义本轮离线研究基准及后续接入仿真/实机的评价方式；协议中的建议实验不等于已经执行。实现职责与限制见[设计记录](../development/2026-10-17-heterogeneous-spatial-exploration.md)。

实际过程与结果另见[2026-10-18 软件报告](2026-10-18-spatial-exploration.md)、[逐 episode 数据](2026-10-18-spatial-exploration.csv)、[配对图与来源](assets/2026-10-18-spatial-exploration/provenance.json)及[完整证据清单](evidence/2026-10-18-spatial-exploration/master-manifest.json)。`001` 保留为最后日志字节准入修复前记录，最终 `002` 复测状态按实际报告判断；重复软件运行不增加独立环境或配对任务块分母。

## 1. 研究问题与可支持的结论

研究问题：共同三维世界表示下，收益选点、全局重定位、遮挡估计和异构目标协调分别对团队观测覆盖、重复探索、路程、规划开销和恢复行为产生什么影响？

当前基准是合成体素环境中的闭环软件实验：隐藏真值产生离散射线观测，规划器只使用已观测地图，每轮重新选点并更新地图。几何移动不包含时间参数化、Gazebo 物理、真实定位误差、飞控与足式步态。可以评价本次策略和协议，不可称为 OmniPlanner 复现、SLAM 精度试验、实际飞行、真实云边网络可靠性或任意三维全场景覆盖。

## 2. 冻结条件与数据分离

每次运行先创建全新输出目录，拒绝覆盖已有证据。冻结以下条件：

- Git HEAD、dirty diff、实际导入的核心/协调/基准源文件 SHA-256、Python 版本、执行命令、配置摘要与依赖。
- 场景生成规则、分辨率、ROI、体素展开顺序、seed、机器人起点、本体包络、移动约束、传感器 FOV 与量程。
- 初始观测生成规则、每轮/每机规划预算、总轮数/路程限制、策略配置、机器人顺序、并列目标决策规则。
- 故障类型、发生轮次、持续区间和恢复条件；所有变体使用同一故障时序。
- 唯一实验 ID、episode ID、策略标签、开始/结束时间与实际结果状态。

同一 `scene + seed` 为配对单位，各变体必须使用相同真值、初始位姿、传感器和故障时序。不同机器人可以有不同传感器和移动约束，但这些差异必须在所有变体间保持。用真值生成观测和事后评分的环境/evaluator，不得向规划器传入真值障碍、不可见目标、预定完整路径或覆盖分母目标 ID。

当前共同坐标来自实验声明，不评价地图配准精度。若后续加入跨地图配准，需冻结变换真值、误差模型、配准算法和允许阈值，并记录配准拒绝，而不是仅让所有机器人的 `frameId` 写成相同字符串。

地面支撑也是场景假设：只有观测到地板体素后，软件环境才提供对应平整承载面的支撑信息，不从未知列推断支撑；这不是从真实传感器估计承载力、摩擦或足式接触。理想无噪声位姿、固定传感器与静态环境共同限制结论。

## 3. 场景与变体矩阵

| 场景 | 对照目的 | 需单列的局限 |
| --- | --- | --- |
| `vertical_complementarity` | 地面与空中对高处区域的互补观测 | 简化体素与单层地面，不代表完整建筑多层通行 |
| `branching_occlusion` | 分支、墙体遮挡、局部收益与远端重定位 | 离散射线可能漏过窄目标；分辨率影响结果 |
| `recovery_disconnect` | 观测交换分区、重连补传、驱动拒绝与未知执行后的恢复 | 只切断 observation exchange；本地 scheduler 仍可用，不评价完整云断联自治 |

| 变体 | 相对基线改变 | 其余条件 |
| --- | --- | --- |
| `baseline` | 收益选点、全局重定位、可见收益与团队协调均启用 | 冻结完整配置 |
| `nearest_frontier` | 以最近合法目标为主要策略 | 保留碰撞、支撑、射线观测和预算 |
| `no_global_reposition` | 只允许安全直连，关闭绕已知障碍的 A* 重定位 | 保留同一本体可达性与安全约束；不是独立全局拓扑规划消融 |
| `no_visibility_gain` | 用简化收益代理代替遮挡收益评分 | 实际传感器观测仍有遮挡 |
| `no_coordination` | 独立选点，关闭团队收益/目标协调 | 每台机器人、总预算和故障相同 |

上述名称对应基准配置标签；精确参数从每次运行的冻结配置读取，不能只靠标签推断。单因素消融说明该机制在给定系统中的贡献，不自动证明所有组合或其他算法下都有同样效果。后续可扩展全因子实验，但需单列运行成本和多重比较。

每轮先对两台机器人分别提案并分配，再串行执行，令目标重叠协调有可观测的作用机会，同时保持一个执行中的 fence。`no_coordination` 关闭目标重复/重叠选择与团队已观测收益抑制，不关闭地图观测共享或执行安全。它不是“关闭一切多机器人通信”的对照。

这是 `assignment_overlap + shared_coverage_guidance` 的联合消融，不能仅据此分别归因两个子机制。后续要区分子机制时，新增独立配置与成组运行，保留本轮配置及解释。成功目标会退役，避免将重复原地选点的低增益成功当成长程进展。

## 4. 覆盖的分母与独立评分

首先冻结任务边界 ROI 和传感器模型。当前指标是 **ROI 体素观测覆盖**，不是网格导航成功率、表面重建精度或体积真实精度。当前三类场景均为 `shape=[11,9,7]`、分辨率 1 m，分母固定为外壳以内的 315 个体素，包含内部 free 和 occupied；外壳不计入分母。实际分母必须以每个 episode 的 `evaluator-truth.json.roiVoxelIds` 为准，不能用“已知地图中的已知比例”冒充全场景覆盖。分母并未剔除遮挡或实体内部不可见体素，未达到 100% 应如实报告。

令 `T` 为事前冻结的目标体素集合，`O_r(t)` 为机器人 r 截至 t 经传感器实际观测并登记的体素集合：

```text
coverage(t) = |T ∩ union_r O_r(t)| / |T|
```

分母为零时记为未定义，不能报告 100%。遮挡、不可达或当前有限采样没有找到的区域仍需按预登记分母报告；若另算“团队可观测区域覆盖”，必须保存独立可观测性定义和计算方法，不能事后剔除失败区域抬高覆盖率。

事件流以 `round=-1` 标识初始观测，可据此区分任务新增观测；当前汇总的覆盖和重复观测包含初始观测。机器人提案中的 `visibleUnknownVoxelIds` 是收益预测，不进入已完成覆盖；只有环境/传感器提供的实际观测事件进入评分。当前不证明任意连续位姿下全部可观测空间已被穷尽。

## 5. 指标表和缺测规则

| 指标 | 定义/报告要求 | 本轮解释范围 |
| --- | --- | --- |
| 终末覆盖率、覆盖曲线 | `coverageFraction=coverageObserved/coverageDenominator`；曲线可由逐事件观测独立导出 | 当前汇总保存终末值，原始事件保留轮次 |
| 新增观测与重复观测 | 各机实际观测集合与团队既有集合的交并；保存原始计数和分母 | 不能用候选预期收益代替 |
| 总路径长度、各机路径长度 | 已执行几何折线各段欧氏长度之和；初始位姿/零步单列 | 软件位移，无轮滑、飞行漂移 |
| 规划耗时 | `planningMsP50/P95/Max/Total` 与 `planningCalls`；计时包含 explorer 构造与 propose | 受硬件/负载影响，不是确定性输出 |
| 派发准入耗时 | `admissionMsP50/P95`，包括当前已知地图检查与独立真值/静止同伴包络检查 | 离线准入成本，不是生产 Runtime 或无线延迟 |
| 通信消息数与字节 | 按实际序列化消息计数，含重发时说明是否纳入 | 不代表 ROS/DDS/TLS 实际链路开销 |
| 故障与恢复 | 触发、检测、停止确认、回收、重分配、首次恢复的事件与时差 | 离线注入与确认 |
| 完成/部分/失败 | 覆盖判据、预算、候选耗尽与异常分别记录 | `no_candidate` 不等于全覆盖 |
| 能耗、实际控制误差、定位误差、实体碰撞 | 未采集则为 `null` / `not_measured` | 不以路程代替实测能耗或零碰撞 |

当前 `repeatedVoxelObservations` 是每条观测中与此前团队联合集合重合的 ROI 体素数，包含同机重复与跨机重叠；`repeatObservationFraction` 以 `roiVoxelObservations` 为分母。`perRobotObservedVoxels` 为各机去重后的体素数，不能从终态计数精确拆出所有时间重复类别。正式论文如需分别分析，使用逐机原始观测另外生成两个指标并登记导出器。

`pathLengthM` 仅累计 `outcome=succeeded` 的几何路径，未验证未知动作实际走了多少，因此不能标为总物理行程。`messageBytes` 按 `communication` 事件的紧凑排序键 JSON、UTF-8 编码包长计数，包含观测上传、向其他在线机器人转发及重连 snapshot，每次传输分别计数；不含全部分配/ROS/DDS/TLS 或无线开销。`reconnectSnapshots` 记录显式重连同步，不允许未记字节的免费全图同步。

`stopAcknowledgements` 是显式 fixture 停止回执次数；`recoveredUncertainLegs` 要求该机器人之后另一次 leg 成功才增加，不把收到停止 ACK 本身计作恢复任务成功。`independentCollisionRefusals` 是静态真值和静止同伴包络拒绝次数，`physicalCollisionCount=null`。不能将其解释为实体零碰撞或真实速度归零。

故障必须分为 `plannedFaults/appliedFaults/skippedFaults`，由 `fault_planned/fault_applied/fault_skipped` 逐项记录 `faultId`、类型、任务与原因；`faultEvents` 只统计实际应用的故障。未到触发轮或没有合法执行任务时保留 skipped，不能仅凭配置声明故障恢复。`faultPlan/faultApplications/faultSkips` 支持逐项审计，`failureReasons` 保留真实拒绝分类。`successfulGainLegs/successfulNoGainLegs` 按成功后新增 ROI 观测区分，执行成功次数不等于探索进展次数。

后续动力学/硬件阶段增加真实时间、碰撞/近失事件、路径跟踪误差、急停响应、定位漂移、传感器拒绝率、续航和能耗。每个指标注明来源、单位、采样率和校准，未测的保持缺失。

## 6. 执行、回放与来源

仓库根目录使用独立软件研究入口：

```bash
.venv/bin/python scripts/spatial_exploration_benchmark.py \
  --config examples/experiments/spatial_exploration_ablation.json \
  --output artifacts/experiments/spatial-exploration-MY-RUN

.venv/bin/python scripts/spatial_exploration_benchmark.py \
  --replay artifacts/experiments/spatial-exploration-MY-RUN

.venv/bin/python scripts/plot_spatial_exploration.py \
  --input artifacts/experiments/spatial-exploration-MY-RUN \
  --output artifacts/experiments/spatial-exploration-MY-FIGURE
```

绘图输出使用独立新目录，生成 PNG、SVG、逐点 CSV 与 `provenance.json`；复画归档 `002` 时，将 `--input` 改为 `docs/experiments/evidence/2026-10-18-spatial-exploration/campaign`，保留新的输出目录。脚本核对已 finalized 的 summary/CSV 与 manifest，并固定输入和生成器摘要；绘图不增加实验样本。绘图依赖已在 `pyproject.toml` 的 `context-research` extra 固定为 `matplotlib==3.10.8`，`make setup` 包含该依赖。

执行前核对脚本 `--help` 和冻结配置；本协议中的 `MY-RUN` 是新目录占位符，不是已有实验结果。默认配置为 3 场景条件 × 2 种子 `[7,19]` × 5 变体，共 30 个配对 episode，每个最多 14 轮，覆盖阈值 1.0。配置的 `max_candidates=32/max_rays=49/max_ray_steps=8192/max_plan_calls=6` 控制规划开销；实际模拟传感器固定使用最多 81 射线和 8192 步。规划评估与观测模型预算不同须保留，不能隐去。

`seed=7/19` 使每种场景的两次占据布局不同；不使用产生同布局的两个 seed 冒充独立场景。`branching_occlusion` 与 `recovery_disconnect` 共用占据结构，只改变故障条件，因此默认是 6 个配对任务块，不是 6 个独立几何环境。`occupancyGeometryHash` 排除 seed/mapId，仅固定 cells 与网格几何；`uniqueGeometryN/uniqueGeometryNByScene` 以实际摘要去重，配对变体布局不一致则拒绝比较。

软件基准不调用机器人运动端口。`--replay` 从保存的配置、实际观测/执行/通信/故障事件重新计算指标并核对事件链与文件摘要；它不重新执行策略寻路或生成传感器观测，也不把耗时字段解释为新测量。

campaign 根目录保留 `input-config.json/config.json/environment.json/manifest.json/summary.json/episodes.csv` 和 `source/` 下四个实际实现文件。每个 episode 保留 `events.jsonl/coverage-ledger.jsonl/coordination.jsonl/evaluator-truth.json/metrics.json`；初态、策略、故障与每条路径/观测均在事件中。基准 `spatial.event.v1` 使用 `index/previousHash/hash`；核心账本 `exploration.event.v1` 使用 `sequence/previousHash/hash`，两者作用不同，不混称同一线协议。重复通知不能重复计入账本。篡改、缺失、重复、乱序、错误世界/网格依据应产生明确拒绝。摘要只能验证所保存字节的一致性，不能证明数据来自实际硬件。

`manifest.sourceHashes` 固定四个实现文件，`source/` 保存对应字节，`gitHead/gitDiffHash` 辅助定位源码，`configHash` 是规范 JSON 的 SHA-256。`environment.json` 记录 Python/platform 和相关依赖版本；原始输入配置与规范化配置同时保存。新建未跟踪文件不一定出现在 `git diff HEAD` 中，因此必须同时保留源码 bytes/hash；单有 Git HEAD 不足以重建本轮实现。恢复时在指定 Git 基线上覆盖冻结源码，并核对环境，不以今日工作树替代原实现。manifest 最终索引产物字节与摘要，运行中源码变化标为 `invalid_source_mutation` 并拒绝纳入比较；软件异常保留未 finalized 的目录，不覆盖为新成功样本。

回放比较覆盖、路程、消息、执行/故障计数和从已记录 `planningMs` 汇总的耗时；它没有测得新的墙钟规划耗时。还核对真值内容 hash、观测分类与真值一致、占据几何身份和事件链末端，逐文件检查已登记产物及冻结源码的 bytes/SHA，并重算 summary 的配对差与 Pareto 标签。它不重新执行策略、传感器射线、物理动作或所有合同测试。`summary.pairedDeltas` 为同 `sceneId+seed` 的“变体减 baseline”，不是统计显著性或置信区间。改变硬件或负载时不混为同一性能条件。

## 7. 统计、最优设计与论文报告

本轮小规模 smoke campaign 用于验证接口和实验基础设施，不能作为普适性能证明。正式研究应事先固定多个训练/开发场景与独立留出场景，按场景复杂度分层，使用足够的配对种子。先报告每次尝试，再报告均值/中位数、离散程度和按配对单位计算的置信区间。

主指标和权重在观察结果前登记；预算截断、超时和未完成 episode 保留在分母中。不得只统计成功任务的耗时。若比较达到覆盖阈值的时间，对未达到者标记删失，并报告阈值达成比例；不能填入最终预算时间冒充已完成。

不同配置用 Pareto 前沿比较覆盖、资源开销和风险，再依应用目标选择。调参过程、候选范围和失败配置保留；不要利用测试集选权重后再将同一测试集作为独立最优证明。单场景、单种子或软件自报成功不能用于“优于所有系统”“全场景完成”“实体可靠运行”等论断。

当前 `paretoLabels` 只在同一配对任务块中，按覆盖最大化与路程、规划耗时、消息字节最小化标注未支配配置，明确 `sampleOnly=true`。`pairedBlockN` 是任务块数，`uniqueGeometryN` 是去重几何数，不能互换。小样本 `uncertainty.confidenceInterval/significanceTest` 保持 null；样本内未支配不等于普适最优，也不证明复杂机制一定比简单策略好。

## 8. 偏离、失败与后续验收

每次修改场景、指标、预算、策略、故障或实现后产生新运行目录，记录原因及发现时间。保存全部失败，不通过重写旧配置、覆盖日志或删除最差 episode 优化结果。软件错误修复后重跑配对矩阵，旧结果作为修复前证据保留。

进入 ROS 2、Gazebo 或实机时另立协议，至少包含地图版本变化、重启、网络分区、定位丢失、动态障碍、多机路径交叉、未知动作结果和独立停止验证。当前单层地形、固定共同坐标、离散射线与静态场景约束不能被后续论文省略。
