# RTAB-Map 与 Nav2 导航环境

此目录为躺营的独立导航服务。RTAB-Map 从底盘 RGB-D 建立/定位三维观测地图并输出二维占据地图；Nav2 结合地图、顶部与底盘相机的深度障碍点云规划路径。地图未观测区域保持未知，规划禁止穿过未知区域。此服务不读取 MuJoCo 物体坐标、碰撞几何或预制环境地图。

运行入口仍是 Robot Runtime 的 `navigation.navigate` 工具。Agent/MCP 不获得裸速度工具。HTTP 仅供受控 Runtime 适配器或已接入 ROS 的机器人控制端使用。

## 两种输入与唯一执行通道

| 启动模式 | 感知/里程计输入 | 速度执行位置 |
|---|---|---|
| `input_mode:=runtime` | gRPC `Observe(streams=['rgbd_raw','sensor_only'], source_id=...)`，双 RGB-D 原始帧；`robot_state.base_pose` 为仿真轮式里程计 | Nav2 发布带原始时间戳的 `TwistStamped` 到私有 `/tangying/navigation/cmd_vel`；HTTP 返回速度，Native Runtime 限速、超时检查后执行 |
| `input_mode:=ros` | 厂家同步 RGB/米制深度、CameraInfo、真实轮式 odom 与 TF，双相机 PointCloud2；不启动 gRPC 相机桥 | Nav2 以明确的 `Twist` 类型输出到配置的 `cmd_vel_topic`，由已调试的实体驱动和 watchdog 执行；HTTP 不返回可执行速度 |

两条速度通道不能同时启用。`ros` 模式下 Runtime 适配器只提交/查询/取消 Nav2 目标，不能再次执行 HTTP 速度。HTTP 的 `actuationMode` 明确区分 `native_http` 与 `ros_driver`，调用方必须核对。

原始 RGB 为 RGB8；深度为 little-endian float32 米。RGB、深度、内参、`base_from_camera`、采集时间来自同次采集。无效深度发布为 NaN，不能转成“前方无障碍”。相机 optical 轴为右/下/前；`base_link` 为 ROS FLU（前/左/上）。本仿真 chassis 本来就是 FLU，初始姿态朝世界 +Y 不意味着需要再旋转相机或速度。

每路相机独立以 monotonic 时钟限制新建采集 RPC 不超过 5 Hz。Native 单帧流结束后不会立即无限重连；慢帧或重连延迟不会积累补发额度。收到的采集时间原样保留，节流不延长数据新鲜度预算。

可选 `robot_self_mask` 是每像素 0/1 的同帧机器人自身首回波掩码，有掩码必须提供 `self_filter_model_revision`。Runtime 只根据机器人 CAD、当前关节与相机标定计算自身深度，并与实际首回波深度匹配；外部物体更近时保留。导航桥检查长度、值与版本，把掩码位置的导航深度设为 NaN、SLAM 彩色像素设为 0，自身后方仍未知。`/camera/{base,head}/rgb/image_unfiltered` 保留原始彩色，用户界面原始图像不受影响。旧输入缺少掩码时不推断自身范围，`/camera/{base,head}/self_filter_status` 和地图 `selfFilter` 明确报告 `available:false`。

局部障碍层对每台相机分别配置 marking 与 clearing：高于 4 cm 的真实点可以标为障碍，地面真实回波保留为清除射线终点。掩码和无效深度没有射线终点，不能清除盲区；未知栅格仍禁止规划通行。

`odom→base_link` 来自里程计，`base_link→camera_optical` 来自真实标定/同帧外参，`map→odom` 仅由 RTAB-Map 发布。HTTP `goalPose` 是 `[x,y,z,qw,qx,qy,qz]`；调用方明确传 `frameId:'odom'` 或 `'map'`。系统 Runtime 的固定 world 坐标在此接入配置中对应 odom；桥通过 TF 把 odom 目标转换为 map，不能把二者直接当成永远相等。

