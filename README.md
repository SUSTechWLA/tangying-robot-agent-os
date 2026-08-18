# Tangying Robot Agent OS

面向桌面抓取、搬运、放置闭环的分布式机器人 Agent 系统。用户说“把红色杯子放进右侧收纳盒”后，系统把自然语言转成带审批、租约、幂等和安全约束的技能图；同一套 Robot Gateway 协议可连接 MuJoCo 或 XLeRobot。

```text
云端：自然语言、任务状态、审批、审计
  HTTPS / WebSocket
笔记本 Local Agent：场景落地、策略执行、断线续跑
  Robot Runtime API（能力快照、超时、取消、急停）
    mTLS gRPC
树莓派 Robot Edge：Safety Supervisor、ROS 2 或 XLeRobot 直连、硬件适配
  USB 串口
两块舵机控制板 -> 12 V STS3215 串行总线舵机
```

这里没有独立 STM32 软件端。XLeRobot 官方结构由树莓派通过 USB 连接两块舵机控制板，再由控制板驱动 STS3215；本项目只安装云端、笔记本和树莓派三个软件端。

> 当前 `v0.1.0-rc.2` 已自动验证完整 MuJoCo 闭环。真实机器人端已具备 ROS 2、mTLS、串口、校准和安全门禁，但第一条物理任务仍必须完成急停、标定、感知/策略接入和 30 次硬件验收；项目不会把缺少策略动作的请求伪装成成功。

升级中的 v1 Agent 已增加两类工具（`pick_and_place` 与 `fetch`）、OpenAI 兼容函数调用和 ROS2-free 直连后端。启用 `AGENT_PROVIDER=openai` 后，LLM 会根据自动生成的技能目录自行编排 `taskgraph.TaskPlan`；模型输出失败时自动回退确定性计划，物理安全字段永远由 Robot Runtime 重新生成。MuJoCo 现在覆盖红/蓝/绿杯子、瓶子、积木、左右收纳盒与前置托盘，并支持“先放 A，再把 B 拿过来”这样的有序复合任务。编排质量通过 `/v1/orchestration/metrics` 观察，方法见 [LLM 编排指标与提升](docs/orchestration.md)。

## 5 分钟先跑通仿真

