# 仿真开发快速上手

## 一键闭环

```bash
./install.sh sim --yes
make build
./bin/robot-agent start sim
./bin/robot-agent status sim
```

浏览器打开 `http://127.0.0.1:8787/`。后台 observer 会在任务审批前发布场景遥测和 PNG 画面；Console 画面不可用时仍显示语义俯视图。提交并审批：

```text
把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来
```

预期任务为 `SUCCEEDED`，最终 `placements` 包含 `red-cup: right-bin` 和 `blue-bottle: front-tray`。查看日志、重启和停止：

```bash
./bin/robot-agent logs sim --follow
./bin/robot-agent restart sim
./bin/robot-agent stop sim
```

PID、日志和 Local Agent 数据分别位于 `artifacts/sim-stack/run`、`artifacts/sim-stack/logs` 和 `artifacts/sim-stack/local-agent`。`demo` 仍用于随机 loopback 端口上的短暂自动清理验收。

只检查依赖：

```bash
bash scripts/demo.sh --check
```

## 语义工具策略训练

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
.venv/bin/python -m tangying_sim.server --listen 127.0.0.1:50051 --seed 7
```

```bash
# 终端 2：Local Agent；明文 gRPC 只允许仿真
go run ./cmd/local-agent --dev-insecure \
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

Agent 代码不感知具体机器人：Local Agent 只依赖 `edge/runtime` 语义接口，仿真与 XLeRobot 通过同一个 Robot Runtime gRPC 协议连接。切换只改变运行配置：

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
