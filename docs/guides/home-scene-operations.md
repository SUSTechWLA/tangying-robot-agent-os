# 四房间家庭场景与移动抓取操作指南

这份指南用于在没有实机时验证家庭场景的传感器输入、房间路线、自然语言解析、底盘导航、机械臂抓取和可恢复步骤。场景包含客厅、走廊、厨房、卧室和卫生间，机器人只通过头部与底盘 RGB-D、底盘里程计和自身状态工作。房间名称是规划标签，不会作为相机“看到的事实”写入重建。

仓库保留两个家庭场景：`home` 只验证房间路线和到达确认；`home_task` 在同一四房间布局中增加一个由 RGB-D 可见的红色杯子和蓝色收纳盒，用于贯通移动操作闭环。`home_task` 是当前 Agent Harness 的家庭全流程参考场景。

## 启动

先停止占用默认端口的旧仿真，再启动家庭 RGB-D Runtime：

```bash
make build
bash scripts/home-slam-stack.sh restart --sim-port 51051 --agent-port 8878
```

要运行完整的移动抓取任务，显式启动 `home_task`：

```bash
make build
bash scripts/sim-stack.sh restart \
  --perception rgbd --scene home_task \
  --sim-port 51051 --agent-port 8878
```

工作台地址为 `http://127.0.0.1:8878/`。状态与日志：

```bash
bash scripts/home-slam-stack.sh status --sim-port 51051 --agent-port 8878
bash scripts/home-slam-stack.sh logs --sim-port 51051 --agent-port 8878
bash scripts/home-slam-stack.sh stop --sim-port 51051 --agent-port 8878
```

工作台应能看到头部 RGB、深度、观测点云和底盘 RGB-D 来源。底盘画面只显示其相机能看到的近场，不能用头部画面或仿真总览替代。

## 自然语言路线

家庭路线至少包含起点和一个目标房间。当前确定性解析支持：

```text
从客厅出发，去厨房确认一下环境
巡检卧室和卫生间，最后回到客厅
```

计划会生成：初始 `observe_scene`，每一段 `navigation.navigate`，紧跟一次 `verify_arrival`。`verify_arrival` 会重新采集底盘 RGB-D，并把同次采集的底盘位姿、误差、来源和时间写进步骤证据。刷新页面、重启 Agent 或暂停任务都不会把旧画面当成新的到达证明。

路线步骤可以在任意已保存的房间检查点恢复。已完成的导航不会重复发送；只读观察和到达确认会重新采集。导航命令在发送后没有可信终态时保持 `PHYSICAL_OUTCOME_UNKNOWN`，必须人工核对，不能复制任务绕过。

如果导航在发送 Nav2 goal 之前被地图就绪门禁拒绝（例如 `NAV_MAP_NOT_READY`），该步骤会记录为可重试的 `FAILED`，不会伪报物理结果未知；地图恢复后可以从已完成的观察检查点继续。只有已经获得运动授权、但没有可信终态的动作才进入 `PHYSICAL_OUTCOME_UNKNOWN`。

## 完整自然语言移动抓取任务

在 `home_task` 工作台输入：

```text
从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅
```

批准前会显示已识别的起点、目标房间、物体颜色和收纳盒颜色。批准后，Harness 严格按以下独立工具调用执行，每个物理动作都有自己的回执和 RGB-D 证据：

```text
observe_scene
navigation.navigate (客厅 → 厨房)
verify_arrival
observe_scene
resolve_targets
plan_grasp
manipulation.pick
verify_grasp
manipulation.place
verify_placement (inside:kitchen-bin，连续 3 帧)
navigation.navigate (厨房 → 客厅)
verify_arrival
```

脚本会把任务状态、工具事件、每一步的原始 RGB/depth 和快照保存到新目录，方便回溯：

```bash
.venv/bin/python scripts/run_home_mobile_manipulation_acceptance.py \
  --base-url http://127.0.0.1:8878 \
  --output artifacts/acceptance/home-mobile-run-1
```

也可以直接通过 HTTP 创建任务：

```bash
curl -s http://127.0.0.1:8878/v1/tasks \
  -H 'Content-Type: application/json' \
  -d '{"adapter":"mujoco","request":"从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅"}'
curl -s -X POST http://127.0.0.1:8878/v1/tasks/TASK_ID/approve
curl -s http://127.0.0.1:8878/v1/tasks/TASK_ID
curl -s http://127.0.0.1:8878/v1/tasks/TASK_ID/observations?limit=200
```

