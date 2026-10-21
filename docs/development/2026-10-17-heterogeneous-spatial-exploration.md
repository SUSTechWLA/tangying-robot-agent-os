# 异构三维探索与可复现实验：设计和变更记录

设计起始日期：2026-10-17；接口核对更新：2026-10-18。类型：设计、实现边界与迁移记录。基线为首次扫描增量之后的空间导航代码；精确源码身份由本轮实验 manifest 的源文件摘要固定。本页与[实验协议](../experiments/2026-10-17-spatial-exploration-protocol.md)、[实际软件报告](../experiments/2026-10-18-spatial-exploration.md)、[教材补充](../../book/updates/2026-10-17-heterogeneous-spatial-exploration.md)配套阅读。

本次增加可替换的三维观察决策、异构分工、可回放覆盖记录和配对消融入口，作为后续接入在线机器人与论文实验的研究基础。**本次是自研的软件参考实现，没有集成 ROS 1 OmniPlanner，没有完成新的 Gazebo、无人机、足式或实体机器人验收。** 现有 `mapping.build(mode=explore)` 仍使用平面探索适配器；新的研究入口不授予运动权限。

## 1. 为什么采用分层方案

共同问题是“选择下一处值得观察的地方”，不同问题是“这台机器人怎样到达那里”。无人车的路径需要地面支撑；无人机可在自由体积内运动；足式平台还需要设备侧步态与接触验证。将这些差别隐藏在一个 XYZ 寻路器中，会把几何可达误作整机可执行。

