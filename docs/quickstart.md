# 仿真开发快速上手

> 本页是轻量仿真入口。新开发者先看[开发者快速上手](development/getting-started.md)与[开发原则](development/principles.md)。云端 Fleet、Local Brain、RoboCasa、用户端和机器人实机的统一步骤见[生产快速上手](production/quickstart.md)。

本页操作适用于 `v0.3.0`。首次检出使用 `git clone --branch v0.3.0 https://github.com/SUSTechWLA/tangying-robot-agent-os.git`，进入项目目录后选择下面一条路线。发布结果见 [v0.3.0 记录](releases/v0.3.0.md)；命令和验收条件本身不代表某次运行已经成功。

## 固定工位闭环

```bash
make setup
make rgbd-start
make sim-status
```

浏览器打开 `http://127.0.0.1:8787/`。后台 observer 会在审批前发布头部 RGB-D；页面可选彩色、深度和观测点云。数据不可用时显示未知，不用全景或静态桌面补齐。提交并审批：

```text
把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来
```

预期任务为 `SUCCEEDED`，最终 `placements` 包含 `red-cup: right-bin` 和 `blue-bottle: front-tray`。查看日志、重启和停止：

```bash
make sim-logs
make rgbd-restart  # 重置模拟工位，先结束当前任务
make sim-stop
```

PID、日志和 Local Agent 数据分别位于 `artifacts/sim-stack/run`、`artifacts/sim-stack/logs` 和 `artifacts/sim-stack/local-agent`。`demo` 仍用于随机 loopback 端口上的短暂自动清理验收。

只检查依赖：

```bash
bash scripts/demo.sh --check
```

参考 RGB-D 工位仅支持红杯、蓝瓶和三个容器；识别来自颜色/深度与已配置几何。旧真值调试仍可通过 `bash scripts/sim-stack.sh start --perception ground-truth` 启动，已有栈须先停止或显式 restart 切换。两者不能混用验收结论。

