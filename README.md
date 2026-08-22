# Tangying Robot Agent OS

Tangying 是一个**云端优先、机器人联网即用**的分布式 Robot AgentOS。用户购买机器人并联网后，机器人通过 mTLS 注册工具目录、观测目录和坐标变换，云端大脑即可接收自然语言、协调多机器人并以版本化环境事实判定任务是否真正完成。Console 实时显示机器人、环境、资源归属与观测健康，支持左键平移、右键旋转、滚轮指针锚定缩放。

无网络环境使用第二部署形态 **Local Brain**：任务、审批、执行和 SQLite 状态留在用户笔记本，不依赖云服务。云端与本地端共用 `world.snapshot.v1`、Robot Runtime、工具目录和 Observation Registry；仿真 Runtime 后续可直接替换为实际 XLeRobot Runtime，而不用改 Agent 任务逻辑。

```text
Cloud Console -> Fleet control plane（单写协调器 + 事件/Outbox + WorldHub）
                    -> mTLS Fleet Link -> 每台机器人 Edge Worker
                    -> Robot Runtime（MuJoCo 或 XLeRobot）
                         - 工具目录 / 观测目录 / fencing / 幂等 / Safety

无网络：Local Console -> Local Brain + SQLite -> 同一个 Robot Runtime
```

当前设计见[云端分布式 AgentOS 与 World/Harness 规范](docs/superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md)和[实施计划](docs/superpowers/plans/2026-08-20-distributed-agentos-world-harness.md)。历史上的[本地优先设计](docs/superpowers/specs/2026-08-18-local-first-runtime-design.md)、[实施计划](docs/superpowers/plans/2026-08-18-local-first-runtime.md)与分层设计文档继续保留，作为 Local Brain 和边界演进记录。

RoboCasa 双 XLeRobot 基线见[RoboCasa 双机器人交接与实机迁移](docs/robocasa-handoff.md)。它已经把中文自然语言、Harness Agent、两个 Edge Worker、两个 Robot Runtime 端点和一个共享 RoboCasa/MuJoCo 世界连成可恢复的完整闭环。

当前仓库已经跑通云端双机器人纵向切片，但尚不等于可直接大规模商用：WorldHub
快照/观测序列的跨重启持久化、leader fencing 与业务提交的同存储原子校验、跨
MySQL/Redis 的资源转移 saga，以及更多真实网络/进程 chaos 仍是生产门槛。当前
实现对这些不确定边界优先失败关闭，详见[分布式架构成熟度](docs/distributed-agentos.md)。

## 先跑通云端双机器人交接

```bash
./scripts/fleet-up.sh up
./scripts/fleet-sim.sh start
./scripts/fleet-sim.sh handoff
# 或一次运行正常流程 + 1 个真实进程断连恢复 + 8 个确定性故障边界：
make fleet-chaos
```

验收任务是：“让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区”。成功必须同时满足工具执行结果、连续稳定的世界观测、唯一资源 owner 和单调 fencing token；只有命令返回成功不会推进云端任务。

## 推荐开发基线：RoboCasa 双 XLeRobot

`datasets/robocasa` 保持为独立上游仓库，RoboCasa 依赖安装在隔离的 Conda 环境中，不污染 AgentOS 主 Python 环境。默认安装使用完成当前场景所需的最小资产；需要完整厨房资产时再执行 full profile。

```bash
make robocasa-install          # 幂等安装 tangying-robocasa 环境
make robocasa-smoke            # 无窗口 RoboCasa / MuJoCo 冒烟
make robocasa-web-assets       # 确定性生成完整厨房与 XLeRobot 本地 GLB
make robocasa-fleet            # 共享世界 + 双 Runtime + 双 Edge + Fleet Cloud
make robocasa-handoff          # 提交中文自然语言交接任务
make test-robocasa-faults      # 8 个边界矩阵 + 2 个真实进程恢复场景
make robocasa-acceptance       # 写出机器可读证据包
```

`make robocasa-acceptance` 的 visual gate 会在启动进程前生成一次性 episode nonce，
并要求 API 世界、页面可见 marker、原始 DOM/请求/帧时间记录及五张 1404×794 PNG
全部绑定到同一 nonce、task、revision 与世界摘要。缺图、黑图、错误 fallback、重定向、
伪造 Harness evidence 或不完整的 fencing/held 轨迹都会 fail closed。

本机浏览器使用 `http://127.0.0.1:18080/`，该端口只绑定 loopback；公网部署仍使用 HTTPS 443。每次 `robocasa-fleet` 启动代表一个确定性 episode，重复演示前先执行 `bash scripts/robocasa-fleet.sh stop` 再启动，以恢复初始方块位置。

浏览器验收的精确入口是：

```bash
make robocasa-web-assets
bash scripts/robocasa-fleet.sh start
open http://127.0.0.1:18080/
```

