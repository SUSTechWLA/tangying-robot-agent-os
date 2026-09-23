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

`stable-return-business2` 从扫描终点返回工位仍**失败**：底盘从里程计约 (-0.421,-0.025) m 移动到 (-0.070,-0.072) m，Nav2 报导航进展不足，任务按 NAV2_ACTION_ENDED 结束。该版本未通过“扫描后自动返站并抓放”。后续差速控制器回归单独记录，不覆盖这次失败。

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

最终本地软件回归：Gazebo/安装/启动配置集合 **110 通过**、真实 ROS 导航节点 **12 通过**、前端全量 **479 通过**，Local Agent、actionloop、recoveryexec、autorecovery、文档 Go 测试通过。集合可能交叠，不相加为独立实验样本数。远程完整门禁以最终提交检查为准。

### 5.6 标定身份与进程重启

真实进程重启首先发现保存地图没有恢复。诊断分出两个原因：一是 Gazebo CameraInfo 晚于工作流构造，启动默认 320×240 参数与装修场景实际 256×192 不同；二是 `calibration.save` 经 Protobuf Struct 往返后把整数变成浮点数，未规范化的序列化改变了内容哈希。`stable-workflows9` 的参数实际相同，但 get/run 的版本 b924f810 与 save 的 3ff284c0 不同；之前的同进程保存/重载测试不足以发现这一问题。

修复后，相机到达前拒绝不匹配地图，并在后续地图读取时重试；保存参数经过严格类型规范化，Gazebo 保留实际派生的 simulation 来源，对相同内容的保存不改变身份。机器人、几何、安全限值和相机/电机参数变化仍拒绝。旧错误哈希的地图不会通过改写身份自动放行，保留原始证据后重新实测建图。新增回归覆盖 RPC 往返、界面 manual 标签、重启一致性、变更速度拒绝和 CameraInfo 延迟，相关集合 **95 通过**。

## 6. CI 的独立复现

