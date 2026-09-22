# 默认 Gazebo 仿真

`scripts/sim-stack.sh` 与 `scripts/start-all.sh` 默认使用 Gazebo Harmonic / ROS 2 Jazzy。2026-09-22 验收镜像升级为 Harmonic **8.15.0**、`ros_gz` **1.0.24**，保留 Jazzy 官方兼容组合。引擎启动失败会报错并回滚本次进程，不会自动换回 MuJoCo。需要 Docker Compose、已启动的 Docker daemon，以及 `make setup`、`make build` 生成的宿主依赖。

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

工作台默认 `http://127.0.0.1:8787/`，Robot Runtime `127.0.0.1:50051`。首次安装先运行 `make gazebo-build`，将耗时较长的 ROS/Gazebo 下载和编译与服务就绪等待分开。后续启动按代码摘要自动复用或重建镜像；必要时通过 `SIM_STACK_STARTUP_TIMEOUT=600` 扩大服务等待时间。`make up` 同样默认 Gazebo；其 `--with-navigation` 不会再启动第二套导航容器。

| 场景 | Gazebo 世界 | 用途 |
|---|---|---|
| `home` | 程序化四房间家庭 | RGB-D 探索、地图、导航 |
| `tabletop` | 桌面、动态红杯/蓝瓶、收纳盒/托盘 | 完整工位任务验收 |
| `home_task` | 家庭 + 启动位置处的操作工位 | 导航与工位操作集成 |
| `home_furnished` | 固定版本 AWS Small House + 当前机器人 | 带纹理家具的 ROS 2 系统集成 |

这些是同名场景的 Gazebo 实现，并不与 MuJoCo 的物品、几何布局和任务成功率等价。尤其装修家庭不自动继承 MuJoCo 的 `ceramic-mug` / `kitchen-tray` 工作区。

## 感知和能力边界

装修家庭默认采用 256×192、关闭阴影的 CPU 渲染配置，其余场景为 320×240，内参与世界摘要随实际配置更新。双相机 RGB、深度、重建点云来自 Gazebo 传感器，具有来源、采集时间、模拟时钟序号和世界/标定摘要。默认不提供真值感知。前端切换相机使用同一个 `Observe.source_id` 合同。

现提供 13 个工具：观察、目标解析、抓取规划、吸附抓取、抓持验证、放置、放置验证、预定位、导航、到达验证、关节运动、安全姿态恢复和急停。`RobotProfile.internallyPlannedTools` 显式声明由适配器生成轨迹的工具；其他驱动以及 `arm.move` 继续要求合法的 `action_chunk`，不会放宽已有契约。

四种场景均含一个可重复验收的彩色工位。默认头部 RGB-D 用颜色连通区域、深度和圆柱表面拟合识别 `red-cup`、`blue-bottle`、`right-bin`、`front-tray`；重名、遮挡、过期反馈会拒绝执行。该探测器只用于已标定的这些物体，不是任意家庭物品识别。底盘 IMU 补偿俯仰/横滚，二维里程计提供平移与航向。底盘相机仍用于建图。机器人以收臂姿态出生，头部相机位于底盘上方 1.30 m、俯视 45°；彩色检测只覆盖已标定工位体积，同色家具不会被当作任务物品。放置后和空载导航前均会真实收臂，持物时拒绝通用收臂导航。

抓取模式明确为 `sim_suction`：原生 Gazebo 插件仅在末端距允许物体中心不超过 9 cm 时建立固定物理连接，使用实际关节控制运动，释放后由重力落入容器。没有写入物体位姿、远距离吸附或成功占位。验证独立检查身份、至少 5.5 cm 的抬升、连续稳定、释放、直立及完整容纳。这是简化吸附模型，不声称模拟真实真空压力或精细夹爪接触。

取消/急停会撤销尚未处理的吸附请求并保持已抓物体；持物时通用回安全姿态请求返回 `PAYLOAD_HELD_REQUIRES_PLACE`，避免恢复动作随意丢弃物体。重启保留急停记录；运行结果未知必须先核对物理状态。

`TANGYING_GVF_ENABLED=1` 启用现有证据验证边界。Gazebo 抓放使用明确的 `SimSuctionHolding` / `SimSuctionPlaced` 合约，证据来自带时间和序号的仿真物理状态，不伪造力传感器。建图移动及任意关节轨迹仍需要各自动作合同；严格模式不会把缺合同的运动当成已验证成功。

## 建图和导航

先在工作台 SLAM 页建图，或通过相同的服务 API：

```bash
.venv/bin/python scripts/build_sim_map.py \
  --base-url http://127.0.0.1:8787 \
  --mode explore --max-travel-m 3 --max-legs 1 --timeout 600 \
  --output artifacts/acceptance/gazebo-map-1
```