## 本机 MuJoCo + Docker

先启动本地 RGB-D Runtime，确保其声明 `head-rgbd` 与 `base-rgbd`，支持 `rgbd_raw`。Docker Desktop 必须能够访问 `host.docker.internal:50051`。默认地址仅用于本机开发。真实 Runtime 使用 mTLS，`runtime_channel()` 支持 `TANGYING_RUNTIME_CA_FILE`、`TANGYING_RUNTIME_CERT_FILE`、`TANGYING_RUNTIME_KEY_FILE`，挂载可信文件并设置 `TANGYING_RUNTIME_INSECURE=0`。

推荐从仓库根目录使用统一入口：

```bash
make navigation-restart NAVIGATION_ARGS='--build --mode mapping'
make navigation-status
make navigation-logs
make navigation-stop
```

家庭场景使用同一套 ROS 2 / RTAB-Map / Nav2 服务，但必须显式选择场景；启动脚本会把场景同时写入仿真 Runtime、Compose 和 launch 参数，避免把家庭 RGB-D 误接到桌面配置：

```bash
make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'
make navigation-status
```

家庭地图仍需从 RGB-D 和里程计现场建图；`--scene home` 只选择四房间仿真几何与对应参数，不提供预制地图，也不把房间名称当作相机观测。

它只管理固定 `tangying-navigation` 项目，自动生成并复用权限为 600 的 `artifacts/sim-stack/navigation.env`，不打印 token。`restart` 会显式重启本机仿真 Runtime，确保它使用同一导航配置；`start` 不会悄悄改写已运行 Runtime 的环境。镜像已构建时可省略 `--build`。切换定位模式需明确 `make navigation-restart NAVIGATION_ARGS='--mode localization'`。不删除地图卷。

下面是维护者的手动 Compose 等效入口；不要同时使用另一组 token 启动同一 Runtime：

```bash
# 从仓库根目录执行，token 不应写入 Git、日志或命令行参数。
export TANGYING_NAVIGATION_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export TANGYING_NAVIGATION_MODE=mapping
docker compose -p tangying-navigation -f deploy/robot/navigation/compose.yaml build
docker compose -p tangying-navigation -f deploy/robot/navigation/compose.yaml up -d
```

Runtime 端配置同一个私有 token 与 `http://127.0.0.1:18790`。Compose 只发布 loopback 端口；容器内服务监听 `0.0.0.0` 是为 Docker 端口转发使用，不代表可向局域网开放。ROS DDS 默认限制在容器内。

镜像采用 OSRF 官方多架构 `ros:jazzy-ros-base`，依赖来自 Ubuntu/ROS 官方软件仓库，Python protobuf/gRPC 来自其官方 PyPI 包。可通过 `--build-arg ROS_IMAGE=ros@sha256:<已核验摘要>` 固定镜像。Dockerfile 专用 ignore 文件仅纳入导航包与 protobuf，避免复制本地凭据和其他产物。

停止：`docker compose -p tangying-navigation -f deploy/robot/navigation/compose.yaml down`。**不要加 `-v`**，该选项会删除持久地图卷。

## 建图与定位

`mapping` 允许 RTAB-Map 增量建立地图；`localization` 使用已存在的非空数据库，缺库直接拒绝启动。两种模式都不会自动删除数据库。默认卷 `tangying-navigation-maps` 保存 `/data/maps/rtabmap.db` 与独立命令幂等账本 `navigation.sqlite`。

切换定位模式：先正常停止导航容器，再设置 `TANGYING_NAVIGATION_MODE=localization` 并重新启动。请在同一现场完成标定、轮式里程计校验和地图持久化之后切换。空白或缺纹理画面、错误内参/TF、失去定位都会使 ready 为 false，不能把“ROS 进程在运行”视作可导航。

地图内容不会因为机器人静止而每五秒作废。`ready` 同时要求已有非空 RTAB 占据图、当前 RGB-D/odom/TF/RTAB处理状态新鲜、Nav2 action 服务可用；定位模式还要求新的定位姿态与可接受协方差。`MAPPING_ODOMETRY` 明确表示建图期间依赖轮式里程计，不伪装成已经完成实机定位验收。

