# 仿真开发快速上手

README 的一键路径是：

```bash
# 开发笔记本
./install.sh sim --yes
./bin/robot-agent demo
```

`demo` 使用随机空闲端口、固定随机种子和临时状态目录，依次启动开发云端、MuJoCo Robot Gateway 和 Local Agent，创建并审批“把红色杯子放进右侧收纳盒”，断言最终状态为 `SUCCEEDED` 后清理所有子进程。只检查依赖可运行：

```bash
./bin/robot-agent status sim
bash scripts/demo.sh --check
```

## 分终端调试

先完成安装，再开三个终端：

```bash
# 终端 1：MuJoCo Robot Gateway
.venv/bin/python -m tangying_sim.server --listen 127.0.0.1:50051 --seed 7
```

```bash
# 终端 2：内存存储的开发云端
go run ./cmd/cloud-control-plane --dev --listen 127.0.0.1:8080
```

浏览器打开 `http://127.0.0.1:8080`，或用 API 创建并审批任务：

```bash
TASK_ID=$(curl -fsS -X POST http://127.0.0.1:8080/v1/tasks \
  -H 'Content-Type: application/json' \
  --data '{"request":"把红色杯子放进右侧收纳盒","adapter":"mujoco"}' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -fsS -X POST "http://127.0.0.1:8080/v1/tasks/$TASK_ID/approve"
```

```bash
# 终端 3：仅仿真允许明文 gRPC
go run ./cmd/local-agent --dev-insecure --once \
  --cloud http://127.0.0.1:8080 \
  --robot 127.0.0.1:50051 \
  --data-dir ./artifacts/local-agent
```

查询结果：

```bash
curl -fsS "http://127.0.0.1:8080/v1/tasks/$TASK_ID"
```

## PostgreSQL 路径

```bash
docker compose --env-file deploy/config/cloud.env.example \
  -f deploy/docker-compose.yml up --build
```

Compose 默认只把云端和 PostgreSQL 绑定到 loopback。结束时运行：

```bash
docker compose --env-file deploy/config/cloud.env.example \
  -f deploy/docker-compose.yml down
```

## 开发测试

```bash
make setup
make generate-check
make test
make lint
.venv/bin/python scripts/run_simulation_acceptance.py --episodes 30 --seed 20260817
```

仿真允许明文连接仅限 `--dev-insecure` 路径；真实机器人必须使用配对生成的 mTLS 文件。
