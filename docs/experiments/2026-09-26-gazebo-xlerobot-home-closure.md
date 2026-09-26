# Gazebo 同源 XLeRobot 家庭闭环记录

关联规范：[升级规范](../development/2026-09-26-gazebo-xlerobot-home-spec.md)。本记录追加实际结果，旧版彩色启动工位验收不构成本次全屋验收。

## 实施变化

1. `export_gazebo_home.py` 从已配置的 XLeRobot 家庭模型导出 CAD、关节、惯量、碰撞、家具、陶瓷杯/收纳盘、相机与资源摘要。模型包含 18 个原型活动连杆、275 个环境/机器人几何体；底盘的两个平移自由度及一个旋转自由度与参考原型一致。
2. 家庭常量、RGB-D 几何识别和语义服务迁至 gateway 的共享模块；MuJoCo 保留兼容导入。Gazebo 运行时不导入 MuJoCo。
3. Gazebo 使用导出清单建立 Runtime 与 ROS TF 的同一标定，相机分别位于底盘局部 `(0.30, 0, 0.16)` 和 `(-0.025, 0, 1.15)`。标定与世界/网格版本绑定，重新加载地图必须匹配版本。
4. 自然语言验收脚本可选择 Runtime 注册键，任务内容与判据共用。家庭对象目录只提供稳定 ID、类别和工作区，目标位姿仍来自实测 RGB-D。

## 已观察到的问题与修复

| 问题 | 可追溯证据 | 修复 |
|---|---|---|
| 相对输出路径产生无效数据库 URI | `gazebo-xlerobot-start-2.log`、Agent 日志 | 启动脚本规范化目录为绝对路径 |
| SDF `world` 模型名称与保留帧冲突，服务器退出 | 第 3 次启动日志 | 世界几何模型采用独立名称 |
| CAD OBJ 无法线，DART 拒绝碰撞网格 | 第 4 次启动日志 `CustomMeshShape` | 导出编译后的顶点、法线、面及 UV 索引 |
| 原 Gazebo 机器人/场景不是 MuJoCo 家庭原型 | 旧 SDF 与 scene composer | 同源模型导出，移除独立工位的家庭替代关系 |
| 相机安装值重复维护，旧 head 高度错误 | 旧 Runtime/launch/标定分别维护数值 | 统一读生成清单 |
| 关节控制主题与 bridge 不一致 | 导出控制器和桥接配置 | 使用 `/joint/<motor>/cmd_pos` |
| 自由底盘发生横向漂移，扫描进入沙发包络 | 地图试跑 1、2 | 与参考模型相同的平面关节约束，速度超时停车 |
| 用矩形角部半径保护全向圆形底盘，误拒转动 | 底盘几何与扫描守卫审查 | 使用已配置圆形底盘的扫掠胶囊，Nav2 使用外接圆多边形 |
| 原通用伺服 PID 用于 CAD 小惯量夹爪，反馈无法收敛 | 地图试跑 3 关节到位检查 | 按 CAD 惯量调整夹爪与手臂阻尼，保留反馈误差检查 |
| 家庭任务/地图语义缺失及标定 ID 不一致 | 旧 Gazebo bindings、Runtime 观察 | 注册同源房间路线、共享语义服务与一致的标定版本 |

## 执行环境与证据

macOS ARM 主机、Docker Linux ARM64；ROS 2 Jazzy、Gazebo Harmonic。GPU、真实 Orin X 与实机未参与。

```bash
scripts/sim-stack.sh start --engine gazebo --scene home_furnished \
  --perception rgbd --sim-port 50161 --agent-port 8897 \
  --artifacts-dir artifacts/sim-stack/gazebo-xlerobot-home

.venv/bin/python scripts/build_sim_map.py --base-url http://127.0.0.1:8897 \
  --output artifacts/gazebo-xlerobot-map-trial-4 --timeout 1800
```

真实失败记录保留于各次 `artifacts/gazebo-xlerobot-map-trial-*`、启动日志及运行时日志。下面按实际执行顺序追加结果；中止、完成扫描和自然语言任务通过分别记录。

## 验收判定

**2026-09-27，第二十九轮完整自然语言任务闭环通过。** 第四十五次启动中完成第十三轮实测建图并保存/激活，随后在同一物理 episode 连续通过巡检、厨房观察往返、杯子抓放及返回：4 个 LLM 规划任务、34 个确认步骤、7 段实际导航、76 张摘要验证原图。物理重试 0、任务内世界重置 0。详细任务身份和判据见文末第二十九轮；原始数据摘要见[验收清单](2026-09-27-gazebo-xlerobot-home-acceptance.json)。

本次是同源家庭原型的一轮实测通过，不提供成功率统计或实机认证。模型可启动、地图可显示或单个工具 ACK 均不足以代替验收。完整软件门禁结果在文末单独记录。

## 2026-09-27 追加：采集、地图准入与复测

| 轮次 | 实际结果 | 原因与处理 |
|---|---|---|
| 扫描 4 | 未完成 | 家庭转动/移动增加关键帧，旧错误文案与帧预算不一致；调整有界预算并保留限制 |
| 扫描 5 | 9 帧后停止 | IMU 短时延迟触发运动守卫；先停车等待新鲜数据，持续故障仍失败 |
| 扫描 6 | 全部路线扫描完成，地图未通过任务准入 | 242 帧、163 次配准、0 次回环、23.4755 m、155072 点；地图漂移导致起点不满足圆形底盘净空 |
| 自然语言试跑 1 | 巡检任务 `RECOVERABLE_FAILURE` | 第六轮地图的准入拒绝运动，未用重试/位姿写入绕过 |
| 扫描 7 | 修复后复测途中主动停止 | 核查实际 CameraInfo 又发现半像素主点差；保留进度与日志，不计为完成 |
| 扫描 8 | 82 帧后因 RGB-D 过期停止 | 全量测试与软件渲染并行期间发生；停止运动，不放宽新鲜度限制 |
| 扫描 9 | 全屋扫描完成并保存、激活 | 239 帧、225 次配准、7 次回环、21.8337 m、79507 点；不与全量测试并行 |
| 自然语言试跑 2 | 巡检任务 `RECOVERABLE_FAILURE` | 导航声明 600 s，而共享 Runtime 默认最大租约 60 s，运动前 `LEASE_TOO_LONG` 拒绝 |

第六轮地图：`scan-22d283c58eb9`，manifest SHA-256 `7ba23ac6ae3e3e736d06214a2a706166b8bff3db9ea300227d536ec7c71003a3`。捕获时基座位姿原被最新里程计覆盖，造成相机变换与底盘不同采集时刻。修复后保留 capture pose，ROS 驱动只接受由前后测量里程计夹住的传感器时刻，线性插值平移、SLERP 插值旋转，拒绝外推和超过 100 ms 的测量间隔。

实际 head CameraInfo 的 `fx=fy=171.37776081, cx=160, cy=120`；旧 FOV 推导主点为 `159.5,119.5`。当前保留原始 K，而 FOV 推导仅用于明确的纯函数测试夹具。ROS TF、Runtime profile 与标定使用同一光学帧名；原始采集来源、光学帧和标定版本写入关键帧溯源。

理想仿真里程计由 Gazebo 驱动声明 `sigma=(0.001 m, 0.001 m, 0.001 rad)`，经注入的 SLAM factory 保留到新扫描和后续分段；通用默认仍为 `(0.025 m, 0.025 m, 0.015 rad)`。此值是驱动测量不确定度声明，不能复制作为实机标定结果，也不替代深度配准质量检查。

