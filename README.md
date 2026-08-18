# Tangying Robot Agent OS

Tangying 是一个本地优先的桌面机器人 Agent。用户只需在笔记本安装 Local Agent、配置自己的 OpenAI 兼容 LLM API，再配对一台树莓派机器人；任务、审批、执行、事件、配置和界面全部在用户本机运行，不需要云端控制平面、Docker 或 PostgreSQL。

```text
浏览器
  -> 笔记本 Local Agent（单个 Go 进程）
       - 本地 Console 与 HTTP API
       - LLM/确定性意图解析和任务编排
       - 审批、执行、事件与 SQLite 持久化
       -> 用户选择的 OpenAI 兼容 LLM API
       -> mTLS gRPC
            树莓派 Robot Runtime（单个 Python 服务）
              - 能力、观测、确定性安全检查
              - 有界动作执行、看门狗、取消和急停锁存
              -> USB -> XLeRobot 控制板与舵机
```

云端控制平面已退出默认产品。系统的当前边界见[架构说明](docs/architecture.md)；[分层 Runtime/Middleware 规范](docs/superpowers/specs/2026-08-18-layered-runtime-middleware-design.md)、[改造计划](docs/superpowers/plans/2026-08-18-layered-runtime-middleware.md)、此前的[本地优先规范](docs/superpowers/specs/2026-08-18-local-first-runtime-design.md)和[实施计划](docs/superpowers/plans/2026-08-18-local-first-runtime.md)均是长期保留的开发设计资产。基础设施扩展遵循[Middleware 适配指南](docs/middleware.md)。

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
  --data '{"request":"把红色杯子放进右侧收纳盒","adapter":"direct"}' \
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

## 开发验证

```bash
make setup
make generate-check
make build
make test
make lint
make sim2real-check
```

更多资料：[协议不变量](docs/protocols.md)、[Agent 与 Sim2Real](docs/agent-v1.md)、[Middleware](docs/middleware.md)、[LLM 编排](docs/orchestration.md)、[Console](docs/user-console.md)、[树莓派快捷部署](docs/install/robot-pi-quick.md)。
