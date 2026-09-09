# Gazebo Harmonic 家庭场景与自然语言验收

## 为什么选择这套组合

目前没有一个开源项目同时提供“丰富家居任务、ROS 2 原生接口、RGB-D 建图、Nav2 导航和可直接迁移到任意实机”的完整生产栈。躺营采用分层组合：Gazebo Harmonic 作为传感器和底盘动力学的 ROS 2 仿真源，RTAB-Map 负责从 RGB-D/里程计建立地图，Nav2 负责规划和速度输出，Agent 继续负责自然语言分解、审批、恢复和证据。

| 项目 | 适合放在哪一层 | 优势 | 不能直接替代的部分 |
| --- | --- | --- | --- |
| [Gazebo Harmonic](https://gazebosim.org/docs/harmonic/ros2_integration/) | 生产导航仿真 | `ros_gz_bridge` 可把 Gazebo 传感器和控制消息接入 ROS 2；与 [Nav2 Gazebo 流程](https://docs.nav2.org/jazzy/configuration_and_development/first_time_robot_setup_guide/gazebo/)一致 | 家居物体/长任务需要自己建模 |
| [RoboCasa](https://github.com/robocasa/robocasa) | 操作和厨房任务回归 | MuJoCo 物理、家居资产和抓放任务丰富 | 不是 ROS 2 原生 SLAM/Nav2 栈；当前仓库保留其操作回归 |
| [AI2-THOR](https://github.com/allenai/ai2thor) | 语言、房间和交互基准 | 200+ 房间、2600+ 家居物体、RGB-D/分割和大量交互动作 | Unity 离散动作和真值状态不能当作实机传感器 |
| [iGibson](https://github.com/StanfordVL/iGibson) | 研究对比 | 交互式真实房屋和 RGB-D，支持大量场景 | 官方 ROS 示例仍是 ROS1/Noetic 时代，移植到 Jazzy 要单独维护 |
| [BEHAVIOR-1K/OmniGibson](https://behavior.stanford.edu/) | 长时家务 benchmark | 1000+ 家务任务和大量物体/场景 | 依赖 Isaac Sim，资产和许可管理复杂，不作为首个生产后端 |

Gazebo 的关键价值是数据边界与实机一致：ROS 侧只订阅 RGB、米制深度、CameraInfo、点云、相机 TF、`odom`，并向唯一的 `/cmd_vel` 写入速度。Gazebo 内部位姿、房间标签和碰撞真值不会进入 RTAB-Map 或 Agent；这与实机 RGB-D 输入保持同一合同。

## 启动 Gazebo 家庭后端

Docker 镜像包含 ROS 2 Jazzy、RTAB-Map、Nav2、Gazebo Harmonic 的 ROS bridge 和当前导航包。首次运行会构建较大的镜像：

```bash
make gazebo-house-start GAZEBO_HOUSE_ARGS="--build --mode mapping"
make gazebo-house-status
make gazebo-house-logs
```

服务地址默认为 `http://127.0.0.1:18791`，独立使用 ROS domain `62` 和 Compose 项目 `tangying-gazebo-house`，不会干扰现有 MuJoCo 家庭栈。停止时保留地图和任务回执：

```bash
make gazebo-house-stop
```

地图写入 Docker volume 的 `/data/maps/gazebo_house/rtabmap.db`。完成探索并确认地图可重开后，切换定位模式：

```bash
make gazebo-house-restart GAZEBO_HOUSE_ARGS="--mode localization"
make gazebo-house-status
```

`localization` 没有非空地图会拒绝启动。删除卷、放宽未知空间或把 Gazebo 真值地图灌入 Nav2 都不属于生产验收路径。

## 2026-09-10 联调结果

本轮使用当前检出的 `tangying-navigation:dev`，在独立 ROS domain 62 的容器内完成了从空数据库开始的建图复测：RTAB-Map 首帧视觉词典建立，受控 RGB-D 探索后 `knownCells=154035`、`currentFrameWords=55`、`dictionaryWords=5445`，两路相机、里程计和 TF 的新鲜度均通过。保存数据库后切到 `localization` 重启，状态回到 `LOCALIZED`，没有把 Gazebo 位姿真值注入定位。

为让 Nav2 在 CPU 主机上稳定消费同一组 RGB-D 数据，桥接保留用户可查看的 `/camera/{base,head}/points` 彩色点云，同时由 `point_cloud_xyz` 按 4 倍降采样输出 `/camera/{base,head}/nav_points` 给局部体素层；实测降采样点云约 15 Hz，未再出现局部代价地图更新超时。此前用全分辨率点云会因处理抖动触发安全停止，这个分层是性能优化，不是额外的环境真值来源。

在已观测自由空间内发送一个短距离目标，Nav2 action 返回 `SUCCEEDED`；随后以当前 `map→base_link` 和同一时刻的新鲜 RGB-D 状态提交相同目标，返回 `POSE_ALREADY_CONFIRMED`，验证了“执行后重新观测”和重复提交不重复运动。未知或未覆盖的跨房间目标仍按 `allow_unknown=false` 被拒绝，不能把这次局部路线当成完整五房间覆盖。

自然语言全量回归使用现有 RoboCasa 操作后端重新执行 13 个用例：5 条中英文正向指令完成，6 条否定/条件/歧义/不支持请求在批准前返回 422，2 条物体或起点不满足的请求在执行前失败，结果 **13/13 符合预期**。这证明 Agent 的自然语言解析、任务 journal、审批、证据和恢复合同没有被 Gazebo 接入破坏；Gazebo 本身当前负责真实 ROS 传感器、SLAM、Nav2 和导航到达确认，抓取/放置仍由 RoboCasa 回归覆盖。

复现命令：

```bash
make gazebo-house-start GAZEBO_HOUSE_ARGS="--build --mode mapping --port 18810"
make gazebo-house-status
make gazebo-house-restart GAZEBO_HOUSE_ARGS="--mode localization --port 18810"
ROBOCASA_PYTHON=/opt/homebrew/Caskroom/miniconda/base/envs/tangying-robocasa/bin/python \
  .venv/bin/python scripts/evaluate_natural_language.py \
  --output /tmp/tangying-natural-language-run
```

端口、token、地图和任务数据库都是本机私有运行状态；发布日志只记录非秘密指标，不提交 token、私钥或本机证据目录。

## 一次完整的自然语言任务

首个验收任务只要求导航和环境确认，目的是验证 sim2real 的闭环，不假设 Gazebo 能凭空提供抓取策略：

```text
从客厅出发，去厨房确认一下环境，然后回到客厅
```

Agent 应分解成：

1. `observe_scene`：获取当前机器人两路 RGB-D 和本体状态。
2. `navigation.navigate`：将自然语言中的厨房解析为已配置的目标位姿；Nav2 只接受 RTAB-Map 已观测的自由空间。
3. `verify_arrival`：在目标处重新采集 RGB-D、`map→base_link` 和 `odom`，核对目标误差与采集时间。
4. 对客厅重复导航和到达确认。
5. 每个步骤写入任务 journal；暂停、进程重启或输入短暂丢失后从最近安全检查点恢复，不重发已经有可信终态的物理步骤。

这条路线只有在先完成五房间受控探索、地图覆盖目标走廊并且 RTAB-Map 视觉质量就绪后才应通过。`allow_unknown=false` 保持不变；如果厨房仍是未知区域，目标应安全失败并显示 `NAV_MAP_NOT_READY`/Nav2 拒绝，而不是“为了跑通”穿过未知空间。

## Gazebo→ROS 传感器合同

实现位于 `robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf`、`config/gazebo_house_bridge.yaml` 和 `launch/gazebo_house.launch.py`：

```text
/camera/base/rgb/image_raw       sensor_msgs/Image
/camera/base/depth/image_raw     sensor_msgs/Image (32FC1, metres)
/camera/base/rgb/camera_info     sensor_msgs/CameraInfo
/camera/base/points              sensor_msgs/PointCloud2
/camera/head/...                  同上
/odom                            nav_msgs/Odometry
/tf, /tf_static                  相机外参和 odom→base_link
/cmd_vel                         geometry_msgs/Twist (唯一速度出口)
```

RGB-D 图像和点云来自相同 Gazebo 相机帧，彩色/深度尺寸和时间由桥保留。导航副本仍经过完整足迹和未知空间检查；前端可以显示这些实测图像，不显示房屋全知视角。

Gazebo 使用模拟时钟，导航节点的 freshness、速度租约和 RTAB-Map 状态会跟随 ROS clock；真实机器人默认关闭 `use_sim_time`，复用同一个 `navigation.launch.py` 的 `input_mode:=ros`，只替换 topic 和驱动。

## 生产放行边界

以下检查全部通过后，才把 Gazebo 结果标为 `SIMULATION_GO`：

- 两路 RGB-D、CameraInfo、点云、TF 和 `odom` 持续新鲜；图像与点云能在 RViz/控制台对齐。
- 低速走完客厅、走廊、厨房、卧室、卫生间，RTAB-Map 数据库非空，重启后能定位。
- 自然语言路线分解正确；每一段都执行“导航后重新观测”，保存完成来源和原始证据。
- 未知区域、定位丢失、速度租约过期、相机断流和取消都保持停止，任务可恢复且不重复不确定的物理动作。
- 使用 XLeRobot 前，先把 Gazebo 的两个 RGB-D topic 替换成真实 topic，校准 `camera_info`、光学 TF、轮式里程计、实体急停和底盘足迹，再在真实房屋重新建图。

`SIMULATION_GO` 不是 `PHYSICAL_GO`。实机还必须验证制动距离、相机遮挡和低矮障碍、机械臂收臂包络、抓取/放置反馈、断网和掉电恢复。只有这些证据进入发布清单，才可上线无人值守任务。

## 开发者入口

- Gazebo 世界与传感器：`robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf`
- ROS bridge：`robot/ros2_ws/src/tangying_navigation/config/gazebo_house_bridge.yaml`
- Gazebo + 导航组合启动：`robot/ros2_ws/src/tangying_navigation/launch/gazebo_house.launch.py`
- 通用 RTAB-Map/Nav2 启动：`robot/ros2_ws/src/tangying_navigation/launch/navigation.launch.py`
- 生命周期入口：`scripts/gazebo-house-stack.sh`
- 自然语言路线解析：`agent/intent/parser.go` 与 `skills/manipulation/home_route.go`
- 任务恢复和历史证据：`docs/development/observation-evidence.md`、`docs/frontend/console-v1.md`
