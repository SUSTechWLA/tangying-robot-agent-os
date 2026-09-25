# 角色 Harness 与统一 Docker 镜像：软件验收记录

日期：2026-09-25。设计依据：[本轮 ADR](../superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md)；上一轮规范和结果见[云边 ADR](../superpowers/specs/2026-09-25-cloud-edge-brain-upgrade-adr.md)及[上一轮记录](cloud-edge-upgrade-acceptance.md)。本页随本轮 commit 保留；后续实机认证须另建含设备、镜像和模型哈希的记录。

## 本轮软件证据

| 项目 | 命令 / 判据 | 结果 |
| --- | --- | --- |
| Harness 与系统任务 | `go test ./internal/agentharness ./internal/actionloop ./internal/modelroute ./internal/recoveryexec ./fleet ./cmd/local-agent ./cmd/fleet-control-plane` | 通过；含角色隔离、系统草案与 operator/设备鉴权、回执上下文及各阶段模型路由 |
| 容器部署合同 | `python -m pytest -q tests/deploy/test_agent_harness_compose.py`、云端及 Orin 两个 profile 的 `docker compose ... config -q` | 3 项静态合同和三种 Compose 配置通过；未启动完整云端堆栈或连接真实 Runtime |
| 全套回归 | `make test`、`make lint`、`make generate-check` | 全部退出码 0；Go 套件通过，Python 2346 通过/40 跳过，前端 479 通过/0 失败；格式和生成文件无差异 |
| 并发与双架构 | `go test -race ./internal/agentharness ./internal/actionloop ./internal/modelroute ./internal/recoveryexec ./fleet ./cmd/local-agent ./cmd/fleet-control-plane`、`go test -race ./middleware/sqlite`、`make edge-orin-build`、三个入口 `GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build` | 重点包与 SQLite race、Linux arm64 和 amd64 交叉编译通过 |
| 实际镜像构建 | `docker buildx build --platform linux/arm64 --pull=false --load -t tangying/agent:local-validation -f Dockerfile.agent .`、`docker run ... server --help` | 本机 Docker 完成 arm64 镜像构建并运行 server 入口；UID/GID 0640 bind mount 读取验证通过。双架构 Buildx 首轮在 Docker Hub amd64 Debian 基础镜像 token 请求超时，尚无 amd64 容器构建证明 |

静态/模拟合同确认 Server 不会看见机器人写工具、Edge 不会看见 Fleet 草案工具；冒充只读工具的写 manifest 在模型选择前被拒绝。operator 的系统任务可创建 `CREATED` 草案，设备及 Assist 凭据被拒；草案没有自动批准或派单。云端路由仅在配置系统模型时启用，模型输入有界，结果保留在循环轨迹中。上述是**软件能力证明**，不代表真实 GPU 工具调用协议和容器已在目标设备运行。

上一轮 PR 的远程 CI 在 `tests/install/test_demo_contract.py::test_demo_exit_reaps_the_actual_local_agent_process` 遇到 SQLite `SQLITE_BUSY`，演示任务因此转入 `RECOVERABLE_FAILURE`，使 `release-gate` 失败。检查发现 `PRAGMA foreign_keys` 仅在 `sql.DB` 的一次连接上设置，连接池其他连接未配置等待；本轮把 5 秒 busy timeout 和外键检查放进每个连接的 DSN，并添加双连接回归测试。该修复的 CI 结果需以本轮推送后的运行记录为准，不把本地单次通过写成远程已通过。

该演示合同在全套回归中通过；之后单独连续重跑三次，均退出码 0。远程 runner 的并发和文件系统条件仍以新一轮 CI 为准。

首次把 `make generate-check` 与 `make lint` 并行时，lint 正好读取被生成器临时替换的 Python 模块，得到一次 `ModuleNotFoundError`；生成检查结束后**顺序重跑** `make lint` 通过。此为验证命令并行造成的干扰，已保留失败现象，后续门禁应顺序执行这两条命令。

## 目标环境尚待签字

- Orin NX：JetPack/CUDA/推理引擎与量化权重哈希；容器 amd64/arm64 镜像来源、UID 65534 的证书/env/卷权限；本机模型真实输入的内存、冷启动和 p50/p95/p99；断网、重启、角色切换和锁冲突。
- 云端 GPU：实际 OpenAI 兼容工具调用、SYSTEM/INTENT/PLANNING 模型独立性、限流和超时、请求并发与失效注入；容器镜像 digest 和模型哈希。
- 真实机器人与机群：现场标定、物理急停、未知结果与持物恢复；按目标机器人数量、观测频率和任务混合对 MySQL、Redis、WorldHub、网关与推理池做长稳/故障恢复及 HA 压测。

结论仅为**待实机认证的软件候选**。不得将编译、仿真和静态 Compose 检查称为 Orin/GPU 现场认证或数千台实际容量证明。
