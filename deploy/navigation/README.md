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

它只管理固定 `tangying-navigation` 项目，自动生成并复用权限为 600 的 `artifacts/sim-stack/navigation.env`，不打印 token。`restart` 会显式重启本机仿真 Runtime，确保它使用同一导航配置；`start` 不会悄悄改写已运行 Runtime 的环境。镜像已构建时可省略 `--build`。切换定位模式需明确 `make navigation-restart NAVIGATION_ARGS='--mode localization'`。不删除地图卷。

下面是维护者的手动 Compose 等效入口；不要同时使用另一组 token 启动同一 Runtime：

```bash
# 从仓库根目录执行，token 不应写入 Git、日志或命令行参数。
export TANGYING_NAVIGATION_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export TANGYING_NAVIGATION_MODE=mapping
docker compose -p tangying-navigation -f deploy/navigation/compose.yaml build
docker compose -p tangying-navigation -f deploy/navigation/compose.yaml up -d
```

Runtime 端配置同一个私有 token 与 `http://127.0.0.1:18790`。Compose 只发布 loopback 端口；容器内服务监听 `0.0.0.0` 是为 Docker 端口转发使用，不代表可向局域网开放。ROS DDS 默认限制在容器内。

镜像采用 OSRF 官方多架构 `ros:jazzy-ros-base`，依赖来自 Ubuntu/ROS 官方软件仓库，Python protobuf/gRPC 来自其官方 PyPI 包。可通过 `--build-arg ROS_IMAGE=ros@sha256:<已核验摘要>` 固定镜像。Dockerfile 专用 ignore 文件仅纳入导航包与 protobuf，避免复制本地凭据和其他产物。

停止：`docker compose -p tangying-navigation -f deploy/navigation/compose.yaml down`。**不要加 `-v`**，该选项会删除持久地图卷。

## 建图与定位

`mapping` 允许 RTAB-Map 增量建立地图；`localization` 使用已存在的非空数据库，缺库直接拒绝启动。两种模式都不会自动删除数据库。默认卷 `tangying-navigation-maps` 保存 `/data/maps/rtabmap.db` 与独立命令幂等账本 `navigation.sqlite`。

切换定位模式：先正常停止导航容器，再设置 `TANGYING_NAVIGATION_MODE=localization` 并重新启动。请在同一现场完成标定、轮式里程计校验和地图持久化之后切换。空白或缺纹理画面、错误内参/TF、失去定位都会使 ready 为 false，不能把“ROS 进程在运行”视作可导航。

地图内容不会因为机器人静止而每五秒作废。`ready` 同时要求已有非空 RTAB 占据图、当前 RGB-D/odom/TF/RTAB处理状态新鲜、Nav2 action 服务可用；定位模式还要求新的定位姿态与可接受协方差。`MAPPING_ODOMETRY` 明确表示建图期间依赖轮式里程计，不伪装成已经完成实机定位验收。

地图状态的 `readinessBlockers` 明确列出当前未满足条件，`inputAgeMs` 分别记录底部/顶部相机、odom、map TF 与 RTAB 处理输入的年龄，`checkedAtUnixMs` 标明检查时间。桥在原因变化时记录这些无凭据诊断；相机错误仅记录本地校验原因或 gRPC 状态码，不打印 transport details。准备动作后短暂未就绪与持续失去感知应根据这些实际字段区分，不能放宽 1 秒相机/TF 预算来掩盖采集竞争。

需要排查速度延迟时，可在启动前设置 `TANGYING_NAVIGATION_TRACE_VELOCITY=1`；日志只记录原发布时间、接收／检查时间和当前目标授权状态，不记录凭据。默认关闭。速度接收与停止看门狗使用独立回调组，普通感知处理不会通过默认互斥回调组串行阻塞它们。

`visualQuality` 还要求 RTAB-Map 实际当前帧与词典均至少有 20 个视觉词。仅有即时深度栅格、但没有有效视觉词典时，`ready:false`。参考 320×240 相机配置 `Kp/MaxFeatures=250`，保留 `Kp/BadSignRatio=0.5` 与默认 20 个几何内点门槛：RTAB-Map 冷启动首先要求至少 125 个真实描述子。较高分辨率或其他相机需要根据实际特征和定位验证调整预算，不能通过关闭质量检查实现就绪。首帧缺少有效特征时等待；不把 RTAB-Map 已剔除的无效节点写入可重开的图数据库。

Compose 与镜像明确使用 `SIGINT`，允许最多 60 秒正常退出，让 RTAB-Map 保存数据库。停止日志应出现 `Saving database/long-term memory...done!`。强杀或掉电后的数据不能仅凭 SQLite 完整性检查认定可用，还应核对视觉词典、图节点并实际重开。系统不自动删除、重建或替换损坏地图。

