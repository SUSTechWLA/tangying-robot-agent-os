# ROS 2 RGB-D 接入

这条接入路径把机器人相机的彩色图、深度图和标定信息送入躺营的统一 RGB-D 感知模块，再通过相同的机器人网关进入 Agent。默认提供的是**相机观测入口**：能返回真实深度计算的世界坐标点云，能够检查数据过期、错位和标定问题。默认工厂没有目标识别模型，也不执行机械臂动作。

要完成单机器人真实取放，部署方还需要把目标检测、动作策略、驱动、急停和执行后验证接到同一个 `PluginBackend`。不能仅根据 ROS action server 在线就开放动作。当前首版不依赖 ROS 才能运行；已有 direct 路径继续可用，ROS 是已有 ROS 相机/驱动用户的另一种接入方式。

## 数据经过哪些模块

```text
机器人 RGB Image + 已对齐 Depth Image + CameraInfo
                 │ 原始采集时间、有限同步队列
                 ▼
RosRgbdInput ── 捕获时刻 world ← camera_optical 的 TF
                 │ RgbdFrame：RGB、米制深度、内参、外参
                 ▼
RgbdPerception(detector)
                 │ scene.reconstruction.v1：world / m / wxyz
                 ▼
PluginBackend → RobotRuntimeService → gRPC → Edge / Agent / 用户工作台
```

源码位置：

- `robot/gateway/tangying_robot_gateway/ros_rgbd.py`：不依赖 ROS 的消息快照、同步、转换和网关组装。
- `robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/rgbd_node.py`：ROS 订阅、TF 查询、独立 ROS context 和后台 executor、默认工厂。
- `robot/gateway/tangying_robot_gateway/rgbd.py`：通用 RGB-D 帧校验、深度反投影与可插拔像素检测器。
- `robot/gateway/tangying_robot_gateway/run_plugin.py`：统一启动命令，复用 mTLS、持久命令日志、独占进程锁和安全停机。

旧的 `tangying_ros_gateway.node:ROSBackend` 仍是历史桥接路径，订阅 `scene_entities` 字符串；它不是本节描述的 RGB-D 入口。不要用旧节点的“服务在线”代替新入口的数据与安全验收。

## 相机数据要求

| 输入 | 接受的内容 | 校验与限制 |
| --- | --- | --- |
| 彩色 Image | `rgb8` 或 `bgr8` | 支持行 padding；转换为 RGB uint8；最大 3840×2160，建议首版从 640×480 开始 |
| 深度 Image | `16UC1` 毫米或 `32FC1` 米 | 正确读取大小端；必须已经对齐到彩色图的分辨率和 optical frame |
| CameraInfo | 对应本次采集的标定 | 默认 `rectified: true` 使用 P 的 3×3 内参，P 平移列必须为零；非 rectified 模式只接受零畸变和标准 K |
| 时间 | Image/CameraInfo header 的 Unix 采集时间 | 三者最大时间差默认 30 ms；不改成接收时间；过期和未来帧拒绝 |
| TF | `world ← profile.frameId` | 查询深度图的精确纳秒采集时刻；缺失或非刚性变换拒绝；无 latest fallback |
| 标定版本 | profile 的 `transformRevision` | 同一次部署内内参不可改变；变更标定需更新版本并重启适配器 |

深度无效值不被补成物体坐标。感知模块只用有效深度生成点云和检测目标位置；没有足够有效像素的目标不能用于任务定位。点云受公共契约限制，最多 4096 点；它是当前相机视野的有限采样，不代表完整场景 SLAM 或稠密三维地图。

`rectified: true` 是部署方对订阅图像的明确保证，不能只是改开关绕过畸变校验。应订阅实际去畸变后的彩色图，并把深度对齐到**该彩色图**。不能把未去畸变彩色图与 rectified 的 P 直接配对。CameraInfo 的 ROI/非平凡 binning 当前要求在上游规范化后再输入。

采集时刻来自 ROS header；目前此桥不支持 `/use_sim_time=true`、从零计时的仿真时钟或未经映射的相机设备时钟。真实机器人先同步机器人与相机驱动使用的系统时间，驱动负责把设备时钟正确转换到 Unix。不要给旧数据重新打当前时间来通过检查。

