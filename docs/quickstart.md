# 仿真开发快速上手

## 一键闭环

```bash
./install.sh sim --yes
./bin/robot-agent demo
```

`demo` 使用随机 loopback 端口和临时状态目录，启动 MuJoCo Robot Runtime 与一个长期运行的 Local Agent，创建并审批任务，等待 `SUCCEEDED` 后清理所有子进程。它不需要 Docker、数据库服务或云端控制平面。

只检查依赖：

```bash
bash scripts/demo.sh --check
```

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