完整厨房、两台机器人和关节动画来自本地同源 GLB；`WORLD LIVE`、方块位置、资源监护权和 Harness 判决仍只由权威世界事实决定。视觉资产加载失败或模型 revision 不匹配会明确降级到语义 Canvas，不能伪造任务成功。原始 `file://` 页面不是实时服务入口。验收摘要、manifest/network/performance 记录和五张截图写入 `artifacts/robocasa-harness/manual/`。摘要只有在这些文件具有同一 `runId`/`taskId`、截图是可解码 PNG、浏览器快照 revision 与 canonical SHA-256 一致、同源网络成立且实测首交互不超过 5 秒、steady render capacity 不低于 50 FPS 时才通过；capacity 定义为最后一次记录交互 250 ms 之后、最后最多 300 个真实 `renderer.render()` 样本的 `1000 / mean(duration_ms)`。显示器 / 自动化表面的实际 rAF cadence 另行原样记录，不会冒充 50 FPS。runner 启动时会清除旧证据，禁止拼接历史 artifact。

浏览器证据通过 runner 在启动 Fleet 前创建的 `127.0.0.1` 一次性 bearer 接收端提交。接收端规范化 PNG/世界快照，封存原始 DOM、交互、readiness、600 个页面 rAF 时间戳，以及 600 个带浏览器时间戳的 `renderer.render()` duration，并用只存在于临时目录的 Ed25519 私钥签名；仓库中的 `tests/e2e/robocasa_golden_capture_anchor.json` 固定 golden run 的公钥指纹、run/nonce/task 和 envelope 哈希。validator 同时重算签名清单、完整五项禁缓存网络生命周期、图像内容、显示 rAF、steady render mean/median/p90/p95/max，因此替换 artifact pack 内的 JSON、截图或公钥都会失败关闭。

## 5 分钟跑通仿真

支持 macOS 13+ 和 Ubuntu 22.04/24.04，要求 Go、Python 3.11+。安装器会准备其余依赖。

```bash
gh repo clone SUSTechWLA/tangying-robot-agent-os
cd tangying-robot-agent-os
./install.sh sim --dry-run --yes
./install.sh sim --yes
./bin/robot-agent doctor sim
make build
./bin/robot-agent start sim
./bin/robot-agent status sim
```

浏览器打开 `http://127.0.0.1:8787/`。任务批准前即可看到 XLeRobot、桌面、红色杯子、蓝色瓶子、右侧收纳盒和前方交付托盘。输入：

```text
把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来
```

审批后预期任务进入 `SUCCEEDED`，且场景状态显示 `red-cup -> right-bin`、`blue-bottle -> front-tray`。栈的日常操作是：

```bash
./bin/robot-agent logs sim --follow
./bin/robot-agent restart sim
./bin/robot-agent stop sim
```

长期服务使用精确 PID 文件，不会按名称批量终止进程。短暂的自动清理验收仍可用 `./bin/robot-agent demo`。分进程调试见[仿真快速上手](docs/quickstart.md)。

仿真采用官方 XLeRobot 模型提交 `3d14695e40c9c68229c0aacffca6053c75cd3eb6`。它是用于语义闭环的固定版本模型，不是对最新双轮实机逐毫米标定的数字孪生。

语义工具策略训练和验收不要求 GPU：

```bash
.venv/bin/python scripts/train_semantic_policy.py train --episodes 1000 --seed 7 \
  --output artifacts/training/semantic-policy.json
.venv/bin/python scripts/train_semantic_policy.py evaluate \
  --checkpoint artifacts/training/semantic-policy.json --episodes 100 --seed 1007 \
  --min-success-rate 0.90
```

## 安装角色

| 角色 | 机器 | 内容 |
| --- | --- | --- |
| `local` | 用户笔记本 | Local Agent、SQLite、Console、证书和用户服务 |
| `robot-pi` | Raspberry Pi 4/5 | 直连 Robot Runtime、驱动、安全日志和系统服务 |
| `sim` | 开发笔记本 | Local Agent、MuJoCo 和开发依赖 |

```bash
./install.sh local --dry-run --yes
./install.sh robot-pi --dry-run --yes
./install.sh sim --dry-run --yes
```

旧 `./install.sh cloud` 会明确返回迁移提示，不会安装云端组件。ROS 2 代码仅作为可选兼容目录保留，不在默认 XLeRobot 路径中。

## 笔记本安装与配置

```bash
./install.sh local --yes
robot-agent configure local
robot-agent doctor local
```

启动后在浏览器打开 `http://127.0.0.1:8787`。首次页面可填写 LLM provider、API Base URL、模型和 API Key；也可在本地配置中设置：

```text
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://your-provider.example/v1
AGENT_MODEL=your-model
AGENT_API_KEY=...
```

