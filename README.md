# 躺营 · Tangying Robot Agent OS

Tangying 把自然语言任务、机器人工具、世界观测和结果验证连接起来：Agent 理解与编排，Edge/Runtime 执行受约束动作，Harness 根据新鲜环境证据确认完成。产品目标是完成现场集成后的“联网即用”：联网部署使用 Fleet；无网络使用独立 Local Brain。

**当前 V1 是仿真与集成候选版。** 仓库提供确定性仿真策略、实机驱动接入、策略 sidecar 和验收工具；没有随仓库交付适配所购 XLeRobot 的已训练生产策略，也没有真实机器人的现场验收结论。代码包版本与 V1 工作范围是两个概念，发布身份、验证结果和剩余限制见 [V1 当前状态](docs/production/v1-release-status.md)。

| 你现在要做什么 | 从这里开始 |
| --- | --- |
| 刚买 XLeRobot，准备安装与实验 | [购机后 Sim2Real 上手](docs/sim2real/README.md) |
| 新加入项目，准备开发 | [开发者快速上手](docs/development/getting-started.md) → [开发原则与代码地图](docs/development/principles.md) |
| 操作工作台、查任务与机器人 | [工作台使用说明](docs/user-console.md) |
| 测试自然语言、理解当前能力 | [任务评测与改进记录](docs/development/natural-language-evaluation.md) → [Agent 契约](docs/agent-v1.md) |
| 部署 Fleet、查 API 或排故 | [部署与运维手册](docs/production/README.md) |
| 查找其他说明或旧设计 | [完整文档索引](docs/README.md) |

## 先跑通本地仿真

开发基线为 Go 1.26、Python 3.11、Node.js 和 Git。主 Python 环境与 RoboCasa 环境分开；无需 LLM API Key 即可运行已支持的确定性任务。

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
make setup
make build
make sim-start
```

打开[本地工作台](http://127.0.0.1:8787/)，确认“仿真环境”和场景观测，再输入：

```text
把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来
```

Local 模式先创建任务、检查理解与步骤，再批准执行。预期结果是任务 `SUCCEEDED`，红杯在 `right-bin`，蓝瓶在 `front-tray`。首次依赖安装可能较久；分终端调试见[轻量仿真指南](docs/quickstart.md)。

```bash
make sim-status
make sim-logs
make sim-stop
# 一次性运行并自动清理的命令行演示
make demo
```

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

```text
让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区
```

成功必须同时有工具结果、Harness 后置条件、唯一资源 owner 与递增 fencing token。3D 画面和模型输出都不能单独证明完成。每次启动是一个 episode；重复演示前先停止再启动以恢复初始场景。

当前语法也支持“一号机器人”“红色积木”“移到交接点”和同句后续步骤中的“把它”。但此 RoboCasa 场景只验证 `1 号 → 交接区 → 2 号 → 右侧目标区` 的单向流程，未提供任意反向搬运或完成后的通用重新授权；换一种说法不会增加 Runtime 的动作能力。

```bash
bash scripts/robocasa-fleet.sh status
bash scripts/robocasa-fleet.sh stop
```

详细流程、更新任务、故障恢复和签名证据见 [RoboCasa 指南](docs/robocasa-handoff.md)。`make robocasa-acceptance` 只离线重验历史 `round4`，不会为当前代码采集新证据。当前前端包含四个 classic script、十类资产角色；历史包按其原始版本解释。新采集使用 `make robocasa-acceptance-candidate`，完整审计后才使用 `make robocasa-acceptance-promote`。

正式 Fleet Compose 的仿真路线另提供以下入口，需 Docker 与已配置的证书/网络：

```bash
./scripts/fleet-up.sh up
./scripts/fleet-sim.sh start
./scripts/fleet-sim.sh handoff
./scripts/fleet-sim.sh stop
```

## 系统边界

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

2026-09-05 自然语言固定评测 13 项符合预期：5 条正向任务完成，6 条在解析阶段拒绝，2 条场景条件检查失败且物体未移动。额外反向搬运探索失败并单独记录。该结果只适用于确定性仿真；完整输入、任务 ID、复现脚本与后续缺口见[评测报告](docs/development/natural-language-evaluation.md)。最新变更见[未发布记录](CHANGELOG.md#未发布--v1-集成候选)。

```bash
make build
make test
make lint
# 修改 proto 后，先准备生成工具，再检查生成文件
make generate-check
```

`make test` 覆盖仓库列出的 Go 包、Python 与全部 Web 单元测试；RoboCasa、故障注入和签名采集各有独立环境与命令。根据改动选择验证范围，准确记录跳过项，见[开发者指南](docs/development/getting-started.md)和[测试与验收](docs/production/testing-and-acceptance.md)。

架构决策保留在 [World/Harness 设计](docs/superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md)及[实施计划](docs/superpowers/plans/2026-08-20-distributed-agentos-world-harness.md)；[早期 Local-first 设计](docs/superpowers/specs/2026-08-18-local-first-runtime-design.md)及[计划](docs/superpowers/plans/2026-08-18-local-first-runtime.md)记录离线形态的演进。它们是决策档案，当前操作与验证以本文所链接的当前指南为准。