`make rgbd-start` 与 `make rgbd-restart` 显式声明 `--scene tabletop`。直接调用 `scripts/sim-stack.sh` 时，省略 `--scene` 会沿用 `run/stack.env` 记录的上一次场景（脚本会打印 `reusing recorded scene ...`）；刚跑过家庭或导航场景后只写 `--perception rgbd`，可能重新打开不配置桌面物体的场景，固定工位任务随即在绑定阶段失败。任务报 `grounding absent` 时按[任务一开始就失败：找不到物体](install/troubleshooting.md#任务一开始就失败找不到物体)排查层次，不要改解析器或物体匹配。

## 离桌导航与完整任务

移动路线需要 Docker Compose。MuJoCo 和 Local Agent 在宿主机运行，RTAB-Map / Nav2 使用独立 Linux 容器。固定工位任务结束后，执行：

```bash
make sim-stop
make navigation-start NAVIGATION_ARGS='--build --mode mapping'
make navigation-status
```

移动配置的底盘初始世界坐标为 `Y=-0.60 m`，工位接近目标为 `Y=0.05 m`，名义位移约 65 cm。此时仍只从机载头部／底盘 RGB-D 形成感知与地图，底盘与关节反馈提供机器人本体状态。`make rgbd-start` 的固定工位默认位置保持不变。

打开 `http://127.0.0.1:8787/`，确认双相机有新画面，等待地图和定位就绪，再生成并批准双物品任务。每个物品的分解为“观察 → 目标确认 → 导航 → 到位重观测 → 抓取规划 → 拿取 → 抓取验证 → 放置 → 稳定放置验证”，两物品共 18 步。导航结束必须根据新的相机数据重新测量物品；图上路径或动作回执不能单独证明已到达。

也可以由脚本创建、批准和检查同一条仿真任务：

```bash
.venv/bin/python scripts/run_navigation_acceptance.py \
  --output artifacts/acceptance/navigation-v0.2-run-1
```

该脚本要求当前现场仍位于离桌初始点，输出目录不存在，导航状态为真正的 RTAB-Map 定位就绪。它使用现有服务，不启动或重置世界，不删除地图或 journal。验收条件包括首段通过 Nav2 实际移动至少 60 cm、第二段在同一位置通过新鲜定位确认、18 个唯一步骤的原始观测关联、历史 RGB/depth 哈希、到位后的新观测，以及杯和瓶分别三帧稳定放置。恢复时可以额外刷新只读步骤。6 次物理工具调用各确认一次，其中第二次导航调用仅确认到位；执行运动的工具步骤是首次导航与四次拿取/放置。成功结果写入 `summary.json`，两次导航调用分别保存完成来源；异常会尽力取消本次任务并保留 `failure.json`、最后任务状态与原始证据，收尾失败也会记录。不能把脚本存在当作验收通过。

长暂停使用另一次新现场和新输出目录：

```bash
# 当前任务结束后重置仿真现场，持久地图继续保留
make navigation-restart NAVIGATION_ARGS='--mode mapping'
make navigation-status
.venv/bin/python scripts/run_navigation_acceptance.py \
  --output artifacts/acceptance/navigation-v0.2-pause-1 --pause-seconds 65
```

脚本在导航运行时提出安全暂停，等待工具完成并进入 `PAUSED`，保持现场运行 65 秒后显式继续。它测试同任务的长暂停，不等于同时重启 Runtime 或复现实机断电。只重启 Agent 和未知动作结果测试使用下文链接的[恢复指南](development/single-robot-loop.md#暂停进程中断与显式继续)。

```bash
make navigation-logs
make navigation-stop
```

已保存地图定位可在完成对应地图配置后使用 `--mode localization`；建图与定位模式都不会自动删除已有地图。完整配置、地图保存和实机接入见 [RTAB-Map / Nav2](development/rtabmap-navigation.md)。

## 语义工具策略训练

此处 NumPy Q-learning 学习的是有限状态下的语义工具顺序，不是视觉模型或物理关节策略，不能作为已训练实机模型使用。

训练模块学习 `observe_scene`、grounding、抓取、验证、放置和恢复等离散工具的调用顺序；审批、deadline、lease、幂等键和 safety profile 仍由 Agent 的确定性代码生成。

```bash
.venv/bin/python scripts/train_semantic_policy.py train --episodes 1000 --seed 7 \
  --output artifacts/training/semantic-policy.json
.venv/bin/python scripts/train_semantic_policy.py evaluate \
  --checkpoint artifacts/training/semantic-policy.json --episodes 100 --seed 1007 \
  --min-success-rate 0.90
```

仿真使用固定官方 XLeRobot 模型提交 `3d14695e40c9c68229c0aacffca6053c75cd3eb6`，用于可重复的语义闭环；它不是最新双轮实机的标定数字孪生。

## 分终端调试

```bash
# 终端 1：MuJoCo Robot Runtime
.venv/bin/python -m tangying_sim.server --listen 127.0.0.1:50051 --seed 7 --perception rgbd
```

```bash
# 终端 2：Local Agent；明文 gRPC 只允许仿真
go run ./cmd/local-agent --dev-insecure --robot-safety-profile simulation \
  --listen 127.0.0.1:8787 \
  --robot 127.0.0.1:50051 \
  --data-dir ./artifacts/local-agent
```

浏览器打开 `http://127.0.0.1:8787`，或调用本地 API：

```bash
TASK_ID=$(curl -fsS -X POST http://127.0.0.1:8787/v1/tasks \
  -H 'Content-Type: application/json' \
  --data '{"request":"把红色杯子放进右侧收纳盒","adapter":"mujoco"}' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -fsS -X POST "http://127.0.0.1:8787/v1/tasks/$TASK_ID/approve"
curl -fsS "http://127.0.0.1:8787/v1/tasks/$TASK_ID"
```

## 仿真与实机切换

Agent 代码不感知具体机器人：Local Agent 只依赖 `edge/runtime` 语义接口，仿真与 XLeRobot 通过同一个 Robot Runtime gRPC 协议连接。上层任务语义保持不变，但实机必须先完成[Sim2Real](sim2real/README.md)的硬件、感知、策略、标定与现场授权，不能只改配置后直接运行：

| 环境 | Local Agent 启动 | 任务 adapter | 安全 profile |
| --- | --- | --- | --- |
| 仿真 | `--dev-insecure --robot 127.0.0.1:50051` | `mujoco` | `simulation` |
| 实机 | `--robot xlerobot.local:50051` 与 mTLS 证书 | `xlerobot_direct` | `desktop_standard` |

任务中的 adapter 会与连接到的 RuntimeInfo.adapter 强制核对。如果浏览器选择 XLeRobot 但 Local Agent 实际连着 MuJoCo，任务会失败关闭，不会把仿真结果误报成实体成功。

## 开发验证

```bash
make setup
make generate-check
make build
make test
make lint
.venv/bin/python scripts/run_simulation_acceptance.py --episodes 30 --seed 20260817
```

真实机器人必须使用配对生成的 mTLS 文件，不能使用 `--dev-insecure`。
