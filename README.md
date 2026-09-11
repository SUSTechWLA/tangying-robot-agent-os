# 躺营 · Tangying Robot Agent OS

Tangying 把自然语言任务、机器人工具、世界观测和结果验证连接起来：Agent 理解与编排，Edge/Runtime 执行受约束动作，Harness 根据新鲜环境证据确认完成。产品目标是完成现场集成后的“联网即用”：联网部署使用 Fleet；无网络使用独立 Local Brain。

**v0.5.0 交付可随时回溯的任务全过程回放，并保持 v0.4.0 的标准机器人工具层。** 27 个工具覆盖底盘、机械臂、夹爪、感知、复合技能与安全，LLM 可直接 function calling 调用，而执行仍走既有的 `ExecuteSkill` 通道与安全监督；工作台可以把任意任务的拆解、工具调用、逐步证据与不一致项一屏复盘。物理写工具仍以命令后的新鲜观测判定完成，返回码本身不算完成。仓库同时提供确定性仿真策略、双 RGB-D、RTAB-Map / Nav2、实机驱动接入和验收工具；没有随仓库交付适配所购 XLeRobot 的已训练生产策略，也没有真实机器人的现场验收结论。软件正式版本与实机放行分别管理，发布身份与本版结果见 [v0.5.0 发布记录](docs/releases/v0.5.0.md)，现场限制见 [V1 当前状态](docs/production/v1-release-status.md)。

离线 Fleet 交接演示入口仍为 `./scripts/fleet-sim.sh handoff`；正式部署使用 Compose Fleet。

## 代码放在哪台机器上

仓库同时包含云端、机器人端与开发机三部分代码。部署文件已经按目标分目录，源码归属见[部署目标与代码归属](docs/deployment.md)，部署文件清单见 [`deploy/README.md`](deploy/README.md)。

| 目标 | 运行内容 | 源码 | 部署文件 | 入口 |
| --- | --- | --- | --- | --- |
| 云端 Cloud | Fleet 控制面、MySQL、Redis、nginx mTLS | `cmd/fleet-control-plane/`、`fleet/` | [`deploy/cloud/`](deploy/cloud/) | `./scripts/fleet-up.sh up` |
| 机器人端 Robot | Edge Worker、ROS 2 网关与安全监督、xlerobot 驱动、导航栈 | `cmd/edge-worker/`、`edge/`、`robot/`、`policy/sidecar/` | [`deploy/robot/`](deploy/robot/) | `./install.sh robot-pi` |
| 本地单机 Local | Local Agent、工作台控制台、任务账本 | `cmd/local-agent/`、`cmd/robot-agent/`、`console/`、`web/`、`internal/` | [`deploy/local/`](deploy/local/) | `./install.sh local` |
| 仿真 Simulation | MuJoCo / Gazebo / RoboCasa 验证环境（不是交付目标） | `sim/` | — | `./install.sh sim` |

`agent/`、`core/`、`orchestration/`、`tasks/`、`skills/`、`middleware/`、`proto/`、`gen/` 是三个目标共用的运行时与协议代码，不随部署目标拆分。

一键启动全部组件（默认只起仿真与本地控制台，云端与导航按需加入）：

```bash
./scripts/start-all.sh up                       # 仿真 + Local Agent + 控制台
./scripts/start-all.sh up --demo                # 起来后再跑一次任务演示
./scripts/start-all.sh up --with-cloud --with-fleet-sim
./scripts/start-all.sh status
./scripts/start-all.sh down
```

`start-all.sh` 只按顺序调用各目标已有的生命周期脚本并汇总健康状态，不重复实现启动逻辑；也可以 `make up` / `make down` / `make stack-status`。

## 我能做什么