任务详情中的 `events` 是步骤时间线；`/observations` 返回每个步骤的历史记录，记录详情下的 `rgb`、`depth` 和 `snapshot` 是可下载的原始证据。刷新工作台或只重启 Local Agent 后，已完成的只读步骤可重新采集，已经发送的物理工具不会自动重放；不确定的物理结果必须人工确认。

## 使用 RTAB-Map / Nav2 建图

房间级建图需要 Linux ROS 2 Jazzy、RTAB-Map、Nav2、双 RGB-D 和轮式里程计。家庭场景通过统一入口传递给仿真、Compose 和 launch：

```bash
make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'
make navigation-status
```

`mapping` 会把当前现场 RGB-D 和 `odom` 建成 `/data/maps/home/rtabmap.db`；确认地图、定位和路径覆盖后，再切换：

```bash
make navigation-restart NAVIGATION_ARGS='--mode localization --scene home'
```

`localization` 要求同一现场已经存在非空地图。空白画面、错误 TF、过期相机或没有视觉词典都会保持未就绪。启动进程存在不等于地图可以导航。

本机本轮现场探针（`make navigation-restart --build --mode mapping --scene home`）确认 RTAB-Map 进程持续处理双 RGB-D，地图栅格和 `map→base_link` 时间戳不断更新；加入家庭地毯纹理后当前状态为 `ready=true`，`currentFrameWords=159`、`dictionaryWords=112`、`knownCells=6589`，定位状态为 `MAPPING_ODOMETRY`。自然语言任务的客厅导航和 `verify_arrival` 已成功；继续前往厨房时，Nav2 因全局地图尚未覆盖未知区域而拒绝规划（`allow_unknown=false`）。这是安全门禁的预期行为：应先用受控低速探索覆盖五个房间，确认视觉词典、占据图和定位后再运行跨房间自然语言路线。

2026-09-12 复测同一现象：`ready=true`、`mode=mapping`、`mapRevision=e9726707…`、`poseSource=rtabmap_tf`；客厅 `navigation.navigate` 与 `verify_arrival` 均 CONFIRMED，随后客厅→厨房的 `navigation.navigate` 以 `NAV_FAILED NAV2_ACTION_ENDED` 结束，Nav2 规划器报 `GridBased plugin failed to plan from (-0.00, -1.25) to (2.20, 3.35)`。即“未知区域不进去”的门禁按设计生效，不是回归。

### 建图阶段目前没有自动探索工具

覆盖五个房间这一步**当前需要人工受控驱动**，仓库还没有可用的自动探索入口：

- 工具层注册了 `explore_for` 与 `scan_environment`（见 `tools.json`），但 MuJoCo 家庭运行时不提供对应的执行技能（`/v1/runtime` 只列出 `navigation.navigate` 等 11 个），因此 LLM 目前无法通过工具调用完成建图覆盖。
- 导航桥的 HTTP 接口只有 `GET /v1/navigation/map` 与 `POST /v1/navigation/goals`，没有速度/遥控接口，所以也无法用脚本直接下发低速速度指令。
- 结果是：冷启动后在 `mapping` 模式下只能导航到已覆盖范围，跨房间路线必须先把地图建出来。

补齐这个缺口（frontier 探索技能或受限速度接口）是通往实机的前置工作；在那之前，[`home_task` 场景与 `make home-accept`](#完整自然语言移动抓取任务)是验证任务闭环、工具调用与证据合同的主路径。

## 当前边界

`home_task` 的物体检测使用 RGB-D 颜色和深度几何，仿真只提供已布置的红杯、蓝色收纳盒和参考机械臂控制器；它没有读取 MuJoCo 真值作为观测，也不是通用视觉模型或通用抓取策略。真实 XLeRobot 需要把头部/底盘相机、TF、里程计、机械臂和夹爪驱动接入同一 `robot.profile.v1`，由现场检测器、碰撞规划和动作后验证替换参考实现。

RTAB-Map / Nav2 的家庭 ROS 2 后端目前继续使用 `scene home` 做建图和定位；它和 `home_task` 的 MuJoCo 全流程参考场景共享导航工具与证据合同，但不能把 ROS 地图或 Gazebo 结果直接当作实机抓取放行。实机验收仍需要真实地图覆盖、制动/急停、标定、抓取成功率和长稳测试。

本机没有客户实机、真实底盘制动和家庭地图采集结果。家庭仿真通过只能说明代码链路和输入合同正确，不能说明实际 XLeRobot 在客户房屋中可以安全运行。