工位含可见纹理地垫以改善初始视觉特征；纹理不足时导航仍拒绝 `NAVIGATION_NOT_READY`，受深度防撞保护的探索可积累地图。`no_reachable_frontier` 是当前地图里没有可达探索目标，不代表全屋已覆盖。导航目标仍需满足 RTAB-Map 定位、新鲜传感器、Nav2 足迹和未知空间规则；不会为演示成功降低门槛。

Gazebo 使用差速 RPP 控制器；回到原点工位时先在前方 30 cm 的入口对正，再进入工作台，每段保留 Nav2 碰撞检查。抓取、抬升、放置和退臂使用同一已标定机械臂工作空间。严格 GVF 模式会拒绝尚无合同的变更服务（包括 `calibration.run` 和 `mapping.move`），不能把默认模式的服务验收当作严格模式已支持。

默认数据在 `artifacts/sim-stack/gazebo/maps/<scene>/`，工作台与容器共享其中的 `workflow/`。RTAB-Map 数据库进一步按世界摘要分目录，几何/相机配置变化不会静默复用旧数据库。切换场景不删除旧地图或安全 journal；急停锁存也不会通过重启偷偷清除。Gazebo 的 `--seed` 目前仅记录 episode 标识，ROS `GzServer` 没有暴露物理随机种子，不能据此声称位级确定性。

Gazebo 的 Agent 任务与批准记录单独存放在 `<artifacts-dir>/gazebo/agents/<scene>/`，不会恢复旧 MuJoCo 任务或另一场景的任务。原 `<artifacts-dir>/local-agent/` 完整保留，显式切回 MuJoCo 时仍可读取。迁移后工作台任务列表为空是独立场景的正常初始状态；导航 token 与 Agent session 仍分别管理。

## 可复现验收

```bash
# 只检查相机、来源、服务目录和正负到达判定
make gazebo-accept
# 增加真实关节动作和回位
make gazebo-accept GAZEBO_ACCEPT_ARGS=--arm-only
# 现场具备有效地图和安全后退空间后增加 Nav2 实际位移
make gazebo-accept GAZEBO_ACCEPT_ARGS="--motion --navigation-distance=-0.15"
# 工具目录门禁；目录齐全仍不能替代业务任务验收
make gazebo-accept GAZEBO_ACCEPT_ARGS=--require-full
```

`--estop` 会锁存急停，只对可丢弃的验收命名空间使用。原始报告、图片、日志保留在被 Git 忽略的 `artifacts/`，核心方法和汇总见[稳定版本闭环验收](../experiments/2026-09-22-gazebo-stable-integration.md)，此前结果见[迁移验收报告](../experiments/2026-09-22-gazebo-default.md)。

自动轮流启动并验证全部四个场景（独立端口、状态目录；结束后自动停止）：

```bash
.venv/bin/python scripts/evaluate_gazebo_scenes.py \
  --output artifacts/acceptance/gazebo-scenes-1 --estop
```

该基础矩阵检查相机、服务、关节、取消与急停。增加真实自然语言顺序任务与独立物理验收：

```bash
PYTHONPATH=python .venv/bin/python scripts/evaluate_gazebo_scenes.py \
  --output artifacts/acceptance/gazebo-business-1 --business --require-full
```

`--business` 在每个场景执行“红杯放进收纳盒，然后取蓝瓶到托盘”，通过生产 Agent API 创建和批准任务，并从 Gazebo 原生状态独立验证最终结果。矩阵使用独立模型配置，避免读取本机个人 API 设置。`--business` 与 `--estop` 分开运行，因为急停锁存后不可继续抓放。物理失败不自动重试，报告保留原始失败。

单场景可用 `scripts/evaluate_gazebo_business.py --mode task --case place|fetch|sequence`；`--mode rpc` 用于分离驱动问题和 Agent 编排问题。`--oracle-container` 指定本次拥有的仿真容器。输出目录必须不存在，保留每步结果、耗时、RGB/深度及物理证据。

工位任务验收不等于全屋覆盖或任意跨房间任务验收。房间巡检/厨房操作必须使用与活动地图版本绑定的已标定语义目标，不能把启动工位重新命名为厨房。

## 显式保留 MuJoCo 回归

```bash
bash scripts/sim-stack.sh restart --engine mujoco --scene tabletop --perception rgbd
SIM_STACK_ENGINE=mujoco make home-furnished
```

旧 `--home-assets` 仅用于 MuJoCo 转换资源包；Gazebo 使用 `--scene home_furnished`。旧命名空间迁移时可识别记录的装修包，选择相应 Gazebo 场景。PID 和日志保留 `mujoco.pid` / `mujoco.log` 的兼容文件名，实际引擎以 `run/stack.env` 的 `ENGINE`、状态输出和 RuntimeInfo.adapter 为准。

自定义端口：`--sim-port`、`--agent-port`，导航 HTTP 为 `SIM_STACK_GAZEBO_NAVIGATION_PORT`（默认 18791，按命名空间记录并复用）；全部仅绑定回环地址。导航 token 由启动器以 0600 权限生成，不应复制进文档或 Git。