## 真实 ROS 机器人直连

在已安装 Jazzy、RTAB-Map、Nav2 的 Linux/机器人 ROS 环境内构建本包（同时安装 numpy、grpcio 与当前仓库 protobuf 模块，仅 gRPC 输入模式需要连 Runtime）：

```bash
cd robot/ros2_ws
colcon build --packages-select tangying_navigation
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
- `GET /v1/navigation/goals/{goalId}`：`PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED`，`goalPoseMap`、当前 `mapPose`、TF 采集时间、`mapRevision`、`latestCmdVel:{linearX,linearY,angularZ,stampUnixMs}`、`velocityValid`、`actuationMode`、`stopReason`。
- `POST /v1/navigation/goals/{goalId}/cancel`：撤销目标并关闭该目标的速度输出。Native 或实体驱动仍必须自行即时归零。

PENDING 不表示有运动命令。RUNNING 只有在收到 Nav2 实际速度后出现；Native 模式只接收 Nav2 原始 TwistStamped 时间；拒绝陈旧/未来及早于当前 goal 接受时间的命令，绑定当前 goal，过期速度保留原时间，不能续期。客户端应持续查询（至少每 2 秒一次；Native 建议 100 ms），失联租约到期自动取消。RGB-D/TF/定位失效会立即取消并失败；客户端还需独立核对速度时间与目标最终误差。

当前 footprint 为专门空手收臂姿态实测边界外扩后的 FLU 矩形 `[[-.24,-.23],[.22,-.23],[.22,.21],[-.24,.21]]`，高度包络 `[-.06,1.20] m`。Runtime 必须先验证 NAV_STOW、无持物与实际包络；HOME 或未知臂姿态不能使用此 footprint，必须拒绝导航。桌面等真实测得障碍不能为演示而删除。

当前工作台小范围验收参数：最大平移速度 0.05 m/s、最大转速 0.2 rad/s，Nav2 目标容差 0.01 m / 0.03 rad。Runtime 应独立检查不超过 0.015 m / 0.04 rad。这里只是参考工作台配置，不能作为其他底盘现场安全参数。

## 验证与可追溯边界

```bash
.venv/bin/python -m pytest -q robot/ros2_ws/src/tangying_navigation/test
# 镜像构建后验证真正的 ROS 安装与 launch 参数：
docker run --rm --entrypoint /bin/bash tangying-navigation:dev -lc \
  'source /opt/ros/jazzy/setup.bash && source /opt/tangying-nav/install/setup.bash && ros2 launch tangying_navigation navigation.launch.py --show-args'
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

2026-09-08 本轮实际容器验收已完成官方 Jazzy/RTAB-Map/Nav2 安装、colcon 构建、双 RGB-D gRPC→DDS 图像/点云/TF/odom 传输和同一地图正常停启。参考视图产生 155 个真实描述子；停止后数据库保存 88 个视觉词与 155 条特征，完整性检查通过；重新打开后继续生成词典与真实占据地图。此记录证明启动和持久化链路，具体导航成功、重定位及现场安全结论应分别依据对应验收记录。

本轮维护中发现默认特征预算与低分辨率相机不匹配，首关键帧无有效词典、后续无效节点仍保留特征引用，导致重开失败。经明确授权，两个刚创建的仿真故障样本保存在同卷 `/data/maps/rtabmap.db.20260908T144216Z.unusable` 及 `/data/maps/rtabmap.db.20260908T144616Z.unusable`；对应 `maintenance-*.json` 保存原因、原表计数、字节数与 SHA-256。第一个样本 SHA-256 为 `04d97ee48fa65d4092069d95b76212883f721d3e066f4986c1bedb05d9400d93`，第二个为 `d20a9eaa3c0781e93e7a33d99b9a0dc08f12bfe707345b1d921cafdfe6ae3b04`。原样本未删除；这次人工维护不是服务启动时的自动清库逻辑。修复依据为 [RTAB-Map 0.22.1 冷启动特征门槛](https://github.com/introlab/rtabmap/blob/0.22.1/corelib/src/Memory.cpp#L4762-L4767)，同时保留坏签名拒绝与正常退出保存。

官方依据：[RTAB-Map ROS 2](https://github.com/introlab/rtabmap_ros/tree/ros2)、[官方 Nav2 RGB-D 示例](https://github.com/introlab/rtabmap_ros/blob/ros2/rtabmap_demos/launch/turtlebot3/turtlebot3_sim_rgbd_demo.launch.py)、[Jazzy Nav2 参数](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_bringup/params/nav2_params.yaml)、[OSRF 官方 ROS 镜像](https://hub.docker.com/_/ros)。本仓库配置使用这些公开接口，未复制预制环境地图。