本次参考 [OmniPlanner](https://arxiv.org/abs/2603.04284v2) 的跨本体规划与平台抽象思路、[GBPlanner 2 / COHORT](https://marco-tranzatto.github.io/publications/fr_2021_gbplanner_2.pdf) 的单机自治与团队探索分工，以及 [Meeting-Merging-Mission](https://arxiv.org/abs/2109.07764v2) 的通信受限信息交换思路。它们是设计参考，不能将原论文性能作为本系统的实验结果。OmniPlanner 的[官方代码](https://github.com/ntnu-arl/gbplanner_ros/tree/gbplanner3)是独立依赖候选；本轮不移植其 ROS 1 运行栈，也不声称复现其算法。

| 层次 | 本次职责 | 后续可替换的实现 |
| --- | --- | --- |
| 世界与定位 | 三态体素、网格几何与坐标/标定身份；显式区分快照和世界基准变化 | 分块地图、八叉树、多层地形、外部 SLAM/VIO/LIO |
| 观察决策 | 从已观测地图生成三维 frontier/NBV 候选，计算有界可见收益 | 图探索、覆盖路径引导、语义巡检、多视角质量模型 |
| 本体可达性 | 复用 `SpatialPathPlanner` 检查自由空间包络和单层地面支撑 | 曲率约束车辆、动力学轨迹、足式落脚规划 |
| 团队分工 | 依据候选收益、路程和机器人能力分配目标，避免重复占用 | 市场分配、拍卖、MILP、学习策略 |
| 证据与评测 | 版本化事件、覆盖账本、配对实验、独立指标重算 | 实际 ROS 2 / Gazebo / 实体记录适配器 |
| 执行与安全 | 沿用 Runtime 的批准、租约、命令身份、停止和新鲜证据规则 | 本轮不增加未经准入的动作通道 |

## 2. 世界表示和上下文一致性

现有 `navigation.volume.v1` 使用固定 `origin/resolution/shape` 和 `-1/0/100`，分别表示未知、自由、占据。机器人不能穿越未知体素，不能为了获得更高收益放松路径碰撞检查。`terrain25d` 继续要求明确的高程、支撑和坡度；单层地形无法表达同一 XY 上重叠的楼板与桥面。

在线系统有两种不同的变化，协议必须分开处理：

- **同一坐标基准的新观测**：世界、标定与网格几何不变，更新地图快照和采集序号。候选可重新评分，但不因每一帧更改任务授权身份。
- **回环、重定位、重配准或网格重建**：世界、标定或网格依据变化。旧目标、路径与证据不能静默沿用；停止并重新绑定或显式迁移。

候选目标 ID 的命名空间与覆盖账本 `geometry_id(volume)` 一致，绑定 `frameId/worldFrameRevision/origin/resolution/shape`，再加观察体素与方向。本地 `robotId/mapId/calibrationRevision/mapRevision` 保留在路径与快照合同中，不用于共同网格目标命名，以便不同机器人识别同一个观察目标。稳定目标 ID 解决“同一观察位置反复命名”，不代表旧路径仍可执行。执行时仍要用最新合法定位和部署重新规划、核验版本。跨机器人相同的 `world` 字符串并不证明地图已对齐；当前软件实验使用预先声明的共同坐标，不实现跨机器人 SLAM 配准。

边缘保留本机事实和执行日志，上层只交换目标、地图/世界引用、收益、分配与证据摘要，不把体素全集塞进大模型上下文。云端或协调器给出的目标分工属于提案；现有 Host/Runtime 权限、预算和单控制者规则继续决定是否执行。当前研究模块未接入 Fleet 网络数据面，不能据此宣称已支持断网后的生产运动。

## 3. 可替换的三维观察决策

新增核心入口为 [spatial_exploration.py](../../robot/gateway/tangying_robot_gateway/spatial_exploration.py)：

```python
from tangying_robot_gateway.spatial_exploration import (
    ExplorationConfig, SensorModel, SpatialExplorer,
)

# volume / navigation_policy / start_pose 由同坐标、同版本的适配器提供
sensor = SensorModel(range_m=4.0, horizontal_fov_rad=1.57, vertical_fov_rad=1.05)
config = ExplorationConfig(strategy="information_gain", use_global_reposition=True)
explorer = SpatialExplorer(volume, navigation_policy, sensor, config)
proposal = explorer.propose(start_pose, excluded_targets=(), observed_targets=())
```

示例数值只说明软件 API，不是任何设备的标定或实机配置。`start_pose` 和候选 `pose` 使用 `[x,y,z,qw,qx,qy,qz]`；理想传感器沿局部 +X 观察、+Z 向上。地面候选只改变 yaw；空中观察朝向是几何目标，不是已通过飞控验收的机体姿态。实际相机云台、相机与机体外参、飞行姿态与传感器可指向范围由 provider 调试并准入。调用者负责提供同一坐标和版本下的合法地图与位姿；此 API 不替代 Runtime 定位新鲜度、批准和控制器准入。

`exploration.proposal.v1` 返回状态、选中候选、有限候选集合及诊断。候选包含 `targetId`、位姿、几何路径、`visibleUnknownVoxelIds`、`informationGain`、`pathLengthM` 和 `score`。这些字段描述“预期能观察什么”，不构成已获得的观测。

当前候选使用体素自由/未知邻接边界及原地观察，地面方向为四个水平主方向，空中还包括上下方向；不是连续空间、任意朝向的穷举。设 r 为体素分辨率、d 为目标直线距离、L 为认证后路径长度、G 为射线首次命中的未观测 unknown 数：`information_gain` 先按 `G/(r+d)` 筛选有限路径验证，再按 `G/(r+L)` 排序。`nearest_frontier` 初筛使用 `-d`，认证后使用 `-L`，仍要求真实射线收益和合法路径。关闭可见收益评分时，将 G 替换为相邻 unknown 数的代理 H，初筛为 `H/(r+d)`、认证后为 `H/(r+L)`，原地观察的 H 为 1；实际可见性与零收益淘汰仍保留，因此不是完全移除传感器模型。

候选生成、射线可见性和寻路都有预算：默认每次提案最多 64 个候选、12 次完整寻路；每个候选最多 81 条传感器射线、8192 次体素步进。未知或占据体素遮挡后续射线，角点同时穿越的体素按组检查，避免穿过对角墙缝计算收益。有限候选、离散射线与有界搜索属于近似，当前无候选只说明当前快照和预算下没有合法提案，不证明连续空间中不存在解。候选预算优先保留近处候选，并非已经实现拓扑图上的全局最优覆盖。

`raycast_observation` 是离散传感器模型。规划器只能对当前已知地图估计收益；离线环境适配器才能使用隐藏真值产生实际模拟观测。规划策略不得读取真值生成候选、计算分工成本或认证路线；独立环境与 evaluator 可以使用隐藏真值做安全拒绝和评分，但不能把未观测占据或替代路径反馈给策略。

## 4. 消融开关与安全不变量

| 配置 | 对照问题 | 必须保持相同的条件 |
| --- | --- | --- |
| `strategy=information_gain` / `nearest_frontier` | 收益与路程联合选择是否优于最近目标 | 场景、初态、传感器、规划预算、随机种子 |
| `use_global_reposition=false` | 只允许可认证直连，与允许 A* 绕过已知障碍的贡献 | 相同几何可达性与运动包络；不等同于关闭独立全局拓扑规划器 |
| `visibility_gain=false` | 遮挡可见收益相对简单收益代理的贡献 | 实际观测仍执行同一射线模型，不能观察穿墙 |
| 团队协调关闭 | 分工对重复探索、覆盖与通信的作用 | 机器人数量、本体能力、故障时序和总预算 |

上述开关不关闭未知空间禁行、包络碰撞、地面支撑、版本校验、预算、停止或证据要求。安全不变量不是用于获得更高任务成功率的可调奖励项。

策略并非越复杂越好。有限地图、通信成本和传感器能力下可能出现不同 Pareto 最优点；论文应同时比较覆盖、完成时间、总路程、规划代价和通信，不能仅凭单个加权分数宣称“最优系统”。权重调参使用训练/开发场景，最终评价使用冻结的留出场景。

## 5. 团队分工、故障和覆盖账本

实现位于 [spatial_mission.py](../../robot/gateway/tangying_robot_gateway/spatial_mission.py)。每台机器人先使用自身传感器和移动约束提出可达目标，`TaskCoordinator` 再处理重复观察收益与目标占用。这是按机器人的合法候选分配，不是通用异构 DAG/全局最优调度。它检查路径机器人/世界身份、有限航点、目标与末航点一致、申报路程和实际折线长度一致，再生成 `exploration.task.v1` 与 `pathDigest`。拒绝不能到达的候选，不用“无人机可达”推导“无人车可达”。

`assigned` 过期可以释放；`running` 过期转为 `uncertain`，不能因租约结束推断设备停止。默认全任务同时只允许一个 `running/uncertain`，即当前团队是有序协作，不是并发飞行与行车。`acknowledge_stop` 确认设备停止之后才解除未知执行的 fence；关闭协调消融也不关闭此规则。

`CoverageLedger.observe` 接收 `robotId/sourceId/sequence/observedAtMs/evidenceId/worldFrameRevision/geometryId` 与体素分类 `0|100`。同来源同序号同内容幂等；旧序号、倒退采集时间、过时观测、重复证据改名及坐标不匹配拒绝。互相冲突的分类保留在账本并排除覆盖，不能最后写入覆盖原事实；本版没有概率融合和冲突自动裁决。

提案、已分配、已到达、已观察是不同状态。仅规划成功不能增加覆盖，动作结果不明不能直接回收为可重发动作。未声明目标分母时 `exploration.coverage.v1.coverage=null`；独立 evaluator 另外计算任务 ROI 指标。`EventJournal` 用 `exploration.event.v1` 的顺序、前序 hash 和内容摘要保存变化，落盘 `flush/fsync`，并在打开时校验。事件数与 100,000,000 字节上限同时在写前准入，避免成功写出重启后无法读取的过大日志。POSIX `flock` 保证同一 journal 的单写者，第二 owner 得到 `JOURNAL_OWNED`；显式 close 后可恢复。`fsync` 失败后拒绝继续 append，不在不确定落盘后无声继续。哈希链是完整性校验，不是外部签名或防恶意重写的信任机制；文件锁也不是跨机器分布式共识。

`SpatialMissionRunner` 将这些模块连接为有界闭环，使用 `SpatialMapPort.snapshot/capture/check_cancel`、`SpatialDriverPort.execute/stop`、`SpatialReportPort.publish` 和可替换的 `explorer_factory`。它核对快照新鲜度、世界/网格、source 单调性，派发前再次检查地图与位姿未改变；成功回执必须匹配任务和路径，并由命令后的新定位证明 XYZ 与朝向到达。步数、规划路线额度、总时长和 `geometryId` 绑定 `runner_created`，重启不能重置预算或换网格解释旧目标。`commandedPathM` 是保守的已派发路线额度，不是实测里程，结果未知的路线也占预算。

账本存在冲突时 Runner 以 `MAP_CONFLICT` 阻止派发；provider 需重新确认配准/地图依据，以新 mission 处理修正后的事实，不自动用投票或最后消息裁决。`DriverResult.rejected` 必须保证没有未确认的在途动作，否则返回 `unknown`。`stop()` 返回 true 必须来自控制器正向停止回执，不能由超时、lease 到期或软件假定代替。

动作后发生盘满或 journal 容量故障时，Runner 仍独立请求控制器停止；若停止回执无法持久化，保留未知执行 fence，不靠清空 journal 继续。到达角误差使用归一化四元数计算，输入容许的小范数漂移不能使实际角差被低估。这些软件不变量仍需以具体控制器和实体停止测量验证。

| 合同 | 身份与含义 | 不赋予的能力 |
| --- | --- | --- |
| `exploration.proposal.v1` | 当前快照中的候选、几何路径、收益与预算诊断 | 不是动作批准，不是已观测事实 |
| `exploration.task.v1` | mission/robot/world、目标、pathDigest、期限和持久状态 | 不替代现有 Runtime 审批、命令 lease 与驱动准入 |
| `exploration.coverage.v1` | 共同网格、已分类体素、冲突、重复和可选分母 | 无分母时不报告覆盖百分比 |
| `exploration.event.v1` | 核心账本/协调器的顺序与哈希链 | 不提供网络鉴权或分布式锁 |
| `exploration.progress.v1` | Runner 的有界进度、停止原因与证据投影 | 不是新的云边任务权威 |

在线 provider 还必须将定位质量、变换/标定、时钟与采集一致性映射到现有完整定位合同；Runner 的 `localized` 与采集年龄检查不能替代定位协方差或真实来源认证。新消息尚未注册为 Fleet/edge-exchange 的生产网络协议，后续网络适配需沿用已有身份认证、TTL、重放防护及对象大小限制。

当前离线协调不提供多机联合时空避碰。基准串行执行时会检查静态真值障碍和静止同伴包络，但这不等于并发轨迹协调。即使两个候选不同，路径也可能在同一时间经过相同通道。真实并发多机接入需增加时空预约、冲突检测、停止优先级与控制器实时避障，并在 Runtime 授权之后单独验收；不能将目标去重称作轨迹无碰撞。

## 6. 研究入口、测试与证据分级

离线基准入口、配置、指标公式、全部消融、回放与归档步骤见[实验协议](../experiments/2026-10-17-spatial-exploration-protocol.md)。[基准脚本](../../scripts/spatial_exploration_benchmark.py)调用实际探索器、覆盖账本和协调器，使用独立环境端口组织离线任务；`SpatialMissionRunner` 的 provider 闭环需由合同测试另行验证。基准运动是软件几何状态转移，不包含 Gazebo 动力学、飞控、真实传感器噪声或硬件。

基准故障中的 `disconnect` 只切断观测上传/共享，本地 scheduler 仍然可用；它检验局部知识和补传，不证明现有 Fleet claim 失效后可以继续运动。`no_coordination` 保留观测地图交换和安全规则，仅对照目标重叠选择与团队已观测收益。实验分母、受测机制与保留机制必须一起引用。

验证按层次进行：核心函数和拒绝不变量；团队分配与事件回放；固定种子的配对离线任务；后续 ROS 2 记录控制器；实际 Gazebo；实体平台。每层分别报告，不能把离线多个 episode 拼接成实体长期成功率。实际测试命令、数量、失败和最终结果由本轮实验报告登记，设计页不预填数值。

实际执行、修复前后身份、配对图与完整来源见[2026-10-18 报告](../experiments/2026-10-18-spatial-exploration.md)。`001` 保留为日志字节上限准入修复前的冻结运行，最终复测使用新目录 `002`；实际状态与最终引用组以报告和清单为准。同设置的软件复测不增加独立几何或任务块样本数，不覆盖原始数据。

## 7. 兼容、迁移与后续接线

现有二维 SLAM、首次环视、Nav2、空间路径握手和 `mapping.build` 行为保持原入口；已有地图与实验不改名、不重写。新模块以独立策略和证据协议扩展，旧部署无需导入研究基准配置。

后续在线适配按以下顺序实施：

1. 地图适配器发布有采集时间、世界/标定和快照身份的注册观测，冻结研究合同。
2. 边缘探索器使用本机地图产生有界提案，由 Host 检查授权目标与预算。
3. 每个目标在当前空间部署重新规划，经 ROS 2 控制器准入、租约与停止合同执行。
4. 仅用命令后的新鲜传感证据提交观测；分工回执经已有鉴权数据交换通道传输。
5. 对断网、重启、定位丢失、动态障碍、路径交叉和结果未知分别验证，再进行硬件现场放行。

后续增加规划器使用相同输入、提案和观测边界；增加指标不改变原始数据；增加场景生成新 run ID。实验中的策略、地图、版本和失败不能由模型摘要覆盖。

| 扩展需求 | 实现与注册位置 | 保持的比较条件 |
| --- | --- | --- |
| 第二种探索算法 | 实现 `ExplorationPolicy.propose`；基准显式登记 `BACKENDS`，Runner 注入 `explorer_factory` | 仅接收已测地图，同一传感器/预算/本体与提案合同 |
| 不同传感器或观测质量模型 | `ObservationModel` 与在线 `SpatialMapPort.capture` | 来源/时间/世界和实际观测证据；不能把预测当事实 |
| 新场景/地图集 | 基准 `SCENE_BUILDERS` 生成独立 `Scene` | 隐藏真值与策略输入隔离，固定 ROI/seed/故障 |
| 第二种分工策略 | 保留 `exploration.task.v1` 和未知执行 fence，显式注入/替换协调层 | 单控制者、停止证据、相同可达候选和预算 |
| 第二种运动规划/设备 | 新导航 provider 和 `SpatialDriverPort`，输出可验证路径与回执 | 本体约束、当前快照、批准/租约/停止和命令后定位 |
| 新指标 | 对冻结事件另写版本化独立导出器 | 原始字节不改，分母/单位/缺测明确，保留旧结果 |

这些是显式 Python 端口与注册点，不是已实现任意远程插件自动发现。每种新提供者先完成拒绝与兼容合同，再进入同一配对矩阵。