提交 `802b348b` 的[Linux CI](https://github.com/SUSTechWLA/tangying-robot-agent-os/actions/runs/35678643417) 中，ROS 构建、安装矩阵和 MuJoCo 兼容检查通过，主测试作业有 12 项失败，涉及旧 MuJoCo 端到端超时、恢复、厨房物体识别和验证租约。这些不能计为 Gazebo 抓放失败，也不能忽略后发布稳定标签。

在独立 Linux ARM64、限制为 2 核的容器中，先复现了 RGB-D 首帧过期（约 2773 ms）。对相同场景、分辨率和相机，仅关闭 OSMesa 的阴影/反射，三帧耗时如下：

| 渲染配置 | 首帧 | 第 2 帧 | 第 3 帧 |
|---|---:|---:|---:|
| 含阴影/反射 | 2.4504 s | 0.5831 s | 0.6368 s |
| CPU 传感器配置 | 0.4670 s | 0.1316 s | 0.1693 s |

这是小样本诊断，不能给出泛化的性能倍数。修复仅对 `MUJOCO_GL=osmesa` 生效，保持几何、图像分辨率、深度、相机内外参和时间检查；新增测试比较两种配置的深度及标定。此后 Linux 针对性回归 **76 通过、1 失败**，剩余为蓝瓶真实抓取失败，未通过重试将其隐去。macOS 对应渲染与 Gazebo 回归 **27 通过**；Gazebo/ROS 纯合同集合 **107 通过、1 跳过**，后续扫描防护测试 **34 通过**；生产 Agent 抓放、取物、顺序与恢复端到端回归 **5 通过**。这些集合有重叠，不将计数相加为独立样本量。提交 `cb3c0fb6` 的 CI 随后为 **2315 通过、43 跳过、1 失败**。剩余项是蓝瓶被腕部遮成瓶盖/瓶身两个区域，两个颜色候选错误触发歧义拒绝。用已标定高度与水平尺寸约束关联片段，并保留分离/超高片段拒绝后，独立 Linux 2 核针对性回归 **6 通过**。并行运行 Gazebo 的额外诊断曾因 2 秒命令租约到期失败；未放宽运行时租约，隔离负载后执行上述针对性验收。随后提交 `aa17a147` 的[完整 CI](https://github.com/SUSTechWLA/tangying-robot-agent-os/actions/runs/35684503523) 全部通过。新增 Gazebo 改动的发布依据是 [PR #5 的最终检查](https://github.com/SUSTechWLA/tangying-robot-agent-os/pull/5/checks)，不能用该历史提交替代最终提交。另修复 push/PR 对同一 head 重复触发并互相取消、留下失败 release-gate 的调度问题：feature 分支使用 PR 触发，main 和版本 tag 保留 push 验证，并提供手动触发；门禁仍要求全部依赖成功。

提交 `988a6146` 的[完整 CI](https://github.com/SUSTechWLA/tangying-robot-agent-os/actions/runs/35687507810) 为 Python **2330 通过、43 跳过、1 失败**；失败是文档检查将导航错误文本中的英文动词误判成 Make 目标，报告改为中文描述同一错误。ROS 作业另有一个 HTTP 测试夹具把发布时刻冻结在构造时，HTTP 认证/校验后超出 250 ms，新鲜度防护正确返回零速度而测试期待运动。夹具改为默认模拟持续发布，显式冻结时间仍覆盖过期/未来消息拒绝；未放宽生产时限。修复后真实 ROS Python 集合 **64 通过**，最终远程结果仍以最终提交为准。

### 6.1 Agent 合同面逐项检查（`scripts/gazebo_contract_check.py`）

验收矩阵回答「任务是否成功」，但不回答「Agent 依赖的每一道闸门是否真的按假设动作」。
一个从未触发的闸门也能让任务通过。`scripts/gazebo_contract_check.py` 针对的正是这一类
失败：每条断言都写成「删掉该行为就会失败」，共 **11 组 73 条**，规则如下。

RPC 面 7 个全覆盖；其中 `Cancel`（13）与 `EmergencyStop`（14）此前只有同名**技能**
被检查过。`EmergencyStop` 的技能走 `ExecuteSkill` 准入、RPC 不走准入，两条路径已分别断言。
13 个规范工具全部真实调用（此前 `verify_arrival`、`navigation.pre_position`、
`recover_to_safe_pose` 只出现在清单里、从未被调用）。规范工具集的断言改为与
`core/robotcontract/contract.go:111-116` 精确相等（13 项，含 `arm.move`），并同时检查
「没有多广告」。注意**运行时**广告 13 个工具，而**规划器**目录
（`skills/manipulation/plugin.go:45-58`）只有 12 条 manifest——`arm.move` 是可调能力，
但不会被规划成任务步骤。

无法在本环境执行的检查记为 observation 而不是 pass，因此「此处未验证」不会读成「已验证」。
本次 4 条记录为：持物拒绝归位（需先持物，会让整轮结束时手上仍有物体）、
在飞取消（本栈无巡检地图，导航立即以 `NAV2_ACTION_ENDED` 结束，取消到达时命令已终态，
拒绝才是正确答案；该半由 `test_service.py:105,230` 覆盖，48 通过）、
以及 `core/` 外残留的仿真器分支清单。

**Gazebo 结果：73/73。** 命令：

```bash
.venv/bin/python scripts/gazebo_contract_check.py \
  --runtime 127.0.0.1:50051 --estop --output artifacts/acceptance/gazebo-contract-1.json
```

### 6.2 与 MuJoCo 的同一份合同对照

检查器已可指定被测后端（`--expect-adapter`），否则把 `gazebo` 写死的检查无法区分
「合同成立」与「这仍然是 Gazebo 运行时」。同一份合同跑在 MuJoCo 上：

```bash
.venv/bin/python scripts/gazebo_contract_check.py \
  --runtime 127.0.0.1:50301 --expect-adapter mujoco --output artifacts/acceptance/mujoco-contract-1.json
```

66 项共有检查中 **47 项在两个后端都通过**——全部安全闸门、全部服务目录与标定恒等性、
观测链、`Cancel` 的拒绝半与流关闭、以及全部 Agent 层检查。**这是「Agent 对仿真器无感知」
目前最强的证据**；`Cancel` 在 MuJoCo 上还端到端通过（`accepted=True state='CANCELLED'`，
终态 `SKILL_EVENT_CANCELLED`），说明该 RPC 合同本身也不依赖后端。

MuJoCo 未通过的 11 项已逐项归因，其中 8 项是本次对比栈的场景配置（`--scene home_task`
且 `HOME_ASSET_PACK=` 为空，即没有摆设工位：`OBJECT_NOT_FOUND`、无关节位置、
`PRE_POSITION_UNAVAILABLE` 等），3 项是真实后端差异：MuJoCo 不广告 `arm.move`；
`navigation.pre_position` 的 `mutates_world` 两个后端声明不一致
（`gazebo_backend.py:103` 内联复制了本应唯一的
`contracts.MUTATES_WORLD_TOOLS`，而那份常量不含它）。后者后果有限但真实：
`core/closedloop/gate.go:62-64` 对不声明变更的工具直接返回 `Required:false`，
所以 Gazebo 要求 `pre_position` 后有确认观测、MuJoCo 不要求。**建议**
Gazebo 改为调用 `mutates_world(name)`，消除第二份清单。

**尚未做的对照**：没有用**同一任务序列**在 MuJoCo 上跑同样次数的真实 LLM 编排，
因此抓取失败**不能**归因为 Gazebo 迁移引入的回归。这是「agent 无感知」在业务层面的
判据，见 `docs/development/2026-09-19-gazebo-backend-status.md` 第 161 行，仍待补。

### 6.3 真实 LLM 五轮：抓取技能是稳定失败点

同一镜像（源摘要 `6cd4524acc6a7b7b…`）、同一模型 `deepseek-v4-flash`、
同一场景、同一 `--case sequence` 序列，共 5 次：

| 运行 | 结果 | 已下发步数 | 用时 | 失败的步 | 错误码 |
| --- | --- | ---: | ---: | --- | --- |
| 1 | `RECOVERABLE_FAILURE` | 7 | 42.0 s | `task01-pick` | `CAPABILITY_UNAVAILABLE` |
| 2 | `SUCCEEDED` | 20 | 85.8 s | — | — |
| 3 | `SUCCEEDED` | 20 | 55.2 s | — | — |
| 4 | `RECOVERABLE_FAILURE` | 17 | 27.7 s | `task02-pick` | `GRASP_TARGET_UNREACHABLE` |
| 5 | `RECOVERABLE_FAILURE` | 17 | 35.0 s | `task02-pick` | `OBJECT_NOT_VISIBLE` |

**2/5 成功，3/5 失败，失败步全部是 `manipulation.pick`。** run-4/run-5 中
`task01-pick` 成功而 `task02-pick` 失败，即**第二个物体 2/2 失败**；第一个物体 5 次里
只失败 1 次。run-5 的独立物理 oracle 给出精确现场：左指端 `y=-0.3691`、
瓶子 `y=-0.3729`，**相差 4 毫米**，`held` 为空——失败发生在**定位到目标之后、
建立吸附之前**，而不是「没走过去」。

必须同时说清边界：**n=5 不能给频率**（95% 区间约 15%–85%）；**根因未定位**
（未取到触发失败的那一帧感知数据与同位置的成功 `plan_grasp` 输出）；
**没有 MuJoCo 同序列对照，不能说是迁移引入的回归**。

另有一条与失败判别力直接相关的观察：`ANOMALY_UNVERIFIED_MUTATION` 在
**5 次运行里全部触发、5 次全部误报**——它点名的都是当时在飞、最终都 `CONFIRMED`
的步；真失败的步在异常发生时甚至还没下发。计时证据（容器 journal 中该命令的原始
事件流）：抓取实际耗时 **9.465 秒**，异常在 **3.35 秒**就发了。它是 critical 级、
带 `AutomaticRetryForbidden`，但按本次样本对「哪一步会失败」没有判别力；
一个每次都亮且 5/5 误报的 critical 告警会训练操作员忽略它。

**恢复链路本身是正确的**：判为 `UNKNOWN_OUTCOME` → `recovery_deferred`
（`deferredReason=PHYSICAL_ACTION_IN_FLIGHT`，不自动动手）→ 只执行**只读**动作
`telemetry.read` 对账 → **从不重放**物理动作，原命令自行到达终态后步被 `CONFIRMED`。
不重放不是一处可被删掉的检查，而是 `internal/autorecovery/supervisor.go:32-35` 的设计：
变更类动作被归类为 `bounded_write` 及以上，该包不具备运行它们的能力。

### 6.4 急停闩锁会持久化并阻断栈启动（设计如此）

`--estop` 组之后闩锁被写入运行时 journal 并在容器重启后依然生效，于是下一次
`sim-stack.sh start` 必然超时失败，且信息具有误导性：

```
sim-stack: services did not become ready within 180s
```

真实原因是就绪探针要求 `Ready: true`（`scripts/sim-stack.sh:587-598`），而带闩锁的
运行时如实上报 `Ready: false, Blockers: ['EMERGENCY_STOP_LATCHED']`。**这不是缺陷**：
`docs/install/xlerobot-setup.md:29` 明确写着急停由 Runtime journal 持久化、重启不解除；
复位是受审计的现场操作，**刻意不提供远程接口**（检查器断言
`emergency_stop.reset` 与 `safety.reset_estop` 都返回 `SERVICE_UNAVAILABLE`）。

复位走 `local_recovery.reset_local`：要求 `--operator-present`、交互式 TTY、
非空 operator 与理由，**写审计文件先于任何状态变更**，日志损坏时拒绝复位并要工程介入。
官方 CLI 是 `run_direct_edge --reset-stop`，但它会先构建 `XLeRobotDirectBackend`，
需要 `xlerobot_adapter` ROS 包，离开机器人镜像就不可用。
`scripts/reset_runtime_latch.py` 因此调用**同一个** `reset_local`，
只用一个占位 backend——因为该函数对后端复位用
`getattr(backend, "reset_stop", None)`，缺这个属性会被跳过而不是判失败，
而 `GazeboSkillBackend` 同样没有 `reset_stop`，路径一致。它不连接、不 arm、不重放。

**可改进处（非缺陷）**：就绪探针把「尚未就绪」与「已闩锁」都归为超时，
把 `Blockers` 透出到启动失败信息可省下一次 180 秒的误诊。

### 6.5 诊断开关：容器环境透传

运行时节点内部按自己的环境决定是否追踪（`TANGYING_TRACE_STEPS`，
`gazebo_runtime_node.py:202`），而从宿主启动的栈原先只写死 7 个容器环境变量，
**无法打开这些开关**。`scripts/gazebo_process.py` 现改为把调用者已导出的
`TANGYING_*` 变量一并透传，并打印实际透传了哪些，同时记录在 `compose.json` 里：

```
gazebo container environment passed through: TANGYING_TRACE_STEPS=1
```

一次性的追踪因此不再需要改源码，运行日志也自述了当时开了哪些诊断。
该文件不参与 `source_revision()`，镜像源摘要不变、不触发重建。

### 6.6 `core/` 之外仍有仿真器分支（尚未消除）

`core/`（任务图、闭环闸门、guard、证据）**零**仿真器符号，adapter 也由运行时自报，
所以「Agent 不需要知道背后是哪个仿真器」在决策层成立。但按字面要求的
「Go 侧零 gazebo 分支」**不成立**：`core/` 之外另有 6 个文件、3 处**控制流分支**
按仿真器名分派，其中前两处是实质问题：

| 位置 | 性质 |
| --- | --- |
| `cmd/local-agent/observer.go:43-46` | `if snapshot.Adapter == "gazebo"` 调整遥测采样率 |
| `edge/worker/observation.go:66` | `if Adapter == "mujoco" \|\| "robocasa"` |
| `cmd/edge-worker/main.go:341,414` | 默认适配器名与场景选择 |
| `edge/worker/worker.go:58,119-120` | 默认 `"mujoco"`；`robocasa` 时改写 `WorldID` |
| `tasks/service.go:158` | `NormalizeAdapter` 把空/`auto`/`sim` 一律归一为 `"mujoco"` |
| `edge/worker/telemetry.go:17` | 注释中出现 MuJoCo 场景名 |

更符合本项目既有风格的做法，是让运行时通过已有的 `RuntimeInfo`/观测能力
**声明自己的采集节奏与新鲜度窗口**（就像它已经自报 `adapter` 与 `catalog_revision`），
Agent 读声明而不是读名字。检查器把这份清单作为 observation 输出，不隐藏也不冒充通过。

### 6.7 本地开发探针

`scripts/gazebo_probe.py` 用 Agent 同一套线上合同（同 schema 版本、同目录修订、
同安全档）手工驱动一个运行时，用于「同一个 Agent 驱动哪个仿真器」的现场排查：
`info`、`observe`、`skill`、`services`、`call`。它此前有两个 bug 从未被执行到
（用了 proto 里不存在的 `ListServicesRequest` 与 `CallServiceRequest`），现已修正为
声明的 `GetRuntimeInfoRequest` 与 `ServiceRequest`。

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
# Agent 合同面逐项检查（§6.1）。--estop 会闩锁运行时，必须放在最后：
.venv/bin/python scripts/gazebo_contract_check.py \
  --runtime 127.0.0.1:50051 --estop --output artifacts/acceptance/gazebo-contract-1.json
# 闩锁后的现场复位（§6.4）。要求交互式终端，会先写审计文件：
.venv/bin/python scripts/reset_runtime_latch.py --journal <runtime>/commands.json \
  --operator NAME --reason "已检查工作区与持物"
# 手工驱动（§6.7）：
.venv/bin/python scripts/gazebo_probe.py --runtime 127.0.0.1:50051 info
```

两个前提必须记住。其一，`--estop` 组的闩锁是**持久**的，之后 `sim-stack.sh start`
会以「服务未在 180 s 内就绪」超时失败，而真实原因是运行时如实上报
`Ready: false, Blockers: ['EMERGENCY_STOP_LATCHED']`；这不是缺陷，见 §6.4。
其二，合同检查须用仓库 `.venv` 运行（宿主解释器的 grpcio 版本可能低于生成代码要求），
并在被测运行时由**另一个** checkout（例如 worktree）构建时设
`CONTRACT_CHECK_SOURCE_ROOT` 指向那棵树，否则 §6.6 的源码扫描会审错树。

必须分别验收：确定性 Agent 工位编排、真实 LLM 编排、严格 GVF、地图保存重开、全屋语义路线和故障恢复。前者通过不推导后者通过。当前版本支持的是已标定彩色工位与受观测约束的导航/地图服务；尚不能宣称任意家庭任务全通过或无人值守可靠运行。

核心代码、自动化 runner、测试和本报告进入 Git；原始传感器帧、地图、模型响应、容器状态和临时诊断日志保留在忽略目录。稳定 `v0.7.0` 标签必须等待最终 CI 和声明的业务发布范围全部满足，不能为了让版本号出现而跳过门禁。