支持 macOS 13+、Ubuntu 22.04/24.04。新环境先安装并登录 [GitHub CLI](https://cli.github.com/)；仓库若为私有，`gh` 会复用登录凭据，不要把 token 写进命令行。

```bash
# 运行位置：开发笔记本
gh auth login
gh repo clone SUSTechWLA/tangying-robot-agent-os -- --branch v0.1.0-rc.2
cd tangying-robot-agent-os
./install.sh sim --yes
./bin/robot-agent doctor sim
./bin/robot-agent demo
```

预期末行类似：

```text
demo succeeded: task=task-... state=SUCCEEDED events=... seed=7
```

这个命令会在随机空闲的本机端口启动云端、MuJoCo 和 Local Agent，提交中文任务、审批、执行、验证 `SUCCEEDED`，随后自动清理进程。需要逐进程调试时看[仿真快速上手](docs/quickstart.md)。

## 选择部署方式

| 安装角色 | 机器 | 首版支持 | 安装内容 |
| --- | --- | --- | --- |
| `sim` | 开发笔记本 | macOS 13+；Ubuntu 22.04/24.04，amd64/arm64 | Go、Python、MuJoCo、开发云端、Local Agent |
| `cloud` | 云主机/服务器 | Ubuntu 22.04/24.04；Debian 12，amd64/arm64 | Docker、PostgreSQL、Cloud Control Plane |
| `local` | 用户笔记本 | macOS 13+；Ubuntu 22.04/24.04，amd64/arm64 | Local Agent、SQLite、配对证书、launchd/systemd user |
| `robot-pi` | XLeRobot 树莓派 4/5 | Ubuntu Server 24.04 arm64 | ROS 2 Jazzy、Robot Gateway、安全监督、XLeRobot 适配器 |

所有角色共用一个入口：`./install.sh ROLE`。先运行 `--dry-run` 可查看操作而不修改机器；加 `--yes` 可用于无人值守的软件安装。Raspberry Pi OS、Windows 原生和独立 STM32 不在首版支持范围；Windows 可用 WSL2 Ubuntu 跑仿真。

## 三端部署

以下每段都标明执行机器。建议三台机器都检出同一发布标签。

### 1. 云端

```bash
# 运行位置：Ubuntu/Debian 云主机
gh repo clone SUSTechWLA/tangying-robot-agent-os -- --branch v0.1.0-rc.2
cd tangying-robot-agent-os
./install.sh cloud --dry-run --yes
./install.sh cloud --yes
curl -fsS http://127.0.0.1:8080/healthz
sudo robot-agent status cloud
```

健康检查应返回 `{"status":"ok"}`。默认只监听 `127.0.0.1`，不会直接暴露公网；当前 API 本身没有公网认证层。跨机器使用前应配置私网或带 HTTPS/认证的反向代理，再在笔记本写入最终 URL。完整说明见[云端安装与恢复](docs/install/cloud.md)。

### 2. 用户笔记本 Local Agent

```bash
# 运行位置：macOS/Ubuntu 用户笔记本
gh repo clone SUSTechWLA/tangying-robot-agent-os -- --branch v0.1.0-rc.2
cd tangying-robot-agent-os
./install.sh local --dry-run --yes
./install.sh local --yes
robot-agent configure local CLOUD_URL=https://robot-cloud.example.com AGENT_ID=my-laptop
robot-agent doctor local
```

安装后服务保持未启动，直到树莓派完成配对。若通过 SSH 隧道试用云端，可把 `CLOUD_URL` 设为隧道的本机地址。详见[笔记本安装与恢复](docs/install/local.md)。

### 3. 树莓派 Robot Edge

先用 Raspberry Pi Imager 写入 Ubuntu Server 24.04 arm64，启用 SSH，并确认树莓派能访问互联网。机械组装、12 V 电源、舵机 ID 和控制板接线必须在断电状态下按 XLeRobot 官方文档完成。

```bash
# 运行位置：XLeRobot 树莓派
gh repo clone SUSTechWLA/tangying-robot-agent-os -- --branch v0.1.0-rc.2
cd tangying-robot-agent-os
./scripts/robot-pi-quick-deploy.sh --dry-run
./scripts/robot-pi-quick-deploy.sh
```

安装器会固定 XLeRobot 上游提交、安装 LeRobot 0.4.1 和直连 Robot Edge systemd 服务，但不会启动电机。需要 ROS2 后端时仍可使用 `./install.sh robot-pi --yes`。继续前必须完成[树莓派快捷部署](docs/install/robot-pi-quick.md)、[硬件准备](docs/install/robot-pi.md)中的稳定串口别名和交互式标定。

## 笔记本配对树莓派

先从笔记本验证普通 SSH 登录。Ubuntu 用户名不是 `ubuntu` 时必须显式传入实际用户名。

```bash
# 运行位置：用户笔记本
ssh ubuntu@xlerobot.local
robot-agent pair xlerobot.local --ssh-user ubuntu
```

配对会在笔记本创建本地 CA 和 90 天叶证书，只把机器人服务端密钥、证书和客户端 CA 复制到树莓派。CA 私钥绝不会离开笔记本；SSH 主机指纹仍按系统默认流程人工确认。只有明确轮换信任根时才运行：

```bash
# 运行位置：用户笔记本；会让旧客户端证书失效
robot-agent pair xlerobot.local --ssh-user ubuntu --new-ca
```

## 第一次启动前：无动作预检

必须先完成[物理硬件安全检查表](docs/safety-checklist.md)，安装能切断执行器 12 V 电源的实体急停，并保证整个试验期间触手可及。

```bash
# 运行位置：树莓派；以下命令不会连接或驱动舵机
sudo robot-agent doctor robot-pi
sudo systemctl status tangying-xlerobot.service tangying-robot-edge.service
```

预检只有在两个稳定串口、真实校准 JSON、mTLS 证书和 Python 集成都有效时才打印：

```text
PASS no-motion Robot Edge preflight complete
```

然后才允许启动服务：

```bash
# 运行位置：树莓派
sudo robot-agent start robot-pi
sudo robot-agent status robot-pi
```

```bash
# 运行位置：用户笔记本
robot-agent start local
robot-agent status local
```

## 第一条桌面任务

首版自然语言解析支持红/蓝/绿杯子与左右收纳盒。先在浏览器打开云端操作页，或使用 API 创建任务；检查目标、场景和安全状态后再审批。

```bash
# 运行位置：能安全访问云端 API 的操作员终端
CLOUD_URL=https://robot-cloud.example.com
TASK_ID=$(curl -fsS -X POST "$CLOUD_URL/v1/tasks" \
  -H 'Content-Type: application/json' \
  --data '{"request":"把红色杯子放进右侧收纳盒","adapter":"xlerobot_ros2"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -fsS -X POST "$CLOUD_URL/v1/tasks/$TASK_ID/approve"
curl -fsS "$CLOUD_URL/v1/tasks/$TASK_ID"
```

真实 XLeRobot 执行还要求本地感知节点发布场景实体，并由已验证策略给出有界 `action_chunk`；缺少任一项会以明确错误停止，不会输出物理成功。首次只使用轻、软、无液体、非尖锐物体，并按照[硬件检查表](docs/safety-checklist.md)完成 30 次验收。

## 日常命令

```bash
robot-agent doctor [sim|cloud|local|robot-pi]
robot-agent configure [cloud|local|robot-pi] KEY=VALUE
robot-agent start [ROLE]
robot-agent stop [ROLE]
robot-agent restart [ROLE]
robot-agent status [ROLE]
robot-agent logs [ROLE] --follow
robot-agent pair ROBOT_HOST --ssh-user USER
robot-agent demo
robot-agent version
```

云端和树莓派系统服务通常需要 `sudo robot-agent ...`；笔记本 Local Agent 不要使用 sudo。遇到问题先看[统一故障排查](docs/install/troubleshooting.md)。

## 安全与版本边界

- 云端只下发高层技能；树莓派最终执行前检查审批、时限、租约、重复命令、动作键和值域。
- ROS 2 发现限制在树莓派本机；笔记本通过 mTLS gRPC 访问 Robot Gateway。
- 桌面配置禁用底盘 `x.vel` 和 `theta.vel`；缺少策略动作、串口、标定或证书时失败关闭。
- 软件急停不能替代切断执行器电源的实体急停。
- 自动化测试只证明仿真闭环；稳定版 `v0.1.0` 必须通过真实硬件断网、急停和 30 次试验。

深入资料：[系统架构](docs/architecture.md)、[协议不变量](docs/protocols.md)、[用户端 Console](docs/user-console.md)、[LLM 编排指标与提升](docs/orchestration.md)、[生产就绪判定](docs/production-readiness.md)、[XLeRobot 边界](docs/xlerobot-setup.md)、[树莓派快捷部署](docs/install/robot-pi-quick.md)、[XLeRobot 实验前检查](docs/install/xlerobot-experiment.md)、[开发与仿真](docs/quickstart.md)。

## 开发验证

```bash
make setup
make generate
make test
make lint
make sim2real-check   # e2e 复合任务 + 30 轮闭环 + 18 组对象/目标矩阵
```

实机快捷部署见 [树莓派快捷部署](docs/install/robot-pi-quick.md)；在树莓派仓库目录执行 `make deploy-robot-pi`。
