# 首次进入未知场景的探索扫描升级记录

日期：2026-10-17。起始基线：`980e8b4d8`。本文记录本轮已实现的模块、问题修复和验收方法；实际仿真尝试及指标另列实验记录。首次协议先于运动登记，后续观察判据与实现修复在本文追加说明；不同尝试仍以各自冻结的源码和预登记为准。

## 问题与目标

机器人第一次进入环境时，任务应是“先观察、判断可行空间，再持续选择值得观察的位置”，而不是把注册房间巡检路线改名为探索。已有实现具备已知自由空间上的 frontier 选择、信息收益与路程权衡、黏着目标、局部避障拒绝后的重新选择、分段地图发布和里程预算。升级重点是首次观测稀少时的主动扫描、可解释的探索终止以及完整实验留痕。

“无预建地图”不等于“无定位、无在线地图”。本轮保留机载位姿、RGB-D、在线地图和确定性的运动安全门。Gazebo 中的仿真位姿/里程计与机载视觉惯性或激光惯性定位不是同一证据等级，实验必须披露来源。本轮实现针对平面地面机器人；FUEL 探索栈、EGO 连续轨迹后端和相关无人机算法均未集成，也不把无人车结果外推到无人机或足式。

## 保持清晰的模块边界

| 模块 | 职责 | 边界 |
| --- | --- | --- |
| 边缘 Host / 执行器 | 审批、能力调用预算、长操作 lease、取消和证据核验 | 模型不能直接写速度或扩张授权 |
| `RobotWorkflow` | SLAM 会话、首帧、分段发布、标定与地图身份、运动资源占用 | 继续使用注册 `mapping.build` 服务 |
| `SurveyRunner` | 初始观察、探索目标生命周期、进展与终止策略 | 通过 `SurveyMap` / `SurveyDriver` / `SurveyReport` 端口工作 |
| `exploration.py` | 基于当前测量地图的目标和路径选择 | 不读取预定房间路线或仿真真值 |
| 本体驱动与 Runtime | 有界动作、传感器新鲜度、碰撞与停止 | 拒绝动作不可被策略绕过 |
| 独立验收 | 初态、真实轨迹、地图包、停止与失败记录 | 真值仅用于事后评分，不能泄露给规划器 |

相关实现入口：[`survey.py`](../../robot/gateway/tangying_robot_gateway/survey.py)、[`exploration.py`](../../robot/gateway/tangying_robot_gateway/exploration.py)、[`robot_workflow.py`](../../robot/gateway/tangying_robot_gateway/robot_workflow.py)、[`gazebo_workflow.py`](../../robot/gateway/tangying_robot_gateway/gazebo_workflow.py)。

## 本轮已实现的行为

`exploration.py` 的局部未知比例按完整观测圆盘计算，超出现有小栅格的部分继续视作未知，避免首帧小图被误判“已看完”。路径净空以实际安全包络计算，本体足迹补充自由区不能覆盖占据格、净空约束或真实拒绝区。前沿候选先限制在当前机器人可达分量，再花费有限的候选/搜索预算；视线受已观测障碍阻挡，不因距离接近就把墙后的空间计为可见。

`SurveyRunner` 在首次环视前检查取消、剩余行程、帧数、段时限和连续深度不足；可选的后续环视也在动作前检查停止条件。第一段记录初始扫描，续段不重新授权初始环视。全未知或没有自由格的地图报告 `insufficient_map_evidence`；不再因“前沿数为零”判为完成。重复无效路径和无进展均有终止上限。

已到达或无观测收益的目标记录为 `visited_viewpoints`，通过 `exclude_viewpoints` 从下一次视点选择中排除，位置仍可通行。真正的驱动拒绝才进入 `refused` / `avoid_xy`，并影响绕行。这样不会把“站过这里”误当作“这里存在障碍”，也不会因把当前位姿拉黑而把机器人困在原地。

`RobotWorkflow._look_around()` 只请求本体支持的平面原地有界转动，每个命令不超过 0.5 rad，并保留时间、帧数、连续深度不足、停滞和安全拒绝边界。首次实测发现逐个子目标的控制器到达容差会积累成扫描角度缺口，现已改为：对连续测得的 yaw 差进行有符号累计，以累计目标减去实测进度生成剩余角度，跨四分之一圈保留残差；绝对角路程另行记录。反向摆动不能算作完成一圈。该修复需要新的冻结版本实验，不能追改首轮证据。