导航准入先在已保存的实测网格上用 0.32 m 足迹求路径，实际执行交给 RTAB-Map/Nav2。观察中的 `map_route.waypoint_count` 是准入路径点数；`executionProvider=rtabmap_nav2` 说明实际路径由导航栈计算，不能把该计数解释为实际执行分段数。

抓放控制修正高位折叠姿态的不可达上抬：只在当前末端低于观测物体安全上方时升高，保持关节限制和 IK 可达性检查。最终放置证据必须包含至少三个不同物理序列的稳定样本、实际持续时间、盘内范围、直立和释放状态；来源明确为 `gazebo_physics`，末端模式 `sim_suction`。该修复仍需下述物理任务验收。

已完成回归：RobotWorkflow 全部 59 项通过；捕获配对/溯源相关 58 项通过；精确 CameraInfo、时刻插值、捕获位姿保持及 backend/manipulation 定向 51 项通过。Docker 第 10 次构建完成，随后启动第 15 次运行。

第九轮地图 `scan-293f92c68a3c`，manifest SHA-256 `586a80461de03c58640e22b59130945efe3575fe532356b7283490dd3a90a921`，标定 SHA-256 `68c581e7c5e70c1b1909e66ee3e68560b2d5c51eee276f684ed22fe9be80f58c`。优化位姿相对测量里程计的最大平移修正 0.04254 m，地图锚点 `[-0.03553664,-0.01162484,-0.00379383]`。初始帧来源为 `gazebo-home_furnished/base-rgbd`、光学帧 `base_camera_optical_frame`。第九轮从上一轮停止点开始，不通过任务/脚本复位位置或对象。

第二轮巡检 `task-ff5e7f2d5297b670b37c9b8e` 在 pre_position 阶段因 `LEASE_TOO_LONG` 被拒。协议中的 command lease 是本次动作的绝对执行预算，既有 Go dispatch 最大 10 min。共享 Runtime 新增显式 `max_lease_ms` 参数（默认仍 60 s，硬上限 600 s）；已配置 Gazebo 家庭驱动显式选择 600 s，历史夹具仍使用 60 s。超时/租约失效仍锁存停车。gRPC 流断连由同一 watchdog 检测，取消当前命令直至 handler 退出；断连请求不得进入动作 handler。Gazebo 的取消 token 在拥有执行权、进入 Runtime 准入前重置，动作函数不得清除执行中抵达的取消。该变更 65 项 service/safety/backend 回归通过，包括长租约断连停车及未连接时拒绝动作。

全量 Go 测试已通过；Python 全量测试在 3% 时主动中断，为物理仿真释放资源，不能记录为全量通过。闭环完成后再单独运行最终门禁。

第三轮巡检 `task-6c0a237b5ceffa65418c6520` 在 pre_position 阶段遇到底盘 RGB-D `SENSOR_STALE`，尚未运动。随后连续读取的两路相机延迟通常约 0.2–0.6 s，说明采样会恢复，单次准入读到陈旧帧不能通过重标时间修复。导航准入现可在原 command deadline 内、最多 2 s 等待新的原始采样；1 s 新鲜度门禁仍保持，持续陈旧或取消继续拒绝。42 项 backend/service 回归通过，包括等待新测量才验证到位、等待中取消及不清除停止 token。第 12 次镜像构建用于第四轮任务复测。

扫描 8 与全量测试并行时出现采样过期；这是时间相关的观察，不能仅凭该记录证明测试是唯一原因。后续运行将仿真验收与全量测试分开执行，保留故障拒绝。

第四轮巡检 `task-23931d75b8e1a26cf0e6ad47` 已通过 pre_position 并实际导航到卧室附近，最终 `NAV2_ACTION_ENDED`。Nav2 日志报告无法取得运动进展（原始文案保留在日志中）；失败时传感器、地图、里程计、视觉词与 TF 均就绪。独立读取 `/local_costmap/costmap_raw` 后，在目标 X 偏移 `0.12,0.09,0.06,0.03,0.00 m` 的 0.305 m 圆形轮廓上，最大代价分别为 `0,0,164,186,239`，各位置的 lethal/unknown 格均为 0。原软代价权重 `0.1` 在一步中产生 16.4 的评分跳变，超过厘米级目标进展。

