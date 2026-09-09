# 四房间家庭场景操作指南

这份指南用于在没有实机时验证家庭场景的传感器输入、房间路线、自然语言解析和可恢复步骤。场景包含客厅、走廊、厨房、卧室和卫生间，机器人只通过头部与底盘 RGB-D、底盘里程计和自身状态工作。房间名称是规划标签，不会作为相机“看到的事实”写入重建。

## 启动

先停止占用默认端口的旧仿真，再启动家庭 RGB-D Runtime：

```bash
make build
bash scripts/home-slam-stack.sh restart --sim-port 51051 --agent-port 8878
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

## 当前边界

家庭模型用于房间级导航与传感器观测验收，故意没有桌面物体目录；它不会凭空生成杯子、瓶子或可抓取目标。要在家庭场景加入“去厨房拿杯子”，还需在真实 RGB-D 上实现物体检测、三维重建、抓取策略、碰撞规划和动作后验证，并把这些能力注册到同一 `robot.profile.v1`。

本机没有客户实机、真实底盘制动和家庭地图采集结果。家庭仿真通过只能说明代码链路和输入合同正确，不能说明实际 XLeRobot 在客户房屋中可以安全运行。