地图状态的 `readinessBlockers` 明确列出当前未满足条件，`inputAgeMs` 分别记录底部/顶部相机、odom、map TF 与 RTAB 处理输入的年龄，`checkedAtUnixMs` 标明检查时间。桥在原因变化时记录这些无凭据诊断；相机错误仅记录本地校验原因或 gRPC 状态码，不打印 transport details。准备动作后短暂未就绪与持续失去感知应根据这些实际字段区分，不能放宽 1 秒相机/TF 预算来掩盖采集竞争。

导航镜像默认采用 `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`，所有容器内 ROS 节点共享同一 DDS 实现。依据是本机诊断中独立订阅者持续收到新 RGB-D/odom/TF，而 RTAB-Map 自身的 TF 缓存偶发延迟数秒；[RTAB-Map 上游](https://github.com/introlab/rtabmap_ros#recommended-dds) 也建议对此类低算法耗时、通信滞后的情况测试 Cyclone DDS。实机 ROS 驱动与诊断终端应显式使用同一 RMW。需要对比时可指定 `RMW_IMPLEMENTATION=rmw_fastrtps_cpp`，但本地管理脚本会将选择保存进配置指纹，切换必须显式 `restart`，不能在既有运行时上静默混用。更换中间件不改变传感器时间戳、观测新鲜度或速度租约。

从 0.2 起，新发生的导航失败在状态转移时持久化 `failureObservation`，保存触发失败当时的上述精简诊断，覆盖观测丢失、速度租约过期及 Nav2 失败回调；首次终态响应也返回同一份记录。250 ms 速度租约可能先于 1 秒相机门槛触发，因此 `NAV2_VELOCITY_STALE` 的冻结诊断允许 `ready=true`，不能用稍后断流或恢复状态改写当时原因。Native 历史回执对应 `failure_observation`。升级会为现有 goal SQLite 自动添加诊断列，保留全部旧目标；旧目标没有原始诊断时返回 `{}`，诊断本身不可获取时也保留空对象并完成失败转移。排障时先看这份冻结原因，再查看当前 `/v1/navigation/map`，不要将恢复后的状态当作失败时证据。

需要排查速度延迟时，可在启动前设置 `TANGYING_NAVIGATION_TRACE_VELOCITY=1`；日志只记录原发布时间、接收／检查时间和当前目标授权状态，不记录凭据。默认关闭。速度接收与停止看门狗使用独立回调组，普通感知处理不会通过默认互斥回调组串行阻塞它们。

`visualQuality` 还要求 RTAB-Map 实际当前帧与词典均至少有 20 个视觉词。仅有即时深度栅格、但没有有效视觉词典时，`ready:false`。参考 320×240 相机配置 `Kp/MaxFeatures=250`，保留 `Kp/BadSignRatio=0.5` 与默认 20 个几何内点门槛：RTAB-Map 冷启动首先要求至少 125 个真实描述子。较高分辨率或其他相机需要根据实际特征和定位验证调整预算，不能通过关闭质量检查实现就绪。首帧缺少有效特征时等待；不把 RTAB-Map 已剔除的无效节点写入可重开的图数据库。

Compose 与镜像明确使用 `SIGINT`，允许最多 60 秒正常退出，让 RTAB-Map 保存数据库。停止日志应出现 `Saving database/long-term memory...done!`。强杀或掉电后的数据不能仅凭 SQLite 完整性检查认定可用，还应核对视觉词典、图节点并实际重开。系统不自动删除、重建或替换损坏地图。

## 真实 ROS 机器人直连

在已安装 Jazzy、RTAB-Map、Nav2 的 Linux/机器人 ROS 环境内构建本包（同时安装 numpy、grpcio 与当前仓库 protobuf 模块，仅 gRPC 输入模式需要连 Runtime）：

```bash
cd robot/ros2_ws
colcon build --packages-up-to tangying_navigation
source install/setup.bash
export TANGYING_NAVIGATION_TOKEN="<已安全配置的至少24字符token>"
ros2 launch tangying_navigation navigation.launch.py \
  input_mode:=ros mode:=localization database_path:=/可信地图目录/rtabmap.db \
  base_rgb_topic:=/lower_camera/color/image_rect \
  base_depth_topic:=/lower_camera/aligned_depth_to_color/image_raw \
  base_camera_info_topic:=/lower_camera/color/camera_info \
  base_points_topic:=/lower_camera/depth/points \
  head_rgb_topic:=/upper_camera/color/image_rect \
  head_depth_topic:=/upper_camera/aligned_depth_to_color/image_raw \
  head_camera_info_topic:=/upper_camera/color/camera_info \
  head_points_topic:=/upper_camera/depth/points \
  odom_topic:=/wheel/odometry cmd_vel_topic:=/commissioned_driver/cmd_vel
```

厂家发布的彩色图、深度图必须已经对齐且有相同采集时间；Depth 需符合 RTAB-Map 支持的度量编码与 CameraInfo 内参。厂家 PointCloud2 必须使用对应 TF optical 坐标，不能把全知环境几何发布成测量。真实驱动必须独立实现速度限幅、失联归零、驱动使能、实体急停和独占控制；不能把仿真 `base_pose` 的低噪声或 Native 位移执行器移植成真实安全保证。

此命令只配置订阅与唯一输出通道，不替用户启动或验收任何型号的实体电机驱动。实机上线前必须完成地图覆盖、定位丢失、遮挡、盲区、低矮障碍、刹车距离、急停与断网测试。

## HTTP 契约

所有请求必须带 `Authorization: Bearer <token>`，响应 `Cache-Control: no-store`。不提供跳过规划/清空地图/裸速度写入接口。

- `GET /v1/navigation/map?includeGrid=1`：额外返回真实 OccupancyGrid 的 `cells`（-1 未知、0 空闲、100 占据）和 `origin:[xyz,wxyz]`，上限 262144 格；超出或尚未收到时返回 `gridUnavailable`。不包含该查询参数时只返回紧凑元数据。
- `GET /v1/navigation/map`：`ready`、`mode`、`frameId:'map'`、`robotId`、`mapRevision`、宽高/分辨率、有效网格数、地图采集时间、`mapPose`、`poseSource:'rtabmap_tf'`、`poseObservedAtUnixMs`、相机和里程计时间、`localizationState`。
- `POST /v1/navigation/goals`：严格 `{commandId,goalPose,frameId}`，返回 202 与 goal 状态。重复 commandId+相同参数返回原记录；不同参数返回 409。一次仅一个活动目标；未就绪 503。
- `GET /v1/navigation/goals/{goalId}`：`PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED`，`goalPoseMap`、当前 `mapPose`、TF 采集时间、`mapRevision`、`latestCmdVel:{linearX,linearY,angularZ,stampUnixMs}`、`velocityValid`、`actuationMode`、`stopReason`；成功另附 `completionSource`、`completionPoseObservedAtUnixMs`，失败另附冻结的 `failureObservation`。
- `POST /v1/navigation/goals/{goalId}/cancel`：撤销目标并关闭该目标的速度输出。Native 或实体驱动仍必须自行即时归零。

PENDING 不表示有运动命令。RUNNING 只有在收到 Nav2 实际速度后出现；Native 模式只接收 Nav2 原始 TwistStamped 时间；拒绝陈旧/未来及早于当前 goal 接受时间的命令，绑定当前 goal，过期速度保留原时间，不能续期。客户端应持续查询（至少每 2 秒一次；Native 建议 100 ms），失联租约到期自动取消。RGB-D/TF/定位失效会立即取消并失败；客户端还需独立核对速度时间与目标最终误差。

当前 footprint 为专门空手收臂姿态实测边界外扩后的 FLU 矩形 `[[-.24,-.23],[.22,-.23],[.22,.21],[-.24,.21]]`，高度包络 `[-.06,1.20] m`。Runtime 必须先验证 NAV_STOW、无持物与实际包络；HOME 或未知臂姿态不能使用此 footprint，必须拒绝导航。桌面等真实测得障碍不能为演示而删除。

当前工作台小范围验收参数：最大平移速度 0.05 m/s、最大转速 0.2 rad/s，Nav2 目标容差 0.005 m / 0.03 rad。Runtime 应独立检查不超过 0.015 m / 0.04 rad。这里只是参考工作台配置，不能作为其他底盘现场安全参数。

目标坐标遵循 Nav2 的固定世界目标语义：`frameId=odom` 只描述提交时的目标坐标，接受时使用当时有效 TF 转换并绑定到 `map`，之后不会跟随 odom 坐标系漂移。地图闭环校正或里程计漂移可能使最终 map 与原始 odom 判断存在差异；5 mm 控制容差为独立 15 mm 双源核对预留误差余量，不能保证任意定位分歧都能通过。超过预算必须保持停止并重新观测／核对任务，而不是自动重复动作或扩大验收门槛。

0.2 将到位精度和环境网格精度分开：局部碰撞地图保持 25 mm，DWB 新增 `tangying_dwb_critics::ContinuousGoalCritic`，在最终 0.15 m 内用同一 odom 坐标系中真实目标与轨迹 0.5 秒预测点的欧氏距离评分。原 `GoalDist.scale=12` 的栅格归一化权重等于每米 6，新增连续项也取每米 6。同包的四个薄包装 `ApproachGoalAlign/ApproachPathAlign/ApproachPathDist/ApproachGoalDist` 共享 `ContinuousGoal.activation_distance`：距离不大于 15 cm 时统一关闭这四项栅格路径和距离偏好，超过此距离则直接使用 Nav2 原实现。这样避免 1.5 秒末端跨格的离散惩罚覆盖 0.5 秒真实接近目标的收益；不靠提高权重强行压过其他评价。近场由连续 XY 与原 `RotateToGoal` 协调最终到位和朝向。进入此阶段不表示路径已知或可通行：所有候选仍须通过完整足迹的 1.5 秒碰撞检查，未知空间照常拒绝。插件不改地图、不生成速度，也不能覆盖 `ObstacleFootprint` 对碰撞和未知区域的拒绝。过细的 5 mm 地图曾使 RGB-D 清除射线之间出现未知间隙，因此不把进一步细化碰撞图或填平未知当成精确到位的修复。最终旋转保持安全的轨迹终点评分，将角速度样本数增至 17，使终点半采样误差 0.01875 rad 小于 0.03 rad 容差。整条碰撞轨迹仍为 1.5 秒。修改速度、采样数或容差后，运行 `test_launch_config.py`、C++ critic 测试并重新验证实际到达误差。

同一工位连续处理第二个物体时，可能已经在最终到位范围内。HTTP 节点在本次目标完成 TF 绑定后重新核对真实、就绪且 1 秒内的地图定位：已在 15 mm / 0.04 rad 内时返回 `POSE_ALREADY_CONFIRMED`，不发 Nav2 action，也不接受任何速度租约。回执 `completionSource=pose_confirmation` 与实际运动后的 `nav2_action` 明确区分；需要移动时仍使用 5 mm 控制容差。Runtime 对两种来源都独立核对原始 odom 目标，并保留本次新观测。首次远距离导航成功和第二次无运动位置确认不能描述成两次移动。

SQLite 在终结目标的同一事务保存 `completion_source` 和完成决策依据的原定位时间。Native 的 `map_receipt` 包含 `completion_source`、`completion_pose_observed_at_unix_ms` 和 `checked_at_unix_ms`，同时验证完成时定位与当前定位仍然新鲜；未知来源或旧日志缺少来源/时间时拒绝借用恢复后的实时状态补成成功，不重发物理目标。正常已完成任务仍按 Agent 原有持久回执回放。

## 验证与可追溯边界

```bash
.venv/bin/python -m pytest -q robot/ros2_ws/src/tangying_navigation/test
# 在离桌初始位置启动当前仿真，等 navigation-status 返回 ready 后验收：
make navigation-start NAVIGATION_ARGS='--build --mode mapping'
make navigation-status
.venv/bin/python scripts/run_navigation_acceptance.py \
  --output artifacts/acceptance/navigation-mapping-v02
# 镜像构建后验证真正的 ROS 安装与 launch 参数：
docker run --rm --entrypoint /bin/bash tangying-navigation:dev -lc \
  'source /opt/ros/jazzy/setup.bash && source /opt/tangying-nav/install/setup.bash && ros2 launch tangying_navigation navigation.launch.py --show-args'
# 真正编译后的连续目标 critic 数学边界与 pluginlib 动态加载：
docker run --rm --entrypoint /bin/bash tangying-navigation:dev -lc \
  'source /opt/ros/jazzy/setup.bash && source /opt/tangying-nav/install/setup.bash && colcon test --packages-select tangying_dwb_critics && colcon test-result --verbose'
# 真实 gRPC 服务 -> ROS DDS 双图像/点云、FLU odom、TF 与自身掩码：
docker run --rm --entrypoint /bin/bash tangying-navigation:dev -lc \
  'source /opt/ros/jazzy/setup.bash && source /opt/tangying-nav/install/setup.bash && python3 /opt/tangying-nav/src/tangying_navigation/test/ros_rgbd_probe.py'
# 验证 Native 单帧流重连速率及双相机自身掩码：
docker run --rm --entrypoint /bin/bash \
  -e TANGYING_PROBE_HEAD_SELF_MASK=1 -e TANGYING_PROBE_SNAPSHOT=1 \
  tangying-navigation:dev -lc \
  'source /opt/ros/jazzy/setup.bash && source /opt/tangying-nav/install/setup.bash && python3 /opt/tangying-nav/src/tangying_navigation/test/ros_rgbd_probe.py'
```

本地测试验证原始字节/坐标契约、真实 HTTP 鉴权与幂等/重启/取消/失去观测边界；它们不等价于 RTAB-Map 和 Nav2 的现场导航测试。实际容器构建、ROS节点启动、地图和导航闭环的集成验收结果由本轮发布记录单独列出，不能以 mock driver 测试代替。

`run_navigation_acceptance.py` 只接受本机 HTTP 仿真入口，会新建并批准“把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来”任务，核对离桌初始距离、18 个步骤、6 次唯一物理工具调用（首段导航移动、第二段无运动位置确认和 4 次抓放）、实际导航位移／误差、历史 capture 绑定、关键原始图哈希及两次三帧放置验证。输出目录必须全新；脚本不重置世界、不清除日志、不重试不确定物理动作。使用 `--pause-seconds 65` 可验证在导航完成的安全步骤边界暂停超过旧的 30 秒计划预算及单次导航 60 秒执行预算后，剩余步骤仍能继续；暂停不是急停。

0.2 最终基线的建图任务 `task-547e3d2f4b391a5382617a28` 已从 Y=-0.60 m 实际移动 641.995 mm，完成完整 18 步、20 份采集和 40 个原始图哈希核对；本体到位误差 8.339 mm / 0.02375 rad。真实断源任务 `task-46461b8b7a9fab43498c4398` 在运动中停止 ROS 采集桥，确认任务失败、底盘停止、无后续抓放，恢复源后旧证据与失败状态保持不变。最终任务与完整验证范围见 [导航验收记录](../../docs/development/rtabmap-navigation.md#验收证据与历史范围) 和 [0.2 发布说明](../../docs/releases/v0.2.0.md)，不以旧版 5 cm 历史记录替代本轮结果。

同一最终基线的保存地图定位任务 `task-43681128efff51076e4fbb06` 实际移动 643.424 mm，安全边界暂停 65.183 秒后继续完成 18 个唯一步骤；额外 2 次确认都是只读重观测，物理工具未重放。23 份历史采集、46 个原始图哈希通过，本体到位误差 7.352 mm / 0.0225 rad。首段导航运动与第二段无运动位置确认分别记录来源；恢复不是重跑整条任务。

断源脚本 `scripts/run_navigation_source_fault.py --task-id <正在执行的仿真任务> --output <新目录>` 只针对明确指定任务，核对唯一采集桥的容器 ID、PID 和启动时间后执行 SIGSTOP，并最终对同一身份 SIGCONT。它不会创建、批准或恢复任务。停止证明主动请求已登记来源的 `/v1/scene/camera` 原始 snapshot，保存同帧本体位姿和原采集时间；异步 telemetry 缓存仅用于起点与移动检测。脚本要求每帧在 1 秒内、原时间递增且覆盖至少 600 ms；断流和恢复两段都独立检查停止，故障回执与原始图哈希不得被后续状态替换。该故障注入覆盖同桥双 RGB-D 与 odom，不等于单台实机相机拔线测试。

参考仿真工位的地板采用实际渲染的非重复纹理，上下 RGB-D 都只能通过真实渲染像素和深度观测它。底盘收臂并应用自身掩码后，大面积纯色地面曾使 RTAB-Map 当前帧视觉词归零；即使深度仍能生成地面点云，也不能据此认为视觉定位正常。实机验收必须在最终收臂姿态与实际照明下检查底部相机的稳定特征、深度和彩色配准、可见地面及地图就绪。纯色／反光地面需要改善实际可观测性或配置经验证的额外里程计方案，不能关闭 `VISUAL_QUALITY_LOW` 等门槛。

2026-09-08 历史容器验收已完成官方 Jazzy/RTAB-Map/Nav2 安装、colcon 构建、双 RGB-D gRPC→DDS 图像/点云/TF/odom 传输和同一地图正常停启。参考视图产生 155 个真实描述子；停止后数据库保存 88 个视觉词与 155 条特征，完整性检查通过；重新打开后继续生成词典与真实占据地图。此记录证明启动和持久化链路，具体导航成功、重定位及现场安全结论应分别依据对应验收记录。

2026-09-08 的维护曾发现默认特征预算与低分辨率相机不匹配，首关键帧无有效词典、后续无效节点仍保留特征引用，导致重开失败。经明确授权，两个刚创建的仿真故障样本保存在同卷 `/data/maps/rtabmap.db.20260908T144216Z.unusable` 及 `/data/maps/rtabmap.db.20260908T144616Z.unusable`；对应 `maintenance-*.json` 保存原因、原表计数、字节数与 SHA-256。第一个样本 SHA-256 为 `04d97ee48fa65d4092069d95b76212883f721d3e066f4986c1bedb05d9400d93`，第二个为 `d20a9eaa3c0781e93e7a33d99b9a0dc08f12bfe707345b1d921cafdfe6ae3b04`。原样本未删除；这次人工维护不是服务启动时的自动清库逻辑。修复依据为 [RTAB-Map 0.22.1 冷启动特征门槛](https://github.com/introlab/rtabmap/blob/0.22.1/corelib/src/Memory.cpp#L4762-L4767)，同时保留坏签名拒绝与正常退出保存。

官方依据：[RTAB-Map ROS 2](https://github.com/introlab/rtabmap_ros/tree/ros2)、[官方 Nav2 RGB-D 示例](https://github.com/introlab/rtabmap_ros/blob/ros2/rtabmap_demos/launch/turtlebot3/turtlebot3_sim_rgbd_demo.launch.py)、[Jazzy Nav2 参数](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_bringup/params/nav2_params.yaml)、[OSRF 官方 ROS 镜像](https://hub.docker.com/_/ros)。本仓库配置使用这些公开接口，未复制预制环境地图。