密钥不会发送给树莓派，也不会出现在 Console 状态响应、任务事件或日志中。没有 LLM 或服务不可达时，系统对已支持的任务使用确定性解析器。

完整说明见[笔记本安装、配对与恢复](docs/install/local.md)。

## 树莓派安装和配对

机械接线、12 V 电源、舵机 ID、稳定串口别名和标定必须在首次动作前完成。

```bash
# 树莓派
./scripts/robot-pi-quick-deploy.sh --dry-run
./scripts/robot-pi-quick-deploy.sh
sudo robot-agent doctor robot-pi
```

在笔记本通过 SSH 做一次安全引导；日常运行只使用由配对生成的 mTLS gRPC 连接，树莓派不需要反向连接笔记本。

```bash
# 笔记本：首次 SSH 时人工核对主机指纹
ssh ubuntu@xlerobot.local
robot-agent pair xlerobot.local --ssh-user ubuntu
robot-agent doctor local
```

配对时 CA 私钥和笔记本客户端私钥始终留在笔记本。只有明确轮换信任根时使用 `--new-ca`。

首次物理实验必须遵循[安全检查表](docs/safety-checklist.md)、[实验前检查](docs/install/xlerobot-experiment.md)和[生产就绪判定](docs/production-readiness.md)。软件急停不能替代切断执行器电源的实体急停。

## 启动与操作

```bash
# 树莓派
sudo robot-agent start robot-pi
sudo robot-agent status robot-pi

# 笔记本
robot-agent start local
robot-agent status local
robot-agent logs local --follow
```

浏览器访问 `http://127.0.0.1:8787`，输入自然语言任务，检查生成的目标和计划后审批。Local Agent 会将任务写入本地 SQLite，并通过同一进程排队执行。也可以使用本地 API：

```bash
LOCAL_URL=http://127.0.0.1:8787
TASK_ID=$(curl -fsS -X POST "$LOCAL_URL/v1/tasks" \
  -H 'Content-Type: application/json' \
  --data '{"request":"把红色杯子放进右侧收纳盒","adapter":"xlerobot_direct"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -fsS -X POST "$LOCAL_URL/v1/tasks/$TASK_ID/approve"
curl -fsS "$LOCAL_URL/v1/tasks/$TASK_ID"
```

真实执行缺少感知、策略动作块、标定、证书或安全条件时会失败关闭，不会伪造成功。

## 日常命令

```bash
robot-agent doctor [sim|local|robot-pi]
robot-agent configure [local|robot-pi] KEY=VALUE
robot-agent pair ROBOT_HOST --ssh-user USER
robot-agent start [local|robot-pi]
robot-agent stop [local|robot-pi]
robot-agent restart [local|robot-pi]
robot-agent status [local|robot-pi]
robot-agent logs [local|robot-pi] --follow
robot-agent start|stop|restart|status sim
robot-agent logs sim --follow
robot-agent demo
robot-agent version
```

统一排障见[故障排查](docs/install/troubleshooting.md)。

## Fleet 云端（主要产品）

Fleet 是默认联网部署画像：管理一台或多台公网机器人时，用 Docker Compose
一键拉起云端控制平面（MySQL + Redis + 控制平面 + nginx），配一台浏览器即可登录
云端 Console 操作整个机群。无网络时切换到独立 Local Brain；它不依赖 Fleet，
但消费相同的工具与世界状态契约。

```bash
./scripts/fleet-up.sh up        # 1) 启动云端 (生成凭据/证书/白名单, 等 https://127.0.0.1/ 就绪)
./scripts/fleet-sim.sh start    # 2) 启动两台 MuJoCo 机器人 + 两个 edge-worker
./scripts/fleet-sim.sh handoff  # 3) 跑通共享方块交接闭环
# 4) 浏览器打开 https://127.0.0.1/ 登录 (凭据见 ./scripts/fleet-up.sh env)
```

架构、环境变量、API 表与安全边界见 [Fleet 云端控制平面](docs/fleet-cloud.md)；
论文闭环验证见 [分布式 AgentOS 论文闭环](docs/fleet-paper-loop.md)。

## 开发验证

```bash
make setup
make generate-check
make build
make test
make lint
make sim2real-check
```

更多资料：[协议不变量](docs/protocols.md)、[纯分布式 AgentOS 架构](docs/distributed-agentos.md)、[阿里云 Fleet 一键部署](docs/install/alicloud-cloud.md)、[Agent 与 Sim2Real](docs/agent-v1.md)、[Middleware](docs/middleware.md)、[LLM 编排](docs/orchestration.md)、[Console](docs/user-console.md)、[树莓派快捷部署](docs/install/robot-pi-quick.md)、[Fleet 云端控制平面](docs/fleet-cloud.md)、[分布式 AgentOS 论文闭环](docs/fleet-paper-loop.md)。
