# 开发者快速上手

目标是先在无硬件条件下理解并运行一条完整任务链，再修改自己负责的层。购机用户请直接阅读[实机上手](../sim2real/README.md)；当前 V1 的验证范围见[状态页](../production/v1-release-status.md)。

## 环境与第一次运行

| 依赖 | 本仓库用途 |
| --- | --- |
| Go 1.26 | `go.mod` 声明的语言版本；CLI、Local、Fleet、Edge |
| Python 3.11 | 主 `.venv`；Gateway、MuJoCo、测试和脚本；项目声明 >=3.11 |
| Node.js 22 / npm（当前 CI 基线） | 原生 `node --test` 测试、esbuild 与 Three.js bundle |
| Git、Make | 检出、构建和验证入口 |
| Conda | 仅 RoboCasa 路线；与主 `.venv` 隔离 |
| Docker Compose | 正式 Fleet Compose/MySQL/Redis 路线；本地仿真不需要 |

在仓库根目录执行：

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
make setup
make build
make rgbd-start
```

`make setup` 使用 `python3.11` 创建 `.venv`，安装 `.[dev,visual,mcp]` 并下载 Go 依赖；解释器在不同位置时使用 `make setup PYTHON=/absolute/path/to/python3.11`。这是工作区开发环境，无需先运行系统安装器。

打开 [Local Console](http://127.0.0.1:8787/)，确认 MuJoCo 仿真，输入“把红色杯子放进右侧收纳盒”。创建后核对理解与计划，再批准。成功依据是任务终态和场景中的 `red-cup → right-bin`。默认确定性解析不需要 LLM 密钥。

```bash
make sim-status
make sim-logs
make sim-stop
```

`make demo` 是自动清理的命令行闭环；单机器人相机闭环浏览器调试用 `make rgbd-start`。两者不要同时占用默认端口。原始进程命令见[轻量仿真](../quickstart.md)。

相机闭环的逐层源码、启动、恢复与证据接口见[单机器人 RGB-D 闭环](single-robot-loop.md)。`make rgbd-restart` 重置仿真世界，不能用于验证持物期间只重启 Agent 的恢复。后者使用 `scripts/run_rgbd_acceptance.py`，它在独立端口启动并清理自己的进程。

## 理解一条任务链

先读[开发原则与代码地图](principles.md)，再沿以下路径阅读：

1. `web/console_ui.js` 管展示与导航；`web/app.js` 调 API。Local API 在 `console/server.go`，Fleet 在 `fleet/server.go`。
2. `agent/` 与 `orchestration/` 生成经过校验的意图与计划；`tasks/` 管任务、审批、版本和事件。
3. Local 由 `internal/localapp` 与 `edge/agent` 执行；Fleet 由 `fleet/coordinator` 与 `edge/worker` 协作。
4. `edge/runtime` 定义语义命令，`edge/robotclient` 映射到 `proto/robot/v1/robot.proto`。
5. Python `service.py → SafetySupervisor → RobotBackend` 校验并执行；仿真世界返回观测，Fleet 的 `core/harness` 复核后置条件。

任务成功、Runtime 终态、世界观测和 UI 展示各有不同权威，排障用 task/revision/step/command/observation ID 对齐，不改事件来修补显示。

## 按工作内容扩展环境

新机器人从[异构适配器开发](robot-adapters.md)开始。先运行 Profile/感知检查和两种模拟结构的同一套合同测试，再实现厂商驱动、三维感知 provider 与规范工具 handler。`robot/gateway/tangying_robot_gateway/contracts.py` 是 Python schema 定义，`core/robotcontract` 是 Go 消费边界；协议改动需要同时验证两端。MCP 使用可选依赖组 `mcp`，完整开发环境已安装，独立安装与宿主配置见 [MCP 指南](../../robot/mcp/README.md)。

双机器人和 WebGL：

```bash
make robocasa-install
make robocasa-smoke
make robocasa-web-assets
make robocasa-fleet
make robocasa-handoff
```

浏览器入口为 `http://127.0.0.1:18080/`；结束执行 `bash scripts/robocasa-fleet.sh stop`。`datasets/robocasa`、`datasets/robosuite` 是独立上游检出，安装脚本会下载资产；改上游版本须记录实际 commit 并重新冒烟。它们不属于 AgentOS 自有业务代码。