连续深度不足计数在新的操作/SLAM 段以及有效关键帧后重置，累计拒绝帧数另记，避免把不相邻的坏帧当成持续失明。`_open_leg()` 在资源获取前后检查取消与租约，沿用整操作同一 `Event` 和取消标记；续段不能清除用户取消或把已失效的租约重新变成运动授权。

## 新增状态、地图来源与数据窗口

| 字段 | 含义与限制 |
| --- | --- |
| `exploration.initialScanAttempted` / `initialScanStatus` | 初始扫描端口是否调用和返回；`returned` 不等于已经转满一圈 |
| `coverageScope` / `terminationMeaning` | 当前观测栅格的非盲区前沿范围，以及 `frontier_exhausted` 或 `partial`；不表示未知建筑全覆盖 |
| `refused` / `visitedViewpoints` / `routeFailuresSinceProgress` | 分开记录真实拒绝、已退休视点、进展后连续路径失败 |
| `scanEvidence.looks` | 最近 12 次扫描的有界窗口，含 `phase`、`status`、`acceptedFrameDelta`、`commandCount`、`completedSweeps`、`elapsedSeconds` |
| `rotationTargetRad` / `measuredYawAdvanceRad` / `measuredYawTravelRad` / `rotationResidualRad` | 目标角度、有符号实测进度、绝对角路程与目标剩余量；四者必须分开解释 |
| `maxTranslationM` / `coverageClaim` | 扫描时实测平移偏移，及固定的 `not_measured` 覆盖声明 |
| `scanEvidence.legs` | 最近 6 段的探索终止与段行程记录；不是无限历史账本 |
| `consecutiveInsufficientDepth` / `depthRejectedFrames` | 连续不足的坏帧数与整操作累计拒绝帧数，不能互换 |

`mapping.status` 返回这些结构，SLAM 地图来源记录的 `exploration` 中保存窗口、最终段、`frontiersExhausted` 和 `wholeSceneCoverage: not_measured`。长期实验历史由逐次 JSONL、运行目录和不可变地图包承担，不能把最近 12 次/6 段窗口称作完整历史。`operationTravelledM` 是整操作累计行程，`travelledM` 是当前 SLAM 段，两者仍各自保留。

## 测试与实验安排

本轮补充了小图边界、墙后前沿、严格足迹、可达候选、重复无效视点、无自由格、深度计数、动作前停止、初始扫描残差和续段取消竞态的确定性回归。验收记录器另有成功、已有图拒绝、超时停止、禁止覆写、启动结果不确定、导航未就绪与源码变化的 fixture 检查。测试覆盖的通过项以测试输出为准，不能替代物理仿真。

真实 Gazebo 首轮已使用隔离实例执行，实际地图、预算停止、扫描角度缺口与后续修复版本的指标在独立结果中登记。后续尝试必须新建目录和初态；扫描回调返回与整圈实际完成分开判定。SLAM provider/continuation、mapping coverage 等相关回归仍应随实际改动运行。

## 验收入口与已发现的记录缺口

本轮新增 [`validate_exploration_first_scan.py`](../../scripts/validate_exploration_first_scan.py)：拒绝覆盖证据目录，先写 manifest/预登记再发 RPC；检查空注册地图库存与 mapping 模式，调用有 30 秒租约的 `mapping.build(mode=explore)`，轮询续约；保存完整请求、状态及轨迹，退出时核验地图产物和导航身份。超时取消后等待同一操作终态；启动响应丢失时只读等待租约失效，不盲目取消另一会话。源码前后变化使结果无效。该工具仍是 Runtime 验收，不代表通过自然语言 Host、真实硬件或全环境覆盖验收。

[`evaluate_capability_goal.py`](../../scripts/evaluate_capability_goal.py) 支持批准前检查计划中的 `mapping.build`、`mode=explore` 和预算，适合验证自然语言到能力计划的入口。它尚不能独立证明地图冷启动、初始扫描或空间覆盖。

[`survey_run.py`](../../scripts/survey_run.py) 是 Runtime 层调试入口。基线允许重复输出目录，且启动异常、轮询异常、超时取消后的终态记录不完整；用于论文前应补充不可覆盖的运行目录、预登记、全部异常的 `finally` 留痕和取消后的复读。直接 RPC 结果不能冒充自然语言 Host 流程测试。

[`build_sim_map.py`](../../scripts/build_sim_map.py) 默认 `survey`，异常路径不落完整报告，轮询去掉轨迹；不适合作为本轮未知环境唯一证据。所有运行必须显式选择 `explore`，并独立保留轨迹与操作身份。

## 升级前只读快照与隔离要求