ROS Image 对光学坐标和采集时间的约定可查 [Image 消息定义](https://github.com/ros2/common_interfaces/blob/jazzy/sensor_msgs/msg/Image.msg)。标定 K/P/畸变的区别见 [CameraInfo 定义](https://github.com/ros2/common_interfaces/blob/jazzy/sensor_msgs/msg/CameraInfo.msg)。TF 的时间参数为零表示最新变换，因此本入口传入实际采集时间，见 [TF Buffer 接口](https://docs.ros.org/en/kilted/p/tf2_ros_py/tf2_ros.buffer.html)。

## 先把相机接进来

以下命令在已经安装 ROS 2 Jazzy 和相机驱动的机器人 Linux 主机上执行。本次开发测试没有连接真实相机，也没有在 macOS 环境安装或运行 ROS 2；这些现场步骤仍需在购买设备后验收。

1. 安装项目 Python 依赖，并让所用 Python 同时能够导入 `rclpy` 和 `tangying_robot_gateway`。ROS 的 Python 扩展必须与解释器 ABI 一致，不能把另一个 Python 版本的虚拟环境直接混用。
2. 在项目的 `robot/ros2_ws` 目录执行 `rosdep install --from-paths src --ignore-src -r -y` 与 `colcon build --symlink-install`，然后 source ROS 和 workspace 的 setup 文件。
3. 启动厂商相机驱动、图像去畸变/深度对齐处理，以及机器人已经标定的 TF 发布节点。相机装在机械臂上时，TF 必须随关节状态更新；固定相机使用经过测量的静态 TF。
4. 复制并修改 `examples/robots/ros_rgbd.profile.json` 和 `examples/robots/ros_rgbd.config.json`，填入真实 robotId、topic、光学 frame 和测量标定版本。示例中的 `replace-with-measured-calibration-v1` 是占位符，不是已经完成标定的证明。
5. 先执行只读检查，确认当前真实相机帧可以通过统一契约，再启动正式网关。

在项目根目录，设置实际配置路径：

```bash
source /opt/ros/jazzy/setup.bash
source robot/ros2_ws/install/setup.bash
export TANGYING_ROS_RGBD_CONFIG="$PWD/examples/robots/ros_rgbd.config.json"
python -m tangying_robot_gateway.run_plugin check \
  --factory tangying_ros_gateway.rgbd_node:create_sensor_backend
```

`check` 会创建 ROS 订阅，默认最多等 5 秒获得有效同步帧，计算一次点云，输出契约检查结果，然后停止并释放这一个相机适配器实例。它不会 arm 或执行机械臂动作。

正式启动复用统一网关，必须指定持久 journal 和实际证书文件：

```bash
python -m tangying_robot_gateway.run_plugin serve \
  --factory tangying_ros_gateway.rgbd_node:create_sensor_backend \
  --listen 0.0.0.0:50051 \
  --journal /var/lib/tangying-robot-agent-os/runtime-journal.json \
  --server-key /var/lib/tangying-robot-agent-os/certs/server.key \
  --server-cert /var/lib/tangying-robot-agent-os/certs/server.crt \
  --client-ca /var/lib/tangying-robot-agent-os/certs/client-ca.crt
```

也可使用同一 ROS console script：`ros2 run tangying_ros_gateway rgbd_gateway serve ...`，其参数与上面相同。只有本机开发测试可以显式选择 `--allow-insecure`；现场联网部署使用 mTLS。不要同时运行另一个占用相同端口/同一机器人执行权的 direct 或旧 ROS 网关。

默认相机工厂仅接受 `observe_scene` 和 `emergency_stop` 两个工具。这里的急停停止本相机观测源，它没有假装控制机器人电机；已经存在的驱动硬件急停仍由驱动负责。相机工厂不宣称具备取放能力，检测器默认为空，点云仍来自真实深度。

用户界面可以显示同一次采集的 RGB 彩图、深度预览和重建点云。`create_rgbd_backend` 每次只读取一个 `RgbdFrame`，将重建与两张 PNG 放入原子的 `ReconstructionCapture`；不会再读下一帧补图。公开 Observation 的 `compressed_image` / `image_media_type` 是 RGB 图，`compressed_depth_image` / `depth_image_media_type` 是同帧深度预览。两图合计最多 2 MiB，过大时需要降低相机输出分辨率。

深度预览使用固定 0.02–5 米色阶：暖色近、冷色远，无效或超范围深度为黑色。它是原始 `depth_m` 的可视化，不是用颜色反算的测距数据；三维重建始终使用原始米制深度。不要将该 RGB8 PNG 当成 16 位毫米深度图用于二次重建。仿真和 ROS 使用同一 `rgbd_images.py` 编码函数。

## 接到能执行任务的机器人

部署适配器定义一个可信的本地 `module:function` 工厂，组装以下对象后返回 `PluginBackend`：

```python
source = RosRgbdSource(robot_profile, ros_config)
perception = RgbdPerception(detector=commissioned_detector)
backend = create_rgbd_backend(
    robot_profile,
    source,
    perception,
    handlers=commissioned_tool_handlers,
    stop=driver.emergency_stop,
    physical_ready=driver.is_commissioned_and_armed,
    state_provider=driver.read_state,
    disconnect=close_source_and_driver,
)
return backend
```

这段代码展示接线关系，`driver`、检测器和工具 handler 是设备集成方的实现，不是本项目提供的通用运动控制器。工厂还要负责失败时关闭已经创建的资源；构造不得自动 arm。`physical_ready` 必须同时检查本地操作员使能、驱动故障、标定、工作区和该工具的调试状态，不能只检查 ROS action server 是否在线。

一个真实取放任务需要这些具体实现：

| 组件/工具 | 必须依据什么完成 |
| --- | --- |
| detector | 当前 RGB 图中的目标像素 mask、稳定 entityId、类别和置信度；三维位置由 mask 内有效深度反投影产生 |
| resolve_targets | 当前重建结果中确实存在的物体与目标容器，处理缺失、歧义和低置信度 |
| plan_grasp | 实际机械结构、目标几何、碰撞/可达性和已调试抓取策略 |
| manipulation.pick/place | 规范 `action_chunk` 与本型号 actionLimits，实际驱动反馈，正确响应取消、lease 到期和急停 |
| verify_grasp/verify_placement | 执行后的新相机观测，必要时融合夹爪/力传感器，检查实际后置条件 |
| recover_to_safe_pose | 经过调试的安全恢复动作；先确认现场状态和本地解锁条件 |

结果格式仍是公共 `Result`，普通已知失败返回明确失败码。已进入物理 handler 后抛异常或返回非法结果，统一 service 会将结果视为未知、停机并持久锁存；不能用一个新的命令自动绕过。SDK 文档中的命令 journal、幂等、审批、租约和取消规则在 ROS RGB-D 输入下继续生效。

默认相机工厂和上面的设备工厂都通过同一个 gRPC/MCP 任务体系接入用户界面。新增 ROS topic 不是直接对外开放机械臂 action 的理由；用户仍提交任务，机器人网关继续做每次执行前的安全检查。

## 故障怎么定位

| 现象/错误 | 先检查 |
| --- | --- |
| `no synchronized RGB, depth and CameraInfo` | 三个 topic 是否实际有消息、QoS 是否兼容、是否来自同一相机、header 是否在最大时间差内 |
| `stale or future dated` | header 是否 Unix 采集时间，主机时钟是否同步，相机断流，处理耗时是否超出 maxAgeMs |
| `capture stamp and declared optical frame` | profile.frameId、Image.frame_id、CameraInfo.frame_id 是否一致；不能用 base_link 代替 optical frame |
| `camera calibration changed` | 图像分辨率/内参是否运行中被修改；恢复固定配置或更新标定版本后重启 |
| `uncorrected camera distortion` | 图像是否真的去畸变，配置 rectified 与 K/P 是否对应 |
| TF 查询失败/版本不匹配 | 采集时刻是否存在 world 到相机的变换、TF buffer 是否足够、机械臂关节状态是否持续发布 |
| 点云可见但没有物体 | 默认工厂没有识别模型；设备工厂检测 mask 是否正确、目标深度是否有效 |
| 相机在线但不能取放 | profile 是否声明工具、handler 是否真实注册、驱动是否本地使能、任务证据是否齐备 |

原始消息畸形会使输入队列失效，不能继续把之前的缓存帧当健康数据。相机恢复后必须出现新的完整同步观测；故障期间正在查询 TF 的旧帧也会被丢弃。普通重复轮询保留同一序列和原始时间，不会制造新的感知证据。

## 验证范围与现场验收

自动测试可在没有 ROS 的开发机上运行：

```bash
python -m pytest robot/gateway/tests/test_rgbd.py \
  robot/gateway/tests/test_ros_rgbd.py \
  robot/gateway/tests/test_ros_rgbd_entrypoint.py
```

它们覆盖真实数组反投影、ROS 字段解码、同步和标定错误、重复/失效帧、故障竞争，以及真实 gRPC 边界上的米制点云与错误阻断。原生 ROS 消息转换代码也参加测试；不把 Python 字段测试等同于 ROS DDS 联调成功。

本次没有完成厂商相机驱动、DDS 网络、实际 optical TF、真实检测模型、碰撞规划、抓取策略或硬件急停的现场验收。上线前在一台已装好的机器人上依次完成：只读相机检查、标尺验证三维坐标和距离、相机断流阻断、单关节低速动作、空载急停/lease/取消、单物体取放与后置条件验证。测试记录应包含实际相机型号、固件、ROS/驱动版本、标定版本、任务 ID 和执行日志。