这条路线使用 Docker Compose，登录读取私有 `deploy/cloud/.env` 的生成凭据。RoboCasa 是单回合定向交接，重复演示需按该 profile 的停止/启动流程重置。要验证当前检出的语言改动且不接管现有 Docker 服务，使用[自然语言评测脚本](natural-language-evaluation.md#复现)：它独立构建当前 Fleet/Edge，分配本机随机端口，并为每个执行用例等待新观测。

仅改前端展示通常运行 `make test-web`；改 `web/src/webgl_entry.js` 或 Three.js 依赖时，在 `web/` 下执行 `npm ci`、`npm run build`、`npm test`。构建结果及同源资源闭包影响签名验收，见[前端说明](../frontend/console-v1.md)。

已有 18080 服务时，用 `CONSOLE_BACKEND_URL=http://127.0.0.1:18080 node scripts/preview-console.cjs` 在 18130 预览当前前端，无需重建后端。预览会让模型资产和世界 API 来自同一运行服务，避免不同导出版本混用导致 `VISUAL_MODEL_MISMATCH`。三维场景、简洁视图、机器人画面、全局地图都可在工作台直接切换；视图选择只改变显示，不重置仿真。完整嵌入发布仍需要重新构建并验收。

**预览当前前端不等于后端已更新。** 改 `agent/`、`tasks/` 或 Edge 后，核对进程/Compose 使用的检出与二进制来源，再按该服务启动方式重建；不要把另一 worktree 的历史任务当作当前代码结果。独立评测的 `--keep-running` 会写 `ready.json`，使用其中的 `baseURL` 连接预览；随机端口、临时账号与忽略的报告目录不会随 Git 克隆迁移。

修改自然语言理解时，从 `agent/intent/natural_language_test.go` 看支持表达和反例，从 `tasks/service_revision_test.go` 看更新约束，从 `edge/robotclient/grounding_test.go` 看物体/起点如何落到观测。前端 Experience 刷新规则见[数据契约](../production/data-contracts.md#taskexperience-完整快照)，它与 World 的 revision 规则不同。

修改协议需要系统 `protoc`、Go 的 `protoc-gen-go` / `protoc-gen-go-grpc` 和 `.venv` 中 `grpc_tools`。先查看生成脚本与 CI 使用的版本，再运行 `make generate`、`make generate-check`。后者会重新生成并检查 `gen/go` 与 `python/tangying_robot_proto` 的 diff；不要手工修改生成代码。

## 验证范围

| 改动 | 首选验证 |
| --- | --- |
| Go 业务与分层 | 对应包 `go test`，再 `make test-go`；架构守卫在 `tests/architecture` |
| Runtime/Safety/驱动 | 对应 Python 测试，必要时 `make test-python`，包含 Go/Python 真实边界测试 |
| 策略接入 | `make test-policy-sidecar`、`make policy-handoff`、`make policy-faults` |
| Web 展示 | `make test-web`；布局/交互改动补浏览器验证 |
| 文档 | `.venv/bin/python -m pytest -q tests/docs/test_production_docs.py` |
| 安装脚本 | `make install-check` |
| 多机器人恢复 | `make fleet-chaos`；RoboCasa 用 `make test-robocasa-faults` |
| 自然语言理解与执行 | [13 项真实进程仿真评测](natural-language-evaluation.md)，覆盖口语、代词、拒绝路径和起点绑定 |
| 发布候选 | [发布检查](../production/testing-and-acceptance.md)，记录环境、提交、命令与跳过原因 |

常规全套入口是 `make test`、`make lint`。`make test-go` 使用 Makefile 列出的本项目包；主 Python 未安装 RoboCasa 时会有条件跳过，不能把 skipped 写成已验证。完整 RoboCasa 验证使用隔离环境。`make sim2real-check` 是仿真测试与 30 轮模拟验收，不执行实机，也不会签发 PHYSICAL_GO。

`make robocasa-acceptance` 重验历史 `round4`；证明当前版本需重新 candidate 采集并按审计流程晋级。不同层的通过结果不能相互替代。

## 第一个改动的提交准备

先查 `git status --short`，保留已有工作区改动。选择明确的行为入口，修改实现及必要的回归测试，再更新对应指南。检查 `git diff --check`、相关验证结果、秘密与临时制品；不提交 `.env`、私钥、capture session 或上游检出目录。旧设计档案保留，新决策注明适用版本。