下列是升级前审计快照，不代表后续实验的实时状态：旧 Gazebo 容器 `tangying-gazebo-4d6962a6c3d7-gazebo-house-1` 启动时间为 `2026-10-15T14:48:48.106240918Z`；gRPC `127.0.0.1:50461` 返回 `gazebo-home_furnished`。旧扫描 `scan-73e5d48169a5` 已完成，已注册地图 1 张，导航处于 `localization` / `localized`。旧 Console `8927` 当时未监听。该审计阶段只执行信息和状态读取，没有重置地图或改写旧证据。

这个旧实例不能作为“第一次从未知场景建图”的初态。新实验需要独立的 artifacts 目录、Compose 项目/地图存储、机器人身份、端口和 ROS domain；没有导入旧图不够，还必须确认 RTAB-Map 数据库与定位 sidecar 没有沿用旧地图。启动脚本会在同目录恢复保存配置，重复 `start` 也不等于清空环境。

共享宿主机资源不足时，应先确认旧实例无活动操作，再可逆暂停旧容器；新实验使用自己的存储，完成后停止新实例、恢复旧容器。暂停/恢复也要记在实验日志中，不能同时运行两个物理实例导致 CPU/内存竞争却把传感器延迟归因于算法。

详细方法与指标定义见[实验协议](../experiments/2026-10-17-exploration-first-scan-protocol.md)。

## 调研如何落到本轮实现

[EGO-Planner 原论文](https://arxiv.org/abs/2008.08835)解决局部轨迹优化及动态可行性，不能仅接一个轨迹后端就获得完整未知环境探索。本轮保留已经委任的底盘控制和安全层，把改进集中到主动观察、在线 frontier、实测反馈、故障分类和可回溯来源；没有声称移植 EGO、接通飞控或完成三维 next-best-view。[官方 EGO-Swarm 仓库](https://github.com/ZJU-FAST-Lab/ego-planner-swarm)作为未来局部轨迹端口的候选参考，尚未验证本项目的 ROS 2/Jazzy 兼容性。

[Meeting-Merging-Mission](https://arxiv.org/abs/2109.07764)研究受限通信下的多机器人探索协调。本次先把单机观察和终止语义做实，不将它的多机策略直接套到现有云边调度；后续地图会话、观测序列、坐标/世界版本仍应通过显式合同交换，不能让其他 Agent 的地图摘要直接获得底盘控制权。

## 可执行的短程航点

003 真正扫描暴露格心与真实位置的差别：远端目标虽有路线，共同首段可能低于驱动的 0.05 米最小步，从而连续退役目标仍不前进。`next_waypoint` 新增可选 `robot_xy/min_step_m`；Survey 使用实测位置验证候选整条直线，同时满足 lookahead 与内部 `minStepM`。粗网格候选仅在同 mask 验证后取有界前缀，不放宽净空、不跨未验证拐角。`minStepM` 为 Survey 内部配置，`mapping.build` 及验收 CLI 没有新增这个用户参数。

`exploration.nextWaypoint`、`waypointDistanceM`、`minStepM` 提供最后一个候选及执行距离诊断。没有满足最小距离的安全候选时，报告 `no_executable_waypoint` 的部分停止，不能说几何路线不存在或全图完成。新字段不追填001–003原始记录。

## 续建帧审查与修复

首轮单段验证后发现非零锚点下命令/进展/未知查询混帧：`_drive_step` 在边界将 planning-map 目标逆变换到 driver-world 一次，Survey 的前后位姿统一在 planning frame 并归一化 yaw 差，环视未知查询使用 map-frame XY。原始004单段仿真不包含此续建分支，新增非零平移/旋转锚点、静止与地图查询的软件回归，不补造物理续建结果。

## 最终验证

`make build && make lint && make generate-check && make test` 通过：Makefile 维护的 Go 包全过，Python 主套件 3515 项及单列跨运行时边界 2 项通过、40 项跳过，前端 517 项通过。Python 的 20 个警告为 2 个 pytest 插件重写提醒及 18 个既有 NumPy matrix 弃用提醒。266 项针对性回归与全量套件重叠，不另加到总数。另有独立出版环境的 52 项 book/docs 检查、`go test ./tests/docs`、`make book-check` 及目录一致性检查通过。

四次地图包及 381 条底层 ended 动作的零速度命令发布回执独立核验通过；完整全量日志与软件验证摘要见 `docs/experiments/evidence/2026-10-17-exploration-first-scan/post004-software/`。这些检查不升级为新的实体、全环境覆盖或非零锚点物理续建认证。
