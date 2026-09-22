# Gazebo Harmonic 稳定版本、吸附工位与系统闭环验收

日期：2026-09-22。候选分支：`codex/gazebo-default`，[PR #5](https://github.com/SUSTechWLA/tangying-robot-agent-os/pull/5)。本文记录真实 Docker / ROS 2 / Gazebo 运行结果，失败保留；不把模拟器升级、工具目录齐全或单个工具返回 OK 等同于全部业务可发布。

## 1. 验收对象与版本

本轮将默认仿真升级到 **Gazebo Harmonic 8.15.0 + ROS 2 Jazzy + ros_gz 1.0.24**，接入当前 Local Agent、任务批准、执行 journal、观测、前端相机与地图服务。Jazzy/Harmonic 是[官方支持的配对](https://gazebosim.org/docs/harmonic/ros_installation/)。容器内 `/opt/tangying-nav/simulation-packages.txt` 记录实际安装版本；验收报告同时记录镜像 ID、源代码 SHA256、Git HEAD 和工作区是否有未提交修改。Docker 构建会固定基础镜像摘要，但 apt 依赖不是离线快照，因此“相同源码”不能替代“相同运行镜像”的版本证据。

验收机器是 macOS 主机上的 Linux ARM64 Docker 环境。装修家庭相机为 256×192，其余场景为 320×240。感知仅使用 Gazebo RGB-D 传感器与机器人里程计/IMU。所有场景包含同一已标定操作工位，但房屋和相机分辨率不同；这能测试集成差异，不能视为四个独立的任意物品识别基准。

## 2. 分层设计和成功定义

| 层级 | 实际调用 | 必须具备的证据 |
|---|---|---|
| 引擎和驱动 | Compose → ROS bridge → Robot Runtime | Gazebo adapter、真实版本、启动退出码、世界和标定身份 |
| 感知 | 两路公开 Observe | PNG RGB/深度、米制点云、来源、采集时位姿、时间有效性 |
| 基础执行 | arm.move、取消、恢复、正负到达判定 | 实测关节变化、取消后漂移、错误目标被拒绝 |
| 工位业务 | 自然语言 → 生产任务 API → 批准 → 调度 → 工具 | 任务 SUCCEEDED 且 LOCAL_RUN_SUCCEEDED；两个物体均完成抓放 |
| 独立物理验收 | Gazebo 原生状态订阅 | 释放、容纳、支撑高度、直立、连续稳定，不能只采信 Agent 自评 |
| 导航和地图 | Nav2、mapping.*、calibration.* | 实测位移；从 RGB-D 采集到地图保存、激活和取消的服务链 |
| 产品发布 | 最终提交 CI 和完整业务清单 | 未通过、未知、未覆盖必须单列，不能以单机通过替代 Linux CI |

`evaluate_gazebo_scenes.py --business --require-full` 每个场景创建独立状态并顺序执行。它先验证相机、服务、关节、取消和恢复，再创建“把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来”。矩阵隔离用户模型配置，使用确定性规划器检验系统机械链路；不据此评价 LLM 的推理能力。`evaluate_gazebo_business.py` 单场景模式会记录当前实际 provider/model，可另做真实 LLM 闭环。

独立物理验收收集 5 个不同序号的原生状态，模拟时钟跨度至少 200 ms。单次任务成功是任务状态与物理谓词的合取，而不是二者取其一：

\[
S = S_{task}\land S_{journal}\land\bigwedge_{o\in\{cup,bottle\}}
(Released(o)\land Contained(o)\land Supported(o)\land Upright(o)\land Stable(o)).
\]

当前圆柱半径 45 mm。放置检查要求中心相对容器中心的两个水平轴误差均不超过 43 mm，中心高度比容器模型中心高 75±12 mm，倾角不超过 0.15 rad，连续样本位移不超过 4 mm。抓持检查要求真实连接身份正确，且物体比抓取前至少抬升 55 mm。阈值对应此工位模型，不能未经重新标定用于其他容器或实机。

## 3. 吸附模型与完整执行路径

用户允许先用简单吸附打通业务。本实现通过 Gazebo 的原生 `DetachableJoint` 建立固定物理连接，限制为两种已标定物体、合法末端、9 cm 内接近；物体会随关节运动，并在释放后受重力和碰撞约束。运行期间没有物体位姿写入，也没有用世界中物体坐标替代 RGB-D 规划。原生物体状态仅用于吸附反馈和结果验证。模型不包含真空泵、压力曲线、柔顺密封或真实夹爪摩擦成功率。

参考：[Gazebo DetachableJoint](https://gazebosim.org/api/sim/8/classgz_1_1sim_1_1systems_1_1DetachableJoint.html)。独立 `InitialJointPose` 插件仅在构建世界时设置机器人的初始关节姿态，并与位置控制器初始目标一致；它没有运行时命令接口，也不初始化任务物体的抓放结果。

运行链为：

1. 从头部 RGB-D 中解析唯一颜色候选，重建其几何位置。
2. 校验目标、计划所属任务、观测时效、底盘/物体漂移和夹具空闲状态。
3. 用当前关节反馈求逆运动学，沿分段轨迹接近；末段重新观测目标以闭合误差。
4. 近距离建立物理连接，抬升并验证。
5. 以 RGB-D 容器表面规划释放位置，释放、退让并连续验证。
6. 进入下一个子任务前收臂，再执行底盘定位和新观测。

检测器的适用域显式限定为启动里程计系下 `x∈[0.25,0.70] m, y∈[-0.55,0.25] m, z∈[0.65,1.20] m` 的已标定工位。该先验用于排除背景同色家具，不是任意家庭物品检测器；工位内多个同色候选仍拒绝。头部相机提高至底盘上方 1.30 m、俯视 45°，SDF、TF 与标定参数同步。

## 4. 实验中发现的问题与对应修复

以下是开发过程中的诊断对比，不是随机化因果试验。多项变化叠加时，不把最后一次通过归因于单一变化。

| 观测到的问题 | 可定位证据 | 修复及验收意义 |
|---|---|---|
| 健康状态变化使工具目录 revision 改变，执行中命令被判旧版本 | 场景矩阵中途 catalog 不匹配 | revision 仅涵盖稳定工具合同；可用性在每次执行入口另行判断 |
| RTAB-Map 当前帧约 120 个特征，但字典为 0 | 特征记录含负词 ID，数据库词典为空 | 启用好签名新图；Gazebo 的 Kp/BadSignRatio=0.1，保留实际词典及定位就绪门槛 |
| 只有后支撑，机械臂伸出后底盘倾斜约 8.8° | 独立物理位姿与倾角拒绝一致 | 增加前球轮并统一接地面；后退验收中倾角接近 0 |
| 出生高度 0.16 m 使轮子初始穿入地面 | 底盘高度在抓取期间从约 0.177 m 回升 | 四场景出生高度统一为轮子接地高度 0.20 m |
| 工具悬伸影响移动包络，物体在出生时被机械臂碰斜 | 图像与物理姿态吻合 | 收臂出生姿态；空载导航前真实收臂，持物时拒绝通用移动恢复 |
| 收臂准备再次获取导航锁，第二子任务 ROBOT_BUSY | 已完成首个放置，第二步预定位失败 | 同线程可重入事务锁，跨线程仍互斥；加入并发回归 |
| 相机被收起的机械臂遮挡；升高后出现同色家具干扰 | 失败图像、候选区域和缺失实体 | 调整视角，并用显式工位体积限制检测域 |
| 极快导航返回后，完成观测早于派发 3 ms | CLOSURE_EVIDENCE_STALE | 导航结束后等待两路新帧；超时明确要求核对，不改写时间戳 |
| 原生 `gz topic -n 1` 偶尔多输出一帧 | 已成功任务的 oracle JSON 出现 Extra data | 按消息解析并按序号选择最新帧，不按成功/失败选择帧 |
| 扫描把实际地面和自身顶板当作障碍 | 头部点云中 154 个阻挡点位于底盘顶板约 z=0.163 m，基座相机无对应障碍 | 按实际接地面修正高度带；仅移除注册底盘几何内的自身回波，保留周围安全余量内的障碍 |
| 到达检查因无关的机械臂反馈过期被拒绝 | verify_arrival 返回 JOINT_FEEDBACK_STALE | 按工具所需传感器声明阻塞；导航仍需关节反馈以安全收臂 |

RTAB-Map 的特征准入依据可查[上游 Memory.cpp](https://github.com/introlab/rtabmap/blob/master/corelib/src/Memory.cpp) 与[参数定义](https://github.com/introlab/rtabmap/blob/master/corelib/include/rtabmap/core/Parameters.h)。这里修正的是当前分辨率下的实际特征准入，不注入假词典、不设置导航强制就绪。

## 5. 已记录结果与统计解释

早期 `stable-business-matrix-3` 四场景工位任务为 **4/4**，各场景完整验收耗时分别为 home 47.270 s、tabletop 43.627 s、home_task 37.009 s、home_furnished 38.657 s。该结果属于收臂/相机进一步修改之前的版本，不能充当最终代码的验收。

后续回归保留了全部失败：第 4 轮暴露收臂后的遮挡；第 5 轮 tabletop、home_task 通过，home 暴露 3 ms 的完成证据时序问题，home_furnished 暴露同色背景干扰；第 6 轮三个场景通过，home 的基础到达检查因无关关节反馈过期而失败。没有删除这些实验，也没有用自动物理重试覆盖失败。

工位修复阶段的 `stable-business-matrix-7` 为 **4/4 通过**：两路相机、服务、关节、取消/恢复、正负到达判定及双物体任务和独立物理 oracle 全部满足。每个场景一次，规划器为 deterministic；耗时包含该场景业务 runner 的观测与 oracle 采集，不包含容器启动。

| 场景 | 基础检查 | 双物体任务及物理 oracle | 业务验收总耗时 |
|---|---|---|---:|
| home | PASS | PASS | 49.588 s |
| tabletop | PASS | PASS | 47.689 s |
| home_task | PASS | PASS | 48.186 s |
| home_furnished | PASS | PASS | 49.008 s |

原始结果均保留在本机忽略目录 `artifacts/gazebo-migration/stable-business-matrix-*/`，不随 Git 分发。每个 report 均保存实际镜像 ID 和源摘要，开发期工作区状态明确为 dirty；修改后的重新构建/部署应复跑同一 runner。

在四场景矩阵之后，单独修复并复测有界扫描的地面/自身过滤。`stable-workflows3` **通过**：标定读取、重新计算、保存，手动扫描实测 **0.424808 m**，地图保存、激活、库存查询、重载、导航地图和冲突读取，以及新扫描取消。该测试只证明局部地图服务闭环，不证明全屋覆盖。

独立导航已有一次 Nav2 后退 0.15 m 的通过记录，实测约 **0.1456 m**；向工作台内部发送前进目标会被障碍规则阻止。这是局部移动证据，不能推广为全屋可达。

`stable-return-business2` 从扫描终点返回工位仍**失败**：底盘从里程计约 (-0.421,-0.025) m 移动到 (-0.070,-0.072) m，Nav2 报 Failed to make progress，任务按 NAV2_ACTION_ENDED 结束。该版本未通过“扫描后自动返站并抓放”。后续差速控制器回归单独记录，不覆盖这次失败。

### 5.1 真实模型、严格验证与导航边界诊断

真实模型试验使用用户已配置的 OpenAI-compatible `deepseek-flash`，`TANGYING_GVF_ENABLED=1`。与确定性编排矩阵分开记录，不以自然语言请求本身证明调用过模型。保留如下顺序试验：

| 本地归档 | 任务结果 | 可定位原因与修复 |
|---|---|---|
| stable-strict-llm1 | 第二物体抓取后失败 | 吸附已建立，单纯上升 10 cm 的路径不可达；抬升改为在底盘系向内 4 cm、向上 10 cm |
| stable-strict-llm2b | 两次抓取通过，第二次放置失败 | 托盘上方 16 cm 的中间点不可达；改用保留底部间隙的 12 cm 中间点 |
| stable-strict-llm3 | 放置物理谓词 VERIFIED，工具仍失败 | 放下后的垂直退臂不可达；工具失败与物理完成同时如实记录，退臂增加向内 4 cm |
| stable-strict-llm4 | 第一物理步骤 UNKNOWN 并停止 | 新导航结果的嵌套字段违反公共 Result 标量约定；改为 JSON 字符串并加入公共边界校验 |
| stable-strict-llm5 | 第一项抓放通过，第二项定位目标失败 | 退臂仍遮住蓝瓶；将放置后收臂纳入当前动作，再验证释放稳定性 |

`stable-strict-llm2` 在调用任务之前因 oracle 容器名称错误退出，是验收配置错误，不计为物理任务试验。所有实际执行失败都没有自动重试物理命令，下一轮在新的模拟世界中重新验收。抓取 lift 的实际最低 55 mm、放置稳定性、直立和容纳阈值保持不变。

`stable-workflows4` 在严格模式下明确拒绝 `calibration.run`，代码为 `GVF_CONTRACT_REQUIRED`。这是尚无变更服务合同的能力边界。默认模式 `stable-workflows5` 的标定、局部扫描保存重载再次通过，实测 0.424416 m；两种模式不能混称为同一完整能力。

共享控制器原来为可横移底盘设计。当差速底盘只能取 $v_y=0$，只对近目标欧氏距离评分时，原地转向没有即时位置收益，并不能保证收敛。Gazebo 单独使用 [Nav2 RPP](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/controller_plugins/configuring_regulated_pp/) 与角度进展检查，保留原先 5 mm / 0.03 rad 的 Nav2 目标容差及碰撞检查。

`stable-rpp-return1` 从扫描位置前进到约 (-0.089,-0.023) m 后，被 RPP 的碰撞预测拒绝。对矩形底盘，转向时前缘的最大前向投影为 $a\cos\theta+b|\sin\theta|$，因此消除最后的侧向偏差会比直行扫过更宽范围。已标定工位增加 (-0.30,0,0) 的入口对正位姿，每一段仍调用 Nav2；入口被拒绝就停止，不执行后续入站。这是有场景先验的路径约束，不是任意场景导航最优性的证明。

`stable-rpp-return2` 的入口对正把横向误差降至约 0.36 mm，但仍在 x≈-0.077 m 瞬时拒绝。保存的成本图和实际足迹显示拒绝后当前足迹内无持久致命格。保留两次显式诊断命令：第一次从该已知停止状态继续，到 x≈-0.032 m 再次拒绝；第二次设置 Nav2 `failure_tolerance=1.0`，遇到无合法控制量先发零速度，在最多 1 秒内等待新观测，随后到达原点。该配置不忽略碰撞，不改变足迹和到位阈值；持久障碍仍超时失败。这些是有干预的诊断，不能计入一次性完整任务成功率。完整新世界回归单列。

自动探索曾得到 24 帧、12 次配准、约 0.1456 m 移动，但最终因无可达前沿结束；这不是全屋建图成功。家庭语义房间路线目前没有经过完整标定与执行验收，不能把操作工位重新命名为厨房来取得表面成功。

即使最终 4/4 通过，也只是每个场景一次的集成烟雾测试。在独立同分布这一额外假设下，4 次全成功的 95% 单侧二项置信下界也只有约 **47.3%**；实际四场景共享机器人和工位，独立性更弱。要声称生产级可靠性，还需多个初始状态、扰动、故障注入、长时运行与预先固定的评测集。开发过程中反复修改后的通过率不等于盲测成功率。

### 5.2 建图坐标变化与里程计目标不变性

`stable-workflows7` 再次通过，局部扫描实测 0.427636 m。但 `stable-rpp-return3` 在返站时仍失败，停止位姿约 (0.0475,0.0020) m，已经越过所请求的里程计原点。这暴露了目标坐标的语义问题：提交时只转换一次的 map 目标会随 SLAM 的 map→odom 校正而对应到不同的物理位置。

设请求固定在里程计系的位置为 $p_o^*$，将里程计坐标映射到地图坐标的实时变换记为 $T_{mo}(t)$。旧实现固定 $p_m^*=T_{mo}(t_0)p_o^*$，执行时实际追踪的里程计目标变成 $T_{mo}(t)^{-1}T_{mo}(t_0)p_o^*$。除非变换不变，否则它并不等于请求位置。Gazebo 专用行为树采用 Nav2 [GoalUpdater](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_behavior_tree/plugins/decorator/goal_updater_node.cpp)，更新 $p_m^*(t)=T_{mo}(t)p_o^*$；在同一变换时刻，$T_{mo}(t)^{-1}p_m^*(t)=p_o^*$。这是坐标代数的不变性，不是带时延、定位噪声和离散控制时仍必然成功的证明。

侧车以 10 Hz 更新目标，Nav2 以 2 Hz 重新规划；显式 map 目标和原 MuJoCo 行为树保持原有语义。取消及终态会删除待更新目标，过期变换取消导航并返回 GOAL_TRANSFORM_LOST。真实 ROS 节点回归测试覆盖地图平移、固定目标、取消先于确认和过期变换，最终 12 项通过；闭环效果需独立实测。

审查同时修正 Gazebo 碰撞包络为 x=±0.330 m、y=±0.335 m，包含新增前球轮。已行驶区域的圆形净空证明只采用该矩形的内切半径 0.330 m，不能使用外接圆声称机器人未覆盖区域也已被验证。

`stable-workflows8` 局部扫描实测 **0.427019 m**，标定、保存和重载全部通过。紧接着的 `stable-rpp-return4` 使用实际配置的 **deepseek-flash**，在同一世界中自动返站并完成双物体顺序任务，任务状态、执行 journal、独立物理 oracle **全部通过**，业务验收耗时 **88.709 s**，最终里程计平面误差 **4.285 mm**。该次镜像源摘要为 `50390034163947cb3d6a05b42d4440d27e824b7c4cb1cead50acf04dfe993dc5`；后续 5 mm 前球轮包络修正仍需最终镜像回归，不能隐去构建差异。

### 5.3 严格物理验证与运维模型接口

`stable-strict-llm6` 在新的模拟世界中使用 deepseek-flash 和 `TANGYING_GVF_ENABLED=1` 完成双物体顺序任务：**61.853 s，任务与独立物理 oracle 通过，12/12 份 STATE_REPORT 为 VERIFIED**。报告分别覆盖两次预定位、导航、抓取、抓持验证、放置和放置验证。此前 5 次失败保留在 5.1 节，不能把调试后的单次通过解释为稳定成功率。

该次任务中的 Ops 只读恢复仍失败，且并未执行物理重试：内部 `telemetry.read` 工具名中的点被 OpenAI-compatible 服务拒绝，HTTP 400。随后独立诊断又确认当前模型思考模式不支持 `tool_choice=required`，与 [DeepSeek 官方接口约束](https://api-docs.deepseek.com/api/create-chat-completion/) 一致。

修复在模型请求边界建立无冲突合法工具名与内部名称的映射，响应只允许解码本轮提供的名称；不改变执行器权限。请求使用兼容的 auto，执行端仍拒绝零个或多个调用、未提供工具和无效参数。Go 回归覆盖名称冲突、长名称、非 ASCII 名称、未提供名称与多调用拒绝。使用实际配置的 deepseek-flash 做独立只读决策接口探针，**0.89 s 返回并正确解码 telemetry.read**；该探针只验证决策接口，不据此宣称物理恢复成功。

### 5.4 最终运行镜像的四场景回归

`stable-business-matrix-8` 对最终 Gazebo 源摘要 `a96ed44fd836deb3199aa2599c3ae25d0934211fc815db36b06bdc7e39769cfb`（镜像 `sha256:acc2a53c32327298ab1dec072d78b507f60badd9f511db9445d19e579d00d411`）得到 **4/4 通过**。包含前球轮完整碰撞包络、里程计目标更新、取消先于 action 确认的处理，以及吸附抓放后收臂。

| 场景 | 基础检查 | 双物体业务与 oracle | 业务验收耗时 |
|---|---|---|---:|
| home | PASS | PASS | 47.980 s |
| tabletop | PASS | PASS | 48.154 s |
| home_task | PASS | PASS | 51.287 s |
| home_furnished | PASS | PASS | 53.521 s |

独立命名空间 `stable-estop-matrix-2` 对相同四场景的基础检查、取消与急停锁存也为 **4/4 通过**。锁存后的世界未继续执行业务。

这是固定工位和确定性编排的四场景回归，实际模型的结果见 5.2 和 5.3。它不覆盖任意物品、全屋房间语义或长期可靠性。相同方法今后可以做多随机种子、模型、故障注入和版本配对比较，但本报告不虚构未运行的样本。

### 5.5 用户入口的最终部署验证

恢复用户原来的 `http://127.0.0.1:8897` 控制台，运行同一最终镜像。`stable-workflows9` 通过标定、扫描、保存、激活、重载与取消，实测移动 **0.426246 m**，地图 `scan-b1bf93d6fd28`。随后 `stable-rpp-return5` 由实际 deepseek-flash 编排，完成返站和双物体抓放，**84.407 s**，最终平面误差 **4.218 mm**；任务、journal 和独立物理 oracle 全部通过。

同一次任务中 Ops 的 `telemetry.read` 已实际执行（executed=true）。它的恢复验证仍为 verified=false，不能称为物理恢复成功；该试验未注入需要执行恢复动作的故障。没有自动重放物理失败命令。

浏览器实测确认主控制台显示 Gazebo 实时 RGB、建图过程点云，以及可加载的 **43,569 个保存点、55 个关键帧**。用户界面的任务示例和输入提示与当前 Gazebo 工位能力一致。既有用户配置、历史数据和无关营销文档保留；实验目录不入库。

最终本地软件回归：Gazebo/安装/启动配置集合 **110 通过**、真实 ROS 导航节点 **12 通过**、前端界面 **8 通过**，Local Agent、actionloop、recoveryexec、autorecovery、文档 Go 测试通过。集合可能交叠，不相加为独立实验样本数。远程完整门禁以最终提交检查为准。

## 6. CI 的独立复现

提交 `802b348b` 的[Linux CI](https://github.com/SUSTechWLA/tangying-robot-agent-os/actions/runs/35678643417) 中，ROS 构建、安装矩阵和 MuJoCo 兼容检查通过，主测试作业有 12 项失败，涉及旧 MuJoCo 端到端超时、恢复、厨房物体识别和验证租约。这些不能计为 Gazebo 抓放失败，也不能忽略后发布稳定标签。

在独立 Linux ARM64、限制为 2 核的容器中，先复现了 RGB-D 首帧过期（约 2773 ms）。对相同场景、分辨率和相机，仅关闭 OSMesa 的阴影/反射，三帧耗时如下：

| 渲染配置 | 首帧 | 第 2 帧 | 第 3 帧 |
|---|---:|---:|---:|
| 含阴影/反射 | 2.4504 s | 0.5831 s | 0.6368 s |
| CPU 传感器配置 | 0.4670 s | 0.1316 s | 0.1693 s |

这是小样本诊断，不能给出泛化的性能倍数。修复仅对 `MUJOCO_GL=osmesa` 生效，保持几何、图像分辨率、深度、相机内外参和时间检查；新增测试比较两种配置的深度及标定。此后 Linux 针对性回归 **76 通过、1 失败**，剩余为蓝瓶真实抓取失败，未通过重试将其隐去。macOS 对应渲染与 Gazebo 回归 **27 通过**；Gazebo/ROS 纯合同集合 **107 通过、1 跳过**，后续扫描防护测试 **34 通过**；生产 Agent 抓放、取物、顺序与恢复端到端回归 **5 通过**。这些集合有重叠，不将计数相加为独立样本量。提交 `cb3c0fb6` 的 CI 随后为 **2315 通过、43 跳过、1 失败**。剩余项是蓝瓶被腕部遮成瓶盖/瓶身两个区域，两个颜色候选错误触发歧义拒绝。用已标定高度与水平尺寸约束关联片段，并保留分离/超高片段拒绝后，独立 Linux 2 核针对性回归 **6 通过**。并行运行 Gazebo 的额外诊断曾因 2 秒命令租约到期失败；未放宽运行时租约，隔离负载后执行上述针对性验收。随后提交 `aa17a147` 的[完整 CI](https://github.com/SUSTechWLA/tangying-robot-agent-os/actions/runs/35684503523) 全部通过。新增 Gazebo 改动的发布依据是 [PR #5 的最终检查](https://github.com/SUSTechWLA/tangying-robot-agent-os/pull/5/checks)，不能用该历史提交替代最终提交。另修复 push/PR 对同一 head 重复触发并互相取消、留下失败 release-gate 的调度问题：feature 分支使用 PR 触发，main 和版本 tag 保留 push 验证，并提供手动触发；门禁仍要求全部依赖成功。

## 7. 复现及发布边界

```bash
make setup
make build
make gazebo-build
PYTHONPATH=python .venv/bin/python scripts/evaluate_gazebo_scenes.py \
  --output artifacts/acceptance/gazebo-final-1 --business --require-full
# 另一个独立命名空间测试急停，不能在已锁存的环境继续抓放：
PYTHONPATH=python .venv/bin/python scripts/evaluate_gazebo_scenes.py \
  --output artifacts/acceptance/gazebo-estop-1 --estop
# 具备至少 0.6 m 后退空间的工位，可检验本地扫描保存/重载服务链：
PYTHONPATH=python .venv/bin/python scripts/evaluate_gazebo_workflows.py \
  --runtime 127.0.0.1:50051 --output artifacts/acceptance/gazebo-workflows-1
```

必须分别验收：确定性 Agent 工位编排、真实 LLM 编排、严格 GVF、地图保存重开、全屋语义路线和故障恢复。前者通过不推导后者通过。当前版本支持的是已标定彩色工位与受观测约束的导航/地图服务；尚不能宣称任意家庭任务全通过或无人值守可靠运行。

核心代码、自动化 runner、测试和本报告进入 Git；原始传感器帧、地图、模型响应、容器状态和临时诊断日志保留在忽略目录。稳定 `v0.7.0` 标签必须等待最终 CI 和声明的业务发布范围全部满足，不能为了让版本号出现而跳过门禁。
