# 默认 Gazebo 仿真

`scripts/sim-stack.sh` 与 `scripts/start-all.sh` 默认使用 Gazebo Harmonic / ROS 2 Jazzy。引擎启动失败会报错并回滚本次进程，不会自动换回 MuJoCo。需要 Docker Compose、已启动的 Docker daemon，以及 `make setup`、`make build` 生成的宿主依赖。

## 启动和切换

```bash
make sim-start
# 首次默认 home；已有命名空间会明确提示沿用上次场景。
bash scripts/sim-stack.sh restart --scene tabletop
bash scripts/sim-stack.sh restart --scene home_task
bash scripts/sim-stack.sh restart --scene home_furnished
bash scripts/sim-stack.sh restart --scene home
make sim-status
make sim-stop
```

工作台默认 `http://127.0.0.1:8787/`，Robot Runtime `127.0.0.1:50051`。首次启动自动构建镜像，代码摘要变化后自动重建。首次安装和下载需要联网，耗时与网络有关；必要时通过 `SIM_STACK_STARTUP_TIMEOUT=600` 扩大启动等待时间。`make up` 同样默认 Gazebo；其 `--with-navigation` 不会再启动第二套导航容器。

| 场景 | Gazebo 世界 | 用途 |
|---|---|---|
| `home` | 程序化四房间家庭 | RGB-D 探索、地图、导航 |
| `tabletop` | 桌面、动态红杯、青色收纳盘 | 相机、关节与操作控制器调试 |
| `home_task` | 家庭 + 厨房桌面物品 | 移动操作开发 |
| `home_furnished` | 固定版本 AWS Small House + 当前机器人 | 带纹理家具的 ROS 2 系统集成 |

这些是同名场景的 Gazebo 实现，并不与 MuJoCo 的物品、几何布局和任务成功率等价。尤其装修家庭不自动继承 MuJoCo 的 `ceramic-mug` / `kitchen-tray` 工作区。

## 感知和能力边界

装修家庭默认采用 256×192、关闭阴影的 CPU 渲染配置，其余场景为 320×240，内参与世界摘要随实际配置更新。双相机 RGB、深度、重建点云来自 Gazebo 传感器，具有来源、采集时间、模拟时钟序号和世界/标定摘要。默认不提供真值感知。前端切换相机使用同一个 `Observe.source_id` 合同。

本轮接入了 `observe_scene`、`navigation.navigate`、`verify_arrival`、`arm.move`、`recover_to_safe_pose`、`emergency_stop` 和 14 个建图/标定服务。关节执行依赖 `/joint_states` 实际反馈，单位为弧度；恢复接口需要调用方提供已经确认安全的 `action_chunk`，并不声称能从任意姿态自动脱困。

`resolve_targets`、`plan_grasp`、`manipulation.pick/place`、`verify_grasp/placement` 尚未在 Gazebo 接通。它们不会以成功占位。`--require-full` 专门检测这类能力缺口；不能把运行时相机就绪当成完整操作任务就绪。GVF 是独立严格模式，目前不能把尚无 GVF 动作合同的建图移动当成已验证物理动作。

## 建图和导航

先在工作台 SLAM 页建图，或通过相同的服务 API：

```bash
.venv/bin/python scripts/build_sim_map.py \
  --base-url http://127.0.0.1:8787 \
  --mode explore --max-travel-m 3 --max-legs 1 --timeout 600 \
  --output artifacts/acceptance/gazebo-map-1
```

纯色家庭初始视觉词典可能不足，导航会拒绝 `NAVIGATION_NOT_READY`；受深度防撞保护的探索可积累地图。`no_reachable_frontier` 是当前地图里没有可达探索目标，不代表全屋已覆盖。导航目标仍需满足 RTAB-Map 定位、新鲜传感器、Nav2 足迹和未知空间规则；不会为演示成功降低门槛。

默认数据在 `artifacts/sim-stack/gazebo/maps/<scene>/`，工作台与容器共享其中的 `workflow/`。RTAB-Map 数据库进一步按世界摘要分目录，几何/相机配置变化不会静默复用旧数据库。切换场景不删除旧地图或安全 journal；急停锁存也不会通过重启偷偷清除。Gazebo 的 `--seed` 目前仅记录 episode 标识，ROS `GzServer` 没有暴露物理随机种子，不能据此声称位级确定性。

## 可复现验收

```bash
# 只检查相机、来源、服务目录和正负到达判定
make gazebo-accept
# 增加真实关节动作和回位
make gazebo-accept GAZEBO_ACCEPT_ARGS=--arm-only
# 现场具备有效地图和前方安全空间后增加 Nav2 实际位移
make gazebo-accept GAZEBO_ACCEPT_ARGS=--motion
# 完整能力门禁：当前缺抓取工具时必须非零退出
make gazebo-accept GAZEBO_ACCEPT_ARGS=--require-full
```

`--estop` 会锁存急停，只对可丢弃的验收命名空间使用。原始报告、图片、日志保留在被 Git 忽略的 `artifacts/`，核心方法和汇总见[迁移验收报告](../experiments/2026-09-22-gazebo-default.md)。

自动轮流启动并验证全部四个场景（独立端口、状态目录；结束后自动停止）：

```bash
.venv/bin/python scripts/evaluate_gazebo_scenes.py \
  --output artifacts/acceptance/gazebo-scenes-1 --estop
```

该矩阵检查相机、服务、关节、取消与急停，不替代完整导航路线或抓取任务验收。

## 显式保留 MuJoCo 回归

```bash
bash scripts/sim-stack.sh restart --engine mujoco --scene tabletop --perception rgbd
SIM_STACK_ENGINE=mujoco make home-furnished
```

旧 `--home-assets` 仅用于 MuJoCo 转换资源包；Gazebo 使用 `--scene home_furnished`。旧命名空间迁移时可识别记录的装修包，选择相应 Gazebo 场景。PID 和日志保留 `mujoco.pid` / `mujoco.log` 的兼容文件名，实际引擎以 `run/stack.env` 的 `ENGINE`、状态输出和 RuntimeInfo.adapter 为准。

自定义端口：`--sim-port`、`--agent-port`，导航 HTTP 为 `SIM_STACK_GAZEBO_NAVIGATION_PORT`（默认 18791，按命名空间记录并复用）；全部仅绑定回环地址。导航 token 由启动器以 0600 权限生成，不应复制进文档或 Git。