| 你现在要做什么 | 从这里开始 |
| --- | --- |
| 刚买 XLeRobot，准备安装与实验 | [购机后 Sim2Real 上手](docs/sim2real/README.md) |
| 搞清楚哪部分装云端、哪部分装机器人 | [部署目标与代码归属](docs/deployment.md) |
| 接入其他机械结构、传感器或厂商机器人 | [异构机器人适配器开发](docs/development/robot-adapters.md) |
| 让 LLM 通过 function calling 调用机器人 | [机器人工具层](docs/development/robot-tool-layer.md) · [`tools.json`](tools.json) |
| 让外部 Agent 通过 MCP 使用机器人系统 | [MCP 安装与工具说明](robot/mcp/README.md) |
| 使用双相机建图、定位与导航 | [RTAB-Map / Nav2 接入与 Sim2Real](docs/development/rtabmap-navigation.md)；[Gazebo 家庭场景](docs/guides/gazebo-house-operations.md) |
| 验证客厅、厨房、卧室、卫生间家庭路线 | [四房间家庭场景操作](docs/guides/home-scene-operations.md) · [家庭 Sim2Real](docs/guides/home-sim2real.md) |
| 新加入项目，准备开发 | [开发者快速上手](docs/development/getting-started.md) → [开发原则与代码地图](docs/development/principles.md) |
| 提交改动、准备发布 | [分支与发布规范](docs/development/branching.md)：`main` 是最新可发布状态，发布打 `vX.Y.Z` 标签，不建长期版本分支 |
| 操作工作台、查任务与机器人 | [工作台使用说明](docs/user-console.md) |
| 复盘一次任务到底怎么执行的 | [任务全过程回放](docs/frontend/console-v1.md#任务全过程回放) |
| 测试自然语言、理解当前能力 | [任务评测与改进记录](docs/development/natural-language-evaluation.md) → [Agent 契约](docs/agent-v1.md) |
| 部署 Fleet、查 API 或排故 | [部署与运维手册](docs/production/README.md) |
| 查找其他说明或旧设计 | [完整文档索引](docs/README.md) |

## 先跑通单机器人 RGB-D 闭环

首版以一个机器人完成已配置工位的任务为目标。用户路线展示机器人相机的彩色、深度及观测点云；多机器人接口继续保留，后文双机演示属于扩展验证。操作与边界见[单机器人 V1](docs/production/single-robot-v1.md)，实现原理见[RGB-D 闭环](docs/development/single-robot-loop.md)。

开发基线为 Go 1.26、Python 3.11、MuJoCo 3.11.0、Node.js 22（含 npm）、Git 和 Make。`make setup` 安装 Python、Go 与锁定的前端依赖。主 Python 环境与 RoboCasa 环境分开；无需 LLM API Key 即可运行已支持的确定性任务。按正式标签检出，避免获取其他开发线：

```bash
git clone --branch v0.5.0 https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
make setup
make build
make rgbd-start
```

打开[本地工作台](http://127.0.0.1:8787/)，确认 RGB-D 来源和相机画面，再输入：

```text
把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来
```

Local 模式先创建任务、检查理解与步骤，再批准执行。预期结果是任务 `SUCCEEDED`，红杯在 `right-bin`，蓝瓶在 `front-tray`。首次依赖安装可能较久；分终端调试见[轻量仿真指南](docs/quickstart.md)。任务一开始就失败时，先读事件里的 `observed=` / `visible=` 诊断（它会说明当前场景看不到哪些已配置物体），再按[任务找不到物体](docs/install/troubleshooting.md#任务一开始就失败找不到物体)排查，不要先改解析器。

`make rgbd-start` 与 `make rgbd-restart` 显式启动固定工位 `--scene tabletop`；直接运行生命周期脚本时省略 `--scene` 会沿用上一次记录的场景（例如家庭路线留下的 `home`），那种场景不配置桌面物体，固定工位任务必然找不到物体。

参考场景只识别红杯、蓝瓶和三个容器，使用颜色/深度几何与已配置尺寸，并非通用视觉模型。`make rgbd-restart` 重置仿真现场，有持物任务时不要用它模拟 Agent 恢复。`make sim-start` 保留旧真值调试模式，不能作为相机感知验收。

```bash
make sim-status
make sim-logs
make sim-stop
# 一次性运行并自动清理的命令行演示
make demo
```

## 从离桌位置导航后完成任务

这条路线需要 Docker Compose。先结束当前任务并停止固定工位，再启动带导航的双 RGB-D 仿真：

```bash
make sim-stop
make navigation-start NAVIGATION_ARGS='--build --mode mapping'
make navigation-status
```

工作台仍在 `http://127.0.0.1:8787/`。移动配置将底盘初始化在世界坐标 `Y=-0.60 m`，接近目标为 `Y=0.05 m`，名义导航位移约 **65 cm**；默认 `make rgbd-start` 仍是已经就位的固定工位。等地图与定位就绪，再输入上面的双物品指令并检查分解：每个物品依次经过观察、目标确认、导航、到位重观测、抓取规划、拿取、抓取验证、放置、稳定放置验证，共 **18 个工具步骤**。

在刚初始化的导航现场，可用以下脚本自动创建并批准同一条仿真任务，保存任务与原始验证证据：

```bash
.venv/bin/python scripts/run_navigation_acceptance.py \
  --output artifacts/acceptance/navigation-v0.2-run-1
```

脚本要求底盘实际位移至少 60 cm、18 步均绑定原始观测、历史 RGB/depth 的 SHA-256 相符、到位后重新感知，以及两个物品各连续三帧通过稳定放置确认。检查长暂停时，先结束任务并 `make navigation-restart NAVIGATION_ARGS='--mode mapping'` 恢复离桌初始现场，再用**新的输出目录**运行 `--pause-seconds 65`。脚本使用现有仿真服务，不删除地图或 journal；当前实测与剩余问题以发布记录为准。

```bash
make navigation-logs
make navigation-stop
```

保存地图定位、实机里程计与双相机接线见 [RTAB-Map / Nav2](docs/development/rtabmap-navigation.md)，可复现实验步骤见[仿真快速上手](docs/quickstart.md)。

## 四房间家庭场景与 SLAM 验证

家庭场景包含客厅、走廊、厨房、卧室和卫生间，机器人从客厅离桌位置开始，只能使用头部与底盘 RGB-D 及底盘里程计。`home` 场景用于路线和 SLAM 验证；`home_task` 在同一布局中增加 RGB-D 可见的红色杯子、蓝色收纳盒和参考机械臂，贯通底盘导航与机械臂工具调用。家庭路线计划会在每个房间之间插入 `navigation.navigate` 和 `verify_arrival`，到达确认使用新的底盘相机采集和独立位姿误差，便于暂停、恢复和回溯：

```bash
bash scripts/home-slam-stack.sh restart --sim-port 51051 --agent-port 8878
# 工作台：http://127.0.0.1:8878/
```

完整移动抓取链路使用：

```bash
make build
bash scripts/sim-stack.sh restart \
  --perception rgbd --scene home_task \
  --sim-port 51051 --agent-port 8878
```

在工作台输入“从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅”。系统会依次调用 `observe_scene`、`navigation.navigate`、`verify_arrival`、`resolve_targets`、`plan_grasp`、`manipulation.pick`、`verify_grasp`、`manipulation.place`、`verify_placement`，再导航回客厅并确认到达。每个步骤都绑定同一次 RGB-D 观测；放置确认要求收纳关系在连续三帧中稳定。命令行验收和证据保存：

```bash
.venv/bin/python scripts/run_home_mobile_manipulation_acceptance.py \
  --base-url http://127.0.0.1:8878 \
  --output artifacts/acceptance/home-mobile-run-1
```

普通家庭路线可输入“从客厅出发，去厨房确认一下环境”或“巡检卧室和卫生间，最后回到客厅”。要运行真正的 RTAB-Map/Nav2 家庭建图，使用：

```bash
make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'
make navigation-status
```

`home_task` 仿真用于验证相机合同、路线分解、抓取/放置工具和失败关闭；它不提供全局摄像头，也不把仿真真值写入感知结果。实机仍需真实双 RGB-D、里程计、标定、地图覆盖、刹车/急停、机械臂碰撞边界和至少 30 次受监护路线/抓取验收，详细门槛见[家庭 Sim2Real](docs/guides/home-sim2real.md)与[发布清单](docs/operations/release-checklist.md)。

## 再验证双机器人交接

RoboCasa 使用独立 Conda 环境和本地场景资产。两台 Runtime 必须连接同一个共享世界。

```bash
make robocasa-install
make robocasa-smoke
make robocasa-web-assets
make robocasa-fleet
make robocasa-handoff
```

打开 [Fleet 仿真工作台](http://127.0.0.1:18080/)。该命令使用 Compose Fleet，账号和生成的密码保存在私有 `deploy/cloud/.env`。`admin / admin123` 仅用于独立 E2E/自然语言评测夹具，不是 Compose 默认密码。Fleet 页面“创建并开始任务”会创建并批准任务，动作前请核对页面提示。

兼容旧版本地交接入口：`./scripts/fleet-sim.sh handoff`。它仍只用于开发演示，正式部署请使用上面的 Compose Fleet 流程。

```text
让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区
```

成功必须同时有工具结果、Harness 后置条件、唯一资源 owner 与递增 fencing token。3D 画面和模型输出都不能单独证明完成。每次启动是一个 episode；重复演示前先停止再启动以恢复初始场景。

当前语法也支持“一号机器人”“红色积木”“移到交接点”和同句后续步骤中的“把它”。但此 RoboCasa 场景只验证 `1 号 → 交接区 → 2 号 → 右侧目标区` 的单向流程，未提供任意反向搬运或完成后的通用重新授权；换一种说法不会增加 Runtime 的动作能力。

```bash
bash scripts/robocasa-fleet.sh status
bash scripts/robocasa-fleet.sh stop
```

详细流程、更新任务、故障恢复和签名证据见 [RoboCasa 指南](docs/robocasa-handoff.md)。`make robocasa-acceptance` 只离线重验历史 `round4`，不会为当前代码采集新证据。当前前端包含五个 classic script、十一类资产角色；历史包按其原始版本解释。新采集使用 `make robocasa-acceptance-candidate`，完整审计后才使用 `make robocasa-acceptance-promote`。

正式 Fleet Compose 的仿真路线另提供以下入口，需 Docker 与已配置的证书/网络：

```bash
./scripts/fleet-up.sh up
./scripts/fleet-sim.sh start
./scripts/fleet-sim.sh handoff
./scripts/fleet-sim.sh stop
```

## 系统边界

新增的严格接入模式通过 `robot.profile.v1` 描述型号、关节、末端、传感器和可用工具，通过 `scene.reconstruction.v1` 提交有来源和采集时间的世界坐标三维实体/受限点云。Python SDK 与 Go Agent 双端验证，型号专属驱动和重建算法留在适配器内。旧 XLeRobot/MuJoCo/RoboCasa 继续兼容，但不自动获得新合同的接入认证。统一 MCP 提供能力查询、世界观测和待审批任务提议，执行仍经过现有任务与安全链路。

能力声明里的 `mutates_world` 标记会改变世界的工具。这类工具返回成功只是**触发观察**：Agent 要求一条命令派发之后采集、带观测标识的新鲜证据，缺证据即失败关闭且步骤保持未完成。判定、失败分类与重试上限在 [`core/closedloop`](core/closedloop/closedloop.go)，接入要求见[适配器开发](docs/development/robot-adapters.md#会改变世界的工具必须能被新鲜观测确认)。真值调试运行时不为观测提供标识，因此它的写工具会稳定失败关闭，这是预期行为。

```text
Browser → Fleet API / Task / Coordinator / WorldHub / Harness
                       ↓ mTLS Fleet Link
                   Edge Worker → Policy Provider（候选动作）
                       ↓ mTLS RobotRuntime
                   Runtime / Safety / Journal → Adapter → 机器人或仿真

无网络：Browser → Local Agent + SQLite → 同一 RobotRuntime 契约
```

Fleet 与 Local Brain 是独立部署形态，不提供未经协调的双控制端同时操控或自动主备切换。物理动作的不确定终态只允许查 journal、重建环境事实与人工处理，不能盲目重复动作。持久化和单主恢复的实际范围见[架构](docs/production/architecture.md)与[部署限制](docs/production/deployment-and-capacity.md)。

默认实机路径是 ROS2-free XLeRobot direct backend。集成基线固定 XLeRobot 提交 `3d14695e40c9c68229c0aacffca6053c75cd3eb6` 与 LeRobot `0.4.1`；所购版本必须逐项匹配。当前桌面配置禁用移动底盘，软件默认动作上限不等于经硬件验证的安全值。接线、标定、实体急停、感知、策略和 verifier 应按[购机后上手](docs/sim2real/README.md)逐阶段完成。

## 安装与配置入口

| 角色/形态 | 所在机器 | 指南 |
| --- | --- | --- |
| `sim` | 开发笔记本 | [仿真快速上手](docs/quickstart.md) |
| `local` | 用户笔记本，SQLite 与 Console | [Local 安装与配对](docs/install/local.md) |
| `robot-pi` | Ubuntu 24.04 arm64 树莓派，Runtime 与硬件安全 | [树莓派安装](docs/install/robot-pi.md) |
| Fleet | 云端 Compose，MySQL、Redis、控制面和 nginx | [Fleet 部署](docs/fleet-cloud.md) · [阿里云](docs/install/alicloud-cloud.md) |

安装器按角色分别预览，确认版本、平台与安装位置后才去掉 `--dry-run`：

```bash
./install.sh sim --dry-run --yes
./install.sh local --dry-run --yes
./install.sh robot-pi --dry-run --yes
```

常用 CLI（按所在机器选角色，实机启动前先完成现场检查）：

```text
robot-agent doctor ROLE
robot-agent configure ROLE KEY=VALUE
robot-agent pair ROBOT_HOST --ssh-user USER
robot-agent start ROLE
robot-agent status ROLE
robot-agent logs ROLE --follow
robot-agent stop ROLE
robot-agent demo
```

安装前可运行 `./install.sh ROLE --dry-run --yes`；安装后用 `robot-agent doctor ROLE`、`status`、`logs` 排查。旧 `./install.sh cloud` 只提供迁移提示。LLM 配置在工作台左下角“开发模式 → 开发诊断”，或本地受限配置文件中；确定性模式不需要密钥。详见[配置与安全](docs/production/configuration-and-security.md)。

## 验证与贡献

2026-09-05 自然语言固定评测 13 项符合预期：5 条正向任务完成，6 条在解析阶段拒绝，2 条场景条件检查失败且物体未移动。额外反向搬运探索失败并单独记录。该历史结果只适用于确定性仿真；完整输入、任务 ID、复现脚本与后续缺口见[评测报告](docs/development/natural-language-evaluation.md)。本版变更见[Changelog](CHANGELOG.md)，实际发布验证见 [v0.5.0](docs/releases/v0.5.0.md)。

```bash
make build
make test
make lint
# 修改 proto 后，先准备生成工具，再检查生成文件
make generate-check
```

`make test` 覆盖仓库列出的 Go 包、Python 与全部 Web 单元测试；RoboCasa、故障注入和签名采集各有独立环境与命令。根据改动选择验证范围，准确记录跳过项，见[开发者指南](docs/development/getting-started.md)和[测试与验收](docs/production/testing-and-acceptance.md)。

架构决策保留在 [World/Harness 设计](docs/superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md)与[早期 Local-first 设计](docs/superpowers/specs/2026-08-18-local-first-runtime-design.md)，它们记录了离线形态的演进。计划类实施清单不随发布分发。它们是决策档案，当前操作与验证以本文所链接的当前指南为准。