家庭控制器现将 `ObstacleFootprint.scale` 设为正数 `0.0001`，软代价最大 0.0253；最终目标评分仍为每米 6，不修改定位/到位容差、轮廓、碰撞例外、未知空间准入或全路径碰撞检查。正权重确保 DWB 继续调用轮廓检查并拒绝致命碰撞与未知区域。[Nav2 上游实现](https://raw.githubusercontent.com/ros-navigation/navigation2/jazzy/nav2_dwb_controller/dwb_critics/src/obstacle_footprint.cpp)区分轨迹拒绝与合法软代价；本次只校准合法代价偏好。该设置需后续仿真任务复测，不能直接用于实机认证。

同时修复两项 Agent 记录问题：历史异常任务的 finding 现在绑定其真实 task ID，保留系统失败总数，不再回退关联最新的正在运行任务；动作轮次的 `durationMs` 原由 `time.Duration` 编成纳秒，现在显式序列化为毫秒。历史失败关联与单位回归在 `agentruntime`、`internal/actionloop` 通过。第 13 次 Docker 构建和 Go 构建完成，重启第 18 次运行开始第五轮任务验收。各轮代码修复后的服务/仿真重启均保留，验收脚本不在任务中复位或重发不确定物理动作。

第五轮巡检在走廊段因 `NAVIGATION_OBSERVATION_LOST` 停止；失败观察的 blocker 为 `HEAD_RGBD_STALE`，head age 1237 ms、base 895 ms、odom/TF 366 ms、RTAB-Map 2178 ms。没有放宽 1000 ms 相机门禁。导航节点新增专用传感器 callback group，避免相机/里程计/SLAM 状态接收被地图转换和目标更新串行阻塞。物理任务验收不并行运行构建和测试。

运维观察器新增只读 `ExecutionActive(taskID)` 来源，由本地 App 的实际执行权表提供；此来源不发命令、不依据持久化 EXECUTING 状态猜测存活。明确存在当前进程的执行权时，STARTED 是等待中的动作，不能诊断成丢失结果；回调缺失、重启后或执行权消失时，未终结的 STARTED 仍报告未知结果并禁止自动重试。毫秒序列化与反序列化已配对回归。`agentruntime`、`internal/localapp`、`cmd/local-agent`、`internal/actionloop` 回归通过，第 14 次镜像与第 3 次更新后 Go 构建用于第六轮物理任务。

### 第六轮：速度范围与终点采样精度不匹配

- 第十九次启动，第六轮任务 `task-832f6bbd28ca396c30bc7f07`，保存地图仍是 `scan-293f92c68a3c`；原始证据在 `artifacts/gazebo-xlerobot-tasks-trial-6/`。
- 预定位、观察通过，卧室导航以 `NAV2_ACTION_ENDED / 无法取得运动进展` 失败；失败时 base/head 分别 174/353 ms，odom/TF 32 ms，RTAB-Map 1467 ms，无 readiness blocker。这次不能归因于过期图像。
- 终止后地图坐标位置约 `[-2.046805, 3.374837]`。提高 holonomic 速度范围后，9 个线速度采样对应 0.05 m/s 间隔，0.5 s 末段距离目标容易在厘米尺度偏好零速度；角速度 0.5 rad/s 和 17 个采样也不再满足原先按 0.2 rad/s 推导的精度说明。
- 修复：家庭配置的 ContinuousGoal 平移评分前视改为一个控制周期 0.1 s；完整 1.5 s 轨迹仍接受 footprint 碰撞检查。角速度采样 33 个，终点半步误差上限 0.0234375 rad；进展检查采用 PoseProgressChecker，同时认可实际角度进展（0.03 rad），15 s 无进展时限保持。到位容差保持 5 mm/0.03 rad，Runtime 独立到位校验保持。
- 依据：[Jazzy StandardTrajectoryGenerator](https://raw.githubusercontent.com/ros-navigation/navigation2/jazzy/nav2_dwb_controller/dwb_plugins/src/standard_traj_generator.cpp)、[Jazzy PoseProgressChecker](https://raw.githubusercontent.com/ros-navigation/navigation2/jazzy/nav2_controller/plugins/pose_progress_checker.cpp)。第十五次镜像构建后再作实际验收，此处不是成功声明。

### 第七轮：卧室到位通过，持续建图负载阻断后续导航

- 第二十次启动，第七轮卧室 navigation.navigate 与 verify_arrival 均通过；见 `artifacts/gazebo-xlerobot-tasks-trial-7/`。巡检在卫生间段安全停止，不能把卧室通过当成全屋任务通过。
- 原因回执 `NAVIGATION_OBSERVATION_LOST / RTABMAP_PROCESSING_STALE`：RTAB-Map 2592 ms，base/head 118/303 ms，odom/TF 128 ms。没有并行编译或测试；传感器调度修复不能解决 SLAM 本身处理延迟。
- 实际持久化 RTAB-Map 数据库约 709 MB / 4550 节点 / 210071 词。此前多个资源修订的数据库隔离保留；当前资源重启后仍持续以 mapping 模式增加图节点。家庭任务使用保存地图后应切换 localization 模式，避免任务阶段持续扩图。
- 第二十一次启动通过 `TANGYING_NAVIGATION_MODE=localization`，保持原数据库、保存地图与资源版本；就绪状态 `LOCALIZED`，RTAB-Map 485 ms。此启动产生新的物理场景，已结束的失败任务不作原命令重放。第八轮使用新任务 ID 验收。

### 第八轮：巡检物理成功，验收识别出命令证据未固定

- 第二十一次启动，任务 `task-fb2e4fd807a34dbe716a19c5` 的卧室、卫生间、返回客厅及三次到位验证全部通过，任务状态 `SUCCEEDED`；11 份捕获/22 张 RGB-D 图像已下载并验证 SHA-256，三段 navigation.navigate 均有保存地图和命令 ID 的 map_route。
- 验收仍失败：Gazebo Backend 的 Result 没有 observation_id，Agent 因而使用 `post_tool_observation` 路径。该路径能验证新鲜后置状态，但不能通过本次严格要求的命令固定图像验收。没有放宽验收断言，厨房和搬杯子尚未执行。
- 修复公共 Runtime：支持适配器提供 wait_command_capture，同步等待动作完成后的原始捕获，然后验证 observation 和图像契约，将 observation_id 与完整 evidence_observation 固定在终止事件中，最后写入原 idempotency journal。再次请求原命令回放原始事件字节，不能读取新图像替换证据。
- Gazebo 对所有成功技能接入此能力（包括只读观察/验证）；超时、冻结图像或捕获时间未超过动作完成时刻均返回 POSTCONDITION_OBSERVATION_TIMEOUT。没有重新打时间戳或 Agent 仿真器特判。
- 回归验证 `robot/gateway/tests/test_service.py` 与 `test_gazebo_backend.py`：45 passed；新增测试覆盖跨进程回放字节完全相同、无重复执行/捕获、过期和缺失捕获拒绝成功。原日志 `artifacts/gazebo-xlerobot-command-evidence-tests.log`，镜像第十六次构建。

### 第九轮：严格巡检验收通过，厨房短时图像间断

- 第二十二次启动，巡检 `task-60e070371a8010bd4eb08022`：SUCCEEDED，严格 command_observation / 图像摘要 / 三段保存地图导航验收通过。厨房任务 `task-dd9b893177c91e688e07897e` 在导航段失败，未开始抓放。
- 原因：HEAD_RGBD_STALE 1133 ms，base 783 ms，odom/TF 339 ms，RTAB-Map 1400 ms。保持原 1000 ms 新鲜度规则。软件渲染调度抖动需要停车后有界等待，而不是继续使用过期观测或修改捕获时间。
- 新增实际速度门：本家庭 Nav2 只发布内部 controller_cmd_vel，navigation_http 的 20 Hz 发布器是导航阶段唯一外发速度路径；每次发布检查当前完整 readiness、活动目标和 250 ms 控制消息新鲜度，过期立即零速度。独立扫描阶段不由此发布器覆盖速度；取消/完成主动发布零速度，底盘原 500 ms 看门狗保留。
- GoalRegistry 只有显式具备实际速度门的驱动才能开启最多 2 s 的 observation hold。仅允许数据新鲜度类 blocker；低视觉质量、缺地图、无动作服务等立即取消。暂停时 mapReady=false、velocityValid=false、速度为零；恢复清空暂停中缓存的速度，必须等新消息，同一 Nav2 action/command 继续，不重新派发。持续失效或客户端租约到期仍取消。成功后的 map_route 保留暂停原因/时间记录；服务重启仍终止活动命令，不恢复内存中的动作。
- 68 项网关/HTTP/工作流回归通过；真实 ROS 节点发布路径首轮 13 passed（后续增补 idle 不干扰扫描回归）。镜像第十七/十八次构建。
- 厨房验收语义纠正：原脚本要求每个单程请求都至少两段导航，并在返程检查厨房库存；现在去厨房请求要求一段并检查库存，返回客厅请求要求一段。仍要求命令固定图像、导航/到位工具和完整实测地图。
- 清理两个已确认测试目录删除、父进程为 1 的孤立测试 Agent/仿真进程对，仅按准确 PID 和匹配端口发送 SIGTERM；记录 `artifacts/gazebo-xlerobot-orphan-test-cleanup.json`。没有批量终止其他项目进程。

### 第十轮：新速度门发布话题接线错误

- 第二十三次启动，第十轮巡检 `task-ae6fad17a161a90d593dda74`：预定位通过、卧室导航 无法取得运动进展，机器人仍在初始客厅位置，没有虚假到位。
- ROS graph 实测内部 `/tangying/navigation/controller_cmd_vel` 有 controller_server 发布、navigation_http 订阅；新外发 `/tangying/navigation/cmd_vel` 却没有订阅者。实际 Runtime 接收 `/navigation/cmd_vel`，再通过安全门转发 `/cmd_vel` 给 ROS-Gazebo bridge。单节点发布测试不能覆盖跨节点话题接线。
- 修正：launch 显式保存原 driver_cmd_vel_topic，并将它独立传给导航节点；内外两个话题分开配置，不能硬编码猜测驱动入口。实际链路为 Nav2 internal → navigation_http 新鲜度门 → `/navigation/cmd_vel` → Runtime 运动所有权/传感器/急停门 → `/cmd_vel` → Gazebo velocity plugin。启动后以 ROS graph 核对订阅者。
- 第十八次镜像真实节点最终 14 passed；第十九次镜像修正接线。原失败和 source/target 话题诊断保留，下一轮仍须重新物理验收。

### 第十一轮：模型发明未声明参数，在 Runtime 入场拒绝

- 第二十四次启动核对两个速度话题均有一个发布者、一个订阅者。任务 `task-bf0025de82b558a23e4e1d83` 的模型计划给 navigation.navigate 添加 `constraints: {avoidHumans:true,keepUpright:true}`，该工具实际只接受 goalPose。Runtime Safety 返回 TOOL_PARAMETERS_INVALID，尚未进入动作处理器；此轮未证明新速度门的物理导航。
- 修复规划器通用 SkillManifest 可声明 AllowedParameters（nil 兼容尚未声明的扩展；明确空数组表示无参数），标准工具目录声明与 Runtime 对齐的必填/可选参数。LLM 提示提供清单，计划校验拒绝未声明字段，保持意图约束为任务要求，不偷偷传给不支持它的驱动。原先仅检查必填字段，导致非法计划到实际派发才被拒绝。
- 规划器、标准技能、核心技能目录 Go 回归通过；新增“导航 constraints 在派发前拒绝，标准语义 goalPose 接受”测试。Agent 第四次构建。已有候选拒绝/回退机制仍会记录 rejection 与实际计划来源。

### 第十二轮：返程全局规划拒绝与导航地图历史隔离

- 第二十五次启动，模型计划巡检 `task-f56f9c1010178f3e2c6cc881` 完成卧室、卫生间导航/验证/观察，返程在门口以 NAV2_ACTION_ENDED 失败。真实速度门已能驱动底盘，传感器就绪。
- Navfn 原日志：从 `(-0.57,3.20)` 到 `(0.53,-1.12)` 在 0.01 m tolerance 内无法产生路径；诊断 mapPose 约 `[-0.6188896,3.1417808]`，而客厅 odom 目标仍为 `[0,-1.25]`。说明当前 map→odom 存在约半米校正，不能将多次校准/扫描/资源调试过程中持续扩图的原生地图当成干净的任务导航验收地图。此处为历史图漂移/混合的诊断，尚不能断言是哪个具体旧帧或错误闭环导致。
- 机器人停车后完整保留原生 RTAB-Map 数据库 `rtabmap.before-clean-commissioning-20260927.db`：710135808 bytes，4550 nodes，221606 words，SHA-256 `50b1bcc2f069148cf4d9b3c7f61fb072f9f0637097f9feb9d8dd6e1fec357ee2`，SQLite quick_check=ok。保存地图 scan-293f92c68a3c 未删除。归档记录 `artifacts/gazebo-xlerobot-native-map-archive.json`。
- 第二十六次启动（mapping）从空原生数据库开始，通过同一 mapping.start / RGB-D / odometry / 障碍守卫执行第十轮全屋扫描，输出 `artifacts/gazebo-xlerobot-map-trial-10/`。不注入地图、不传送机器人、不重放未知动作。完成后在独立任务阶段启用 localization。

### 第十轮扫描完成：从同一物理采集重建两套地图

- scan-c236dd4ad963：233 帧、221 次配准、2 次视觉闭环、77085 points、23.4752886 m；完整 10 段路线结束返回客厅，保存并激活。
- 保存地图修订 `9f87114c2e4f66cc26fc79ec061cb13e38c8ab85c35f8748166a8f3bbf0d55f9`；相机标定修订保持 `68c581e7c5e70c1b1909e66ee3e68560b2d5c51eee276f684ed22fe9be80f58c`。mapFromWorld 为 `[-0.0278400601,0.0143996630,-0.0024640850]`，平移约 3.13 cm。所有原始采样、来源标识、内参、配准和轨迹在 map-trial-10 与对应不可变 workflow map 下保留。
- 同次扫描原生 RTAB-Map 数据库诊断约 113 MB、748 nodes、539 words（诊断时进程尚在运行，最终值以停车后数据库为准）；末端 mapPose 约 `[0.0085277,-1.2137372]`。这些值仅用于对比，不替代实际规划/到位验收。
- 第二十七次启动为 localization，新的物理任务阶段；保留原生数据库和此次保存地图，待第十三轮完整自然语言验收。

### 第十三轮：巡检通过、厨房到位通过、真实杯子漏检

- 第二十七次启动，新地图巡检 task-649b52ab70636a34c8de55b3：严格验收通过。厨房任务 task-fcf3e2755a53d0648552d6ad 的导航、到位、观察均成功，但厨房库存验收未通过：只有 kitchen-tray 与 ceramic-vase，没有 ceramic-mug。没有进入抓放。
- 实际 RGB 图像清楚可见杯子；诊断读取的是同一静止厨房场景的新 raw RGB-D，保存 `artifacts/gazebo-xlerobot-kitchen-raw-13.npz` / `kitchen-components-13.json`。不是读取物体真值用于检测。
- 支撑面测量 0.73 m。杯子的上部点云 456 pixels，span≈[0.08266,0.08281,0.01125] m；花瓶上部分段 558 pixels，span≈[0.04759,0.06076,0.10142] m，也满足原来的宽松杯体条件；候选大小比触发 0.6 歧义规则，结果杯子被拒绝。
- 修复共享 RGB-D 检测器：从本帧已按完整高度/形状识别的花瓶组件取得像素 mask，将这些像素从杯体分段中排除；不使用先验花瓶位置，不改变同形多杯的歧义拒绝。杯子中心仍由深度测量得到。
- 原始实时诊断捕获及摘要作为 `robot/gateway/tests/fixtures/gazebo-xlerobot-kitchen-rgbd.npz/json` 保留，明确是离线感知回归材料，不是命令完成证明。该捕获回放与原 MuJoCo 感知测试：28 passed；镜像第二十次构建。任务与两套地图均保留，下一轮重新 Gazebo 物理链路验收。

### 第十四轮：厨房库存通过，返程目标占位符被 Runtime 拒绝

- 第二十八次启动，巡检 task-a1c10023659efe365e634d5e 严格通过；厨房 task-775519ad60a00da707dbd2c9 导航、到位、观察及杯子/收纳盘库存验收通过，证明共享 RGB-D 检测修复已进入实际 Gazebo 链路。
- 返程 task-5c110db18258d6e296d13f3b 为 RECOVERABLE_FAILURE / TOOL_PARAMETERS_INVALID，尚未运动。模型将 navigation.navigate 与 verify_arrival 的 goalPose 写成实体占位符 @destination；路线意图没有 destination 实体，绑定成空字符串。
- 修复规划校验：这两类导航目标必须是非空语义航点名或显式数值位姿，拒绝 @ 实体占位符；提示区分语义房间航点与物体实体 ID。Go 三包回归通过。
- 验收补强：每份 map_route 必须属于当前 commandId，捕获 base_pose 必须实际到达该目标，目标与版本绑定的 semantic_navigation 匹配，实际房间顺序必须满足请求。重复到同一房间不能依靠导航次数通过巡检。失败记录仍在 artifacts/gazebo-xlerobot-tasks-trial-14/；抓放尚未执行。

### 第十五轮：目标修复通过，走廊低纹理阻断返程

- 第二十九次启动：巡检 task-fc3e3a438d63d712addde788 和厨房 task-780feffa21bdd99188a63cb9 通过严格房间序列/命令捕获/库存验收。返程 task-d874f20f92f1c4e8dfd38940 正确使用 living_room 语义航点，但以 NAVIGATION_OBSERVATION_LOST / VISUAL_QUALITY_LOW 安全停止；未抓放。
- 失败冻结诊断：base/head 135/277 ms、odom/TF 31 ms、RTAB-Map 135 ms，当前词数 17，字典 75940。停车后走廊底盘相机原始图像单独采样保留 artifacts/gazebo-xlerobot-corridor-base-15.png。OpenCV ORB 同图比较：原 scale=2/3 levels/FAST=20 得 5 keypoints，1.2/8 levels/FAST=7 得 50。keypoints 与 RTAB-Map 最终 words 不等价，仍须实际验收。
- 家庭 RTAB-Map 配置改为更密的图像金字塔和 FAST=7，最大特征数仍 250，词数 >=20、定位协方差和观测新鲜度门槛不变。参数依据 [RTAB-Map Parameters.h](https://raw.githubusercontent.com/introlab/rtabmap/master/corelib/include/rtabmap/core/Parameters.h)。不改场景纹理/相机标定，不使用物体真值。
- 诊断同时发现镜像 pip 自动安装 NumPy 2 与 ROS OpenCV/cv_bridge 的 NumPy 1 ABI 不兼容。拟固定 numpy=1.26.4 / scipy=1.16.3，并验证实际导入；SciPy 1.18.1 要求 NumPy>=2，因此不能只固定 NumPy。第 21 次构建的冲突日志保留，第 22 次使用兼容版本；此修复避免后续 RGB-D 驱动调用 ROS 图像库时崩溃。

### 第十六轮：首次进入抓取，腕部被误识别为杯子

- 第三十次启动 / 镜像 22。真实 ROS 节点 14 passed；实际 numpy/scipy/OpenCV/cv_bridge 导入通过，版本 1.26.4/1.16.3/4.6.0。整仓路径类 launch 测试不能在裁剪后的容器根目录执行；旧夹具版本及缺少 /deploy 目录的诊断日志保留，宿主对应测试已修正并通过。
- 自然语言搬杯 task-7e02a007371408e00883c58c 使用 LLM 计划，厨房导航、到位、观察、解析与 plan_grasp 均通过；pick-cup 在接近后 GRASP_PLAN_STALE 停止，无吸附。物理杯子仍 [2.275,3.415,0.7899992]；左腕到达 [2.2709,3.4093,0.9470]。
- 原始接近 RGB-D / 编码器 / 物理诊断保留 artifacts/gazebo-xlerobot-pick-approach-16.*、pick-approach-joints-16.json、suction-state-16.log。腕部误检杯子位置 [2.24445,3.30783,0.95692]，不是杯子真实运动；共享感知已有 robot_mask 参数，但 Gazebo 调用路径尚未传入。
- 新增通用 CAD 表面过滤：仅加载 SDF 选定机器人视觉几何、当前捕获的编码器和底盘位姿；工作体积内每个像素的测量深度端点必须距实际机器人 CAD 表面 <=4 mm 才排除。AABB 只用于减少计算，不能作为掩码判据。无环境几何、物体真值或分割 ID 输入；缺少/不同步编码器拒绝输出。底盘实测位姿独立绑定，支持旋转和平移关节。
- 同图诊断过滤后测得杯子 [2.27007,3.40838,0.79104]；567 个工作区域机器人像素被排除，首轮过滤约 0.163 s（完整图过滤约 2.03 s 因此不用于任务路径）。杯子仍由当前深度测量，不从规划缓存填充。收纳盘在当前腕部姿态遮挡，必须后续实际观察验证。机器人 CAD/碰撞体、场景、标定和地图均未改变。

### 第十七轮：CAD 过滤已生效，接近后的图像时序不完整

- 第三十一次启动 / 镜像 24，task-c565cb5f9a5325af2fc784b9：厨房导航、到位、观察、解析、plan_grasp 通过；接近后第一次 capture 返回 OBJECT_NOT_VISIBLE。腕部仍在上方 0.94688 m，杯子仍在原位，没有吸附或最终下降。
- 静止后的同姿态实时 RPC 能测得杯子 [2.27024,3.40925,0.79104]，self_filter 记录 available=true / 534 pixels / 当前资源修订。这次失败不能据此放宽可见性或移动阈值：接近完成后直接读缓存，缓存可能来自手臂运动期间。
- 修复：接近动作结束后等待捕获时间和接收时间均更新的 RGB-D，再进行一次当前视觉目标确认和有界最终下降。删除最终下降后要求第二次识别杯子的循环；该姿态可被夹具遮挡，成功仍要求实际近距离吸附互锁、正确物体身份、独立抬升和稳定物理验证。缺少新捕获拒绝继续，取消/动作期限保持。时序回归覆盖缺帧时不吸附及最终夹持姿态无第二次视觉依赖。

### 第十八轮：接近后仍不可见，修正图像与编码器时间绑定

- 第三十二次启动 / 镜像 25，task-4b93403f300d1a4b1ed9fcd4：厨房、解析、规划通过；pick 在第一次接近后重观测仍 OBJECT_NOT_VISIBLE，无吸附。原日志与物理状态保留，不能声明抓放通过。
- 输入审计发现：相机底盘位姿已经按 sensor_stamp_ns 插值，joint_positions_at_capture 却仍取组装时最新编码器，允许约 200 ms 时间偏差。运动帧上的 CAD 掩码会与图像错位；静止后同位置正确识别不能证明运动时绑定正确。
- 修复采集接口：保存有界编码器时间序列，严格使用图像时间的前后采样线性插值（源为已连续的旋转/平移关节量）；无前后括号、间隔 >100 ms、字段变化或非有限值拒绝组装。编码器回调也触发等待中的 RGB-D 组装。CAD 过滤现在要求编码器有效时间恰等于图像时间，并将两份原始时间标识作为字符串写入固定观测；不外推或重标时间。

### 第十九轮：接近观测通过，IK 将夹爪误当臂关节

- 第三十三次启动 / 镜像 26，task-96b957df43a29a380cb66c55：厨房、解析、规划通过；接近后目标识别通过，进入最终下降，第 0 段关节超时。实际杯子仅约 0.45 mm 位移，未吸附；左端链接停在 0.92988 m。失败目标/实测六电机角度和 0.04 rad 到位阈值在 STATE_CHANGED 消息及原任务中保留。
- 审查发现 solve_tip 优化了全部 6 个电机，包含夹爪。为维持夹爪坐标方向，求解将夹爪张角调到 0.8634 rad、腕部倾斜，夹爪/腕部几何可能与桌面约束干涉。未通过提高力矩、扩大到位误差或吸附距离绕过失败。
- 家庭原型修正为 5 个臂电机作为 IK 自由度；夹爪固定为本吸附辅助模式的 0 rad 导航/组装位姿，单独受原限位和动作控制。当前实测杯子目标的左臂接近/抓取点可达；右臂拒绝不可达。保留历史简化夹具的可选第六关节求解仅供其原回归。原型 CAD、桌面碰撞体和吸附 <=0.09 m 互锁均未改变。
- 修正五轴 IK 后的离线整段预检发现：从折叠导航姿态开始，每个中间点强制工具竖直会立即不可达。空臂先以位置约束展开到上方接近点，再在该点对齐工具；最后下降及持物阶段保持竖直约束。夹爪仍固定，未提高关节限位或改动碰撞体。该发现发生在下一轮运动前，预检记录随测试保留。
- 同次审查纠正放置净空：旧 0.12 m 杯心高度减去杯半高 0.06 m，仅高于盘面 0.06 m，不能跨过高 0.10 m 的盘沿。五轴 IK 的实际厨房目标预检支持 0.185 m 杯心转运高度（0.10+0.06+0.025 m 净空）。家庭抬升改为 0.13 m、转运先高位再下降到释放位；原吸附身份/稳定验证和盘内约束不变。该修正在首次放置动作前完成，避免明知几何干涉仍派发。

### 第二十轮：五轴与夹爪分离后仍有碰撞，校准正确工具坐标

- 第三十四次启动 / 镜像 28，task-7787b5199035913bfcaec194：厨房/识别/解析/规划通过；最终下降第 1 段关节超时，夹爪目标 0 rad 而实测约 -0.0848 rad。杯子被推移约 3.84 cm，未吸附；停止后保存原图、物理状态和所有目标/反馈角度。
- CAD 审计进一步确认：原实现以活动夹爪铰链原点作为工具点、且夹爪关闭；工具原点接近杯心之前，腕部/指几何已碰杯。仅修正 IK 自由度不能修复 TCP。
- 以固定指末端实测 CAD pad 标定 TCP [0.012,-0.101,0] m，父链接 link5；夹爪保持独立张开 1.5 rad。同一 JSON 标定配置供 Python FK/IK 和 C++ 物理近距互锁读取，必须完全匹配，原 CAD 和碰撞几何不变。
- 实际厨房观测上的五轴全程预检：抓取、上方接近、抬升和盘沿上方转运的位置误差均 <1e-8 m，竖直约束误差 <1e-7；不表示已物理通过。保留相机/地图修订，独立增加工具内容修订与原规范条款；下一轮需重新物理验收。

### 第二十一轮：TCP 修正到位，夹爪积分收敛不足

- 第三十五次启动 / 镜像 29，task-bf9f70d90485606bf18b7a84：厨房导航、识别、规划通过，最终下降第 2 段停在 JOINT_TARGET_TIMEOUT。五个臂关节误差均小于 0.04 rad，夹爪目标 1.5 / 实测 1.4343 rad。杯子位移约 1.8 mm，未吸附。保留 pick-tcp-21 图像、编码器与 suction-state-21。
- CAD 与当前编码器审计：活动指最低约 0.884 m，杯顶约 0.850 m；活动指没有接触杯子。此处不能继续归因于碰杯。Gazebo JointPositionController 使用仿真 dt 积分，原 i_gain=0.2 对张开夹爪的重力负载收敛太慢。
- 将本原型位置 PID 的 i_gain 显式配置为 2，积分输出仍限于 +/-1、总输出仍 +/-20，P/D、关节限位、0.5 rad/s 下发限速、12 s 单段期限、0.04 rad 到位判据和全部碰撞体不变。未使用 bypass physics 的 velocity-command 模式。依据 [Gazebo Harmonic JointPositionController 官方源代码](https://github.com/gazebosim/gz-sim/blob/gz-sim8/src/systems/joint_position_controller/JointPositionController.cc)。
- 控制参数改变会改变完整资源摘要；本次严格版本绑定不复用旧地图，保留 scan-c236dd4ad963 与原生数据库，重新采集匹配新摘要的地图。TCP 配置 8 项回归通过，控制器/后端 25 项回归通过。

### 第十一轮扫描：积分控制资源重新建图

- 第三十六次启动（mapping），新资源下全部十段路线完成，返回客厅：scan-3cb13c7a67b6，232 帧 / 220 次配准 / 2 次视觉闭环 / 76451 points / 23.4940034 m。
- 保存地图修订 b379d02c6d08fbb528e69189db447cb8bac136be22ec2cc34100c54d021aee11；标定修订仍 68c581e7c5e70c1b1909e66ee3e68560b2d5c51eee276f684ed22fe9be80f58c。进度与 manifest 留在 artifacts/gazebo-xlerobot-map-trial-11。
- 动作前可达性预检增补抬升、盘沿转运和释放点；位置均来自本次捕获，不从模型注册表补齐。匹配新 PID 的 SDF、工具配置/预检/资源回归共 25 passed。镜像 30，随后以 localization 开始新任务阶段。

### 第二十二轮：厨房物理到位后，持久化延迟使固定证据过期

- 第三十七次启动 / 镜像 30，task-0569cd1d64ee0b5004ecd7e1：Nav2 实际状态 SUCCEEDED，Runtime 生成原始捕获 1790453937115 ms；Agent 约 1081 ms 后接收/校验时以 reconstruction stale 拒绝。未进入抓放。地图本次物理导航没有因路径错误失败。
- Runtime JSON journal 已达 94015688 bytes、128 条命令；新增完整命令固定证据后，每次 begin/terminal 都重写所有历史事件。该模式导致时间和存储放大，不能靠放宽 1 s 新鲜度解决。完整 v1 文件保留于 artifacts/gazebo-xlerobot-runtime-journal-v1-before-blob-migration.json。
- 升级公共 Runtime journal v2：事件字节列表按 SHA-256 独立落盘；原子索引仅引用 blob。先 fsync 事件文件/目录，再原子提交并 fsync 索引；只有索引提交后才回收不再引用的旧事件。待核对/已核对未知命令仍不可被正常终止历史淘汰。原 v1 在下一次落盘迁移，回放字节、fencing 和停止锁存保留；缺失/篡改 blob 在重启时锁存停止，不能重新执行。

### 第二十三轮：实际抓取通过，持物遮挡使盘面不可见

- 第三十八次启动 / 镜像 31，task-5292ff25425ca9b9db22cf0d：厨房导航、视觉解析、规划、manipulation.pick 和独立 verify_grasp 均通过 command_observation。实际陶瓷杯被 DetachableJoint 附着并抬升约 0.126 m；持续身份/相对位姿稳定验证通过。不是吸附 ACK 即成功。
- manipulation.place 以 DESTINATION_NOT_VISIBLE 拒绝：当前 RGB 图显示手臂与持有杯子遮住盘面，感知仅输出杯子/花瓶。原始 held-23.png/json 及物理状态保留；杯子仍附着，未放置，未返回客厅。
- 新增家庭驱动内部单次有界主动观察：在当前工具位置向本体内收 0.06 m、上升 0.10 m，保持持物竖直与所属任务；随后等待新 RGB-D 并重新识别盘面。该动作不用盘面缓存/模型位置，仍不可见则失败，不循环搜索/重新派发任务。动作前 workspace 预检包含该观察位置。放置前另加实际 held 身份校验。
- 运行时 journal v2 实测索引 29146 bytes，原事件已摘要绑定；本轮导航和抓取证据正常通过新鲜度校验。旧 94 MB v1 完整文件保留，不放宽传感器年龄。

### 第二十四轮：抓取重复通过；观察姿态与载荷坐标修正

- 第三十九次启动 / 镜像 32，task-2ac51e29abc36a2ed6329004：抓取/独立验证通过；原上升内收观察仍被前臂遮挡，place 返回 DESTINATION_NOT_VISIBLE，没有释放。原 view-24 与 suction-state-24 保留。
- 任务已失败、无活动命令后，经公共 Runtime arm.move 执行五次有界姿态诊断（24、24b、24c、24d、24e），每次命令 ID 独立、限速、当前编码器 FK/IK 与完整回执保存；没有调用私有位姿写入，也未把这些诊断计为 NL 通过。上升/跨身/近身高位仍遮挡；近身外侧低位 [0.05,-0.35,0.925] m（本体坐标工具点）恢复 RGB-D 盘面识别，测得 [2.4580004,3.3351453,0.7309987] m。
- 工具配置 v2 固化两段观察航点：先 [0.26,-0.35,1.08] 再 [0.05,-0.35,0.925]，右侧镜像独立预检；避免穿越机器人颈部，也不依赖盘面真值。本次前臂 CAD/盘沿遮挡诊断留存；没有降低原三个实测盘沿/尺寸/歧义识别判据。
- 放置追加先在原 XY 垂直上抬到盘沿净空再水平转运，防止从较低观察位对角穿过盘沿。杯子相对 TCP 的偏移保存在工具坐标系，用载荷中心 FK/IK 规划；世界常量偏移在手腕转向后不成立，可能产生超过盘内余量的误差。目标盘面仍由本次视觉捕获测量，附着反馈仅用于相对载荷姿态。
- 时序复核追加源传感器 fence：命令完成时记录编码器源时间，后置相机源时间必须更晚，收到时间更新但采集更早的排队帧不能通过。观测 joint_positions 改为捕获时编码器；墙钟由同一仿真时刻的前后编码器时钟锚点映射，不再取延迟图像的接收时刻。保留 sensor_stamp_ns、clock 来源和实际接收时间；拒绝外推、时钟跳变和长间隔。此软件时钟桥不构成硬件相机时钟认证。
- 工具/载荷/捕获/后端与时钟回归 73 passed。下一轮重新自然语言物理验收，仍未宣称完整闭环。

### 第二十五轮：首个严格自然语言搬杯闭环通过

- 第四十次启动 / 镜像 33，task-0d32f0224882c6144301cfb0（LLM 计划）：从客厅去厨房、当前 RGB-D 识别/解析/规划、实际抓取/抬升、独立抓取验证、有界主动观察、载荷中心规划/放置/释放、独立盘内/直立/稳定验证、返回客厅及到位验证全部通过。
- run_home_task_suite --scenario mug-transfer 严格验收 passed=true；所有确认步骤有 command_observation，图像原字节 SHA-256 与命令固定 capture ID 校验通过，两段导航绑定 scan-3cb13c7a67b6 / b379d02c...，无物理重试、无任务内世界重置。
- 单独物理诊断显示杯子 [2.4681170,3.3449099,0.7899997]，相对盘模型 [0.0081170,0.0099099,0.0799997] m，attached=false、held=""，倾斜接近零。该 oracle 仅用于验证，不参与目标视觉定位。
- 原证据 artifacts/gazebo-xlerobot-tasks-trial-25、suction-live-25 保留。此处只证明搬杯单场景；下一轮必须在同一新的物理 episode 内依次通过巡检、厨房往返、搬杯往返，不能拼接独立轮次充当完整验收。

### 第二十六轮：连续巡检和厨房去程通过，返程遇到无纹理白墙

- 第四十一次启动 / 镜像 33，巡检 task-28e1d131c5fa2c3a02eebec7、厨房观察 task-ec4c670816c8713b83f9c506 均通过严格验收。厨房返程 task-464e46626ab917803036a1d4 在走廊 NAVIGATION_OBSERVATION_LOST；搬杯尚未执行，此轮完整套件失败。
- 冻结诊断：base/head 149/302 ms，odom/TF 31/30 ms，RTAB-Map 149 ms，当前视觉词 13、字典 77165，唯一 blocker VISUAL_QUALITY_LOW。本次不是陈旧图像，也不提高 freshness/词数容差。
- 停车后同位置原始图像保留 corridor-base-26 / corridor-head-26。OpenCV ORB 的 FAST=7/5/3/2/1 对底盘相机分别产生 9/10/10/11/12 个角点；头部同图分别 224。该比较是视角诊断，角点不等于 RTAB-Map 实际词数。进一步发现之前第十五轮只调整 home_rtabmap.yaml，而 Gazebo 实际选了 gazebo_house_rtabmap.yaml；之前关于参数修复生效的推断需以此更正。
- 统一同源家庭 profile，显式 slam_camera=head，其他 profile 默认 base 且拒绝非法选择；两路原始 RGB-D 继续用于 Nav2 障碍物和 Runtime 安全。选择的是机器人传感器，不引入 Agent 仿真器分支。每份 profile 的规范 JSON SHA-256 与资源摘要分别绑定原生数据库路径，更换输入或参数不能复用原生旧图。
- 导航收纳姿态的机器人 CAD 可见顶点分析：最大光轴深度约 1.00 m、最大相机距离约 1.20 m。家庭头部词典/视觉匹配仅使用 1.05–4.9 m 实测深度，原生网格最小范围 1.25 m，排除本机近景与 sanitizer 的 5 m 无返回替代点；不是环境掩码，也不影响两路原始近障传感器。实际控制和碰撞门槛不变，头部特征词与地图可用性仍须重新建图/任务验证。
- 依据 [RTAB-Map 官方参数](https://raw.githubusercontent.com/introlab/rtabmap/master/corelib/include/rtabmap/core/Parameters.h) 的 Kp/Vis 深度范围与 detector 定义。当前 DetectorStrategy=8 是 GFTT/ORB；历史将其简称 ORB 的描述只适用于描述子与离线对比，不能等同于运行时完整特征提取器。
- 相机选择/版本隔离/启动配置回归 36 passed，镜像 34/35 构建完成后第四十二次启动重新 mapping；旧原生图、保存地图、失败轮次保留。

### 第十二轮扫描：头部原生导航输入的新地图

- 第四十二次启动 / 镜像 35（mapping），实际 ROS 订阅 head RGB/CameraInfo，Kp 深度 1.05–4.9 m、FAST=7，数据库 /data/maps/home_furnished/0baf96957cca77ef/head-97634ababe40068d/rtabmap.db。初始有效视觉词 201、途中读取 221，均超过原 20 门槛；实际记录 native-head-input-42。
- 完成全部十段路线并回客厅：scan-43923f1d66c7，233 帧 / 220 次配准 / 2 次视觉闭环 / 82977 points / 23.4572921 m。保存地图修订 ea22778fc590b887356bd5b3b9b295ddf9cdc9226bbd90b30b0425d5636f6785；标定仍 68c581e7...。原始 progress/manifest/summary 在 artifacts/gazebo-xlerobot-map-trial-12。
- 网关 DenseSLAM 的保存地图仍来自底盘原始测量，RTAB-Map 原生导航地图来自已声明头部输入，两套资源保留各自职责和来源。资源/CAD/房间布局未变化，未复制旧视觉词典。扫描完成不代替下面的自然语言验收。

### 第二十七轮：首次派发前的瞬时能力目录拒绝

- 第四十三次启动 / 镜像 35，task-88d4cf43054e4cb9319f37b2 在 plan 准入时返回 observe_scene unavailable / RGBD_NOT_READY；没有 TOOL_ACTIVITY、未调用任何动作。保存地图 scan-43923f1d66c7 正常，稍后实际 RTAB-Map/current 201、传感器恢复。
- 通用 Agent Runner 在计划派发前最多 2 s 刷新只读 Runtime 能力目录，100 ms 间隔，仅针对已声明但暂不可用或 PhysicalReady=false 的结果。只在新目录实际恢复后继续；未知工具、身份不匹配、传输/profile 错误立即拒绝；持续故障、调用方期限和取消仍拒绝。此流程没有 Invoke、命令重放或仿真器名称分支。
- 新回归验证真实状态恢复后才通过、未知技能/型号身份立即拒绝、已取消调用不查询和持续故障不派发；第一轮测试假设 auto 不归一已被既有 NormalizeAdapter 语义拒绝，修正夹具为任意 custom 注册键，不修改真实身份校验。edge/agent、cmd/local-agent 回归与 Go 第七次构建随后完成，下一轮重新完整物理验收。

### 第二十八轮：巡检通过，保存地图的厨房工作位净空不足

- 第四十四次启动 / 镜像 35 / Go 构建 7，巡检 task-786c7b12d6286cd28c56bb16 全部通过；厨房 task-175f1a14835702f5e1ea95f7 在保存地图准入 GOAL_NOT_CLEAR 拒绝，尚未向 Nav2 下发厨房导航。当前视觉词 187、无定位/采集 blocker，不归因为头部输入质量。
- 地图 scan-43923f1d66c7 的厨房 0.32 m 足迹与两个占用格相交，距离约 0.316/0.309 m。反查真实点云：一格为 0.737–0.825 m 高的厨房物体边缘，另一格为 0.077–0.726 m 高的桌面竖直边；不是应清除的单个孤点。最大优化位姿修正 0.06817 m，最终 anchor [-0.05646,-0.01613,-0.00510]。没有删除占用格、缩小底盘、放宽未知区域或改工作位。
- 发现 DenseSLAM 原本把驱动 1 mm 不确定度只用于相邻里程计增量，仍允许逐步累计 ICP 偏差。当前 Gazebo PlanarDrive 发布的是世界坐标中实际物理位姿，具有独立的绝对锚定；这个测量性质此前未传给图优化。新增可选 absolute_odometry_sigma，明确提供的驱动才加入每帧绝对位姿因子，并记录到地图 provenance。该家庭驱动同时声明相对和绝对 1 mm/1 mrad；通用默认与实机轮式里程计没有绝对因子，不能将积分里程计当独立全球测量。
- 回归用有偏 ICP 的多帧图对比，要求声明绝对测量时累计误差保持 <2 mm，同时相对默认仍允许漂移；非有限、非正或错误维度 covariance 拒绝。下一轮重新采集保存地图，原生头部字典/资源不变，旧失败地图留存。

### 第十三轮扫描：绝对位姿因子与原足迹预检

- 第四十五次启动 / 镜像 36 / Go 构建 7。原生 RTAB-Map 已使用同资源的头部数据库并处于 localization；网关本轮重新采集、优化、发布保存地图，两套地图的输入与职责分别留存。
- 十段实际路线完成并返回客厅：scan-2741fff12bf4，229 帧 / 218 次配准 / 2 次视觉闭环 / 72962 points / 23.4617865844 m。地图修订 55d732335ef0142cc2774bdea5bc76c25eebc5b81ebbb9c2bee27c732f561499，标定 68c581e7c5e70c1b1909e66ee3e68560b2d5c51eee276f684ed22fe9be80f58c。
- provenance 保存 absoluteOdometrySigma=[0.001,0.001,0.001]。实际最大优化平移修正 0.0397094 m，mapFromWorld=[0.00010956,-0.03970923,-0.00000222]；合成回归的 <2 mm 结果不能当成本次实测误差承诺。
- 精确保存网格上的原 0.32 m 足迹检查：客厅、卧室、卫生间、厨房全部 clear，从客厅出发的已知路线点数分别 1/115/158/115。未删除占用或放宽足迹。原始进度/manifest/summary 与只读预检位于 artifacts/gazebo-xlerobot-map-trial-13、gazebo-xlerobot-map-preflight-13.json。
- 本轮完成建图后，直接在同一物理 episode 开始第二十九轮全套自然语言任务，没有重启、复位对象或写入底盘位姿。绝对位姿与家庭服务回归 83 passed；是否通过完整闭环以其最终严格任务验收为准。

### 第二十九轮：同一物理 episode 完整自然语言闭环通过

| 任务 | 实际任务 ID | 确认步骤 | 实际导航 | 校验原图 | 结果 |
|---|---|---:|---:|---:|---|
| 卧室、卫生间巡检并回客厅 | task-4ca707c76d2f80583e60b3cc | 11 | 3 | 24 | SUCCEEDED |
| 客厅去厨房检查环境 | task-3bf26193bb85070f41aa8c2b | 5 | 1 | 12 | SUCCEEDED |
| 厨房返回客厅 | task-9df5baa03bd3b3d568782f68 | 5 | 1 | 12 | SUCCEEDED |
| 厨房拿杯、放盘并回客厅 | task-7d0ebec43fa52df3d6be694f | 13 | 2 | 28 | SUCCEEDED |

- 四个任务 plan.source 均为 llm。严格脚本按实际工具、房间顺序、当前命令固定捕获、图像原字节 SHA-256、地图/标定绑定与实测到位验证，不按 LLM 步骤名称打分。总计 34 步 / 7 段 / 76 张 RGB/深度图；artifacts/gazebo-xlerobot-tasks-trial-29/summary.json 的 passed=true，脚本退出 0。
- 第十三轮地图建立之后没有重启或重置。physicalRetries=0、worldResets=0；完整杯子任务没有用诊断动作补步骤，也没有重发未知物理结果。
- 最终独立物理读取：杯子 [2.4533810312,3.3303363306,0.7899999964] m，盘 [2.46,3.335,0.71] m，相对盘中心 [-0.0066189688,-0.0046636694,0.0799999964] m；attached=false、held=""，竖直倾角约 1.9e-6 rad。机器人实际回客厅 [0.0016678048,-1.2514550796,0.035] m。physics oracle 只核对结果，不用于视觉目标定位。
- verify_placement 记录 5 个独立物理序列（14095–14099），0.2 s 稳定期，最大相邻位移 0 m；source=gazebo_physics、mode=sim_suction、relation=inside:kitchen-tray。没有把仿真附着反馈当成硬件真空或夹指力证明。
- 实际镜像 ID sha256:6b429ce4ca59f5d274270589b3b01fa4194387f37c7f1f93e2d7aec7c3c3651b；镜像源码标签 80c90a6789436913eb55878e650ac6fe2de1b4a50a9fe62ff395aaf30735a521 与当前构建输入匹配。资源 0baf96957cca77efcc8b4ad36c5b537adaf6488805279eb0a9d60c0ca16b50d5；头部导航 profile 97634ababe40068d4a075d88225b88844073b3bc498de48518c70e5f41ff5350。完整摘要与文件路径存入验收清单，日志、地图、图像留在原运行目录。
- 任务结束后保留当前已放杯场景，完整软件门禁在机器人无活动任务时执行。后续重新验证搬杯须显式开启新 episode，不能在已占用托盘上悄悄复位。

### 部署资源清理与兼容范围

- Gazebo 部署入口只接受同源 home/home_task/home_furnished，三者解析成相同资源；旧差速机器人、独立彩色工位与 AWS 原始房屋组合退出默认/部署路径。旧生成的 harmonic-models、photos 和 aws-small-house-harmonic.sdf 已清理，当前资产目录保留 XLeRobot 导出、固定上游家具源、许可证与装修包。
- 旧工位仅在 tests/fixtures 中支持历史回归与异构契约；MuJoCo 的原型、显式 tabletop 基线、历史地图/报告仍具有测试用途，不作为本轮 Gazebo 成功证据。Agent 不按这三个家庭别名或仿真器名称选择执行算法。
- 当前 Docker 运行容器为本项目 Gazebo 与本项目 Redis/MySQL；未删除项目服务数据卷或历史地图。批量清理不用于掩盖失败记录。

### 最终软件门禁

- 首轮完整 make test：Go 和真实 Go/Python Runtime 边界 2 项通过；Python 2407 passed / 3 failed / 40 skipped，796.78 s。三项失败分别为报告引用的英文 Nav2 日志被文档检查误认成 Make target、可视化依赖清单未列新增固定 Rtree、语义地图测试仍从旧 MuJoCo 文件解析已迁出的常量。原日志保留，逐项修复后定向复测通过；未修改运动判据或为通过而跳过测试。
- 第二轮完整 make test 退出 0：全部选定 Go 包通过，真实边界 2 passed，Python 主集 2410 passed / 40 skipped / 20 warnings（778.50 s），Web 479 passed / 0 failed。跳过项是可选环境/资源依赖的既有条件测试，不代表目标硬件认证。
- make lint、make generate-check、make build、go test ./tests/docs、Python 文档 5 项、book-check（24 sections）均通过。实际 ROS 容器内 NavigationNode 回归 14 passed / 0 skipped，11.12 s，使用真实 ROS 消息、节点和 SQLite，隔离动作客户端以避免外部运动。
- 日志路径、SHA-256 和实际数量见验收清单 softwareGates。受保护主线的 release-gate 仍由对应 GitHub 提交的 CI 独立判定，未通过前不合并。
