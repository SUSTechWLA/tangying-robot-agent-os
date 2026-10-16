# 开发者快速上手

目标是先在无硬件条件下理解并运行一条完整任务链，再修改自己负责的层。核对日期：2026-10-16。当前入门使用 **Gazebo 家庭场景**；本页后半保留的 `v0.3.0` 固定工位与离桌导航命令只供历史回归。按模块找代码和测试请从[开发与升级导航](README.md)开始，购机用户另读[实机接入指南](../guides/hardware-agent-integration.md)。

正式软件版本见 [v0.7.0 发布记录](../releases/v0.7.0.md)，后续功能以当前检出的提交、[变更日志](../../CHANGELOG.md)和相应实验报告为准。书籍数字版 `2.2.0` 是独立出版版本，实现复核固定到 `821659df`；阅读书籍不意味着本地工作分支已经包含或发布其后的全部改动。开始前记录 `git branch --show-current`、`git rev-parse HEAD` 和 `git status --short`，不要为跟随本文切换或覆盖正在工作的分支。

## 环境与第一次运行

| 依赖 | 本仓库用途 |
| --- | --- |
| Go 1.26 | `go.mod` 声明的语言版本；CLI、Local、Fleet、Edge |
| Python 3.11 | 主 `.venv`；Gateway、资源构建、测试和脚本；项目声明 >=3.11 |
| MuJoCo 3.11.0 | `pyproject.toml` 精确锁定；当前 Gazebo 路线用于同源资源构建，历史 MuJoCo 路线用于物理仿真 |
| Node.js 22 / npm（当前 CI 基线） | 原生 `node --test` 测试、esbuild 与 Three.js bundle |
| Git、Make | 检出、构建和验证入口 |
| Conda | 仅 RoboCasa 路线；与主 `.venv` 隔离 |
| Docker Compose | 当前 Gazebo 家庭、RTAB-Map/Nav2 Linux 服务与 Fleet；历史固定工位相机仿真无需 Docker |

当前第一次运行按以下顺序进行；复杂命令集中维护在对应指南，避免复制多套启动流程：

1. 阅读[全新部署冷启动](../operations/fresh-deployment.md)，运行预检，按仿真路线准备依赖和开发环境。新克隆从[分支规范](branching.md)的 `main` 入口开始；复现某轮实验则使用报告记录的提交及配置。
2. 阅读 [Gazebo 默认引擎](../guides/gazebo-default.md)，准备同源场景资源和容器，再启动家庭场景。日常入口为 `make home-furnished`，默认控制台是 `http://127.0.0.1:8897/`。显式使用独立运行目录与端口时，以启动输出为准。
3. 按[装修家庭演示](../guides/furnished-home-demo.md)完成整机标定、巡检建图和地图启用；按 Gazebo 指南在任务前完成 `mapping` 到 `localization` 的切换。切换会重建场景，不能把任务中重启解释成连续执行。
4. 从指南支持的巡检任务开始，在创建后核对计划并批准，再在任务记录核对动作后的观测与到达证据。模型驱动的统一目标需要按[统一能力指南](../guides/unified-capability-goals.md)配置 GOAL 模型；安装模型并不会增加未实现的机器人能力。
5. 阅读[真实任务逐阶段讲解](../guides/natural-language-task-lifecycle.md)，沿 task/revision/step/command/observation ID 将控制台、执行账本和原始证据连起来，再修改负责的层。

`make setup` 使用 `python3.11` 创建 `.venv`，安装 `.[dev,visual,mcp,context-research]`、下载 Go 依赖，并通过 `npm ci --prefix web` 安装前端依赖。执行前需要上表中的 Node.js 22 和 npm；无需另行全局安装 Three.js 或 esbuild。解释器在不同位置时使用 `make setup PYTHON=/absolute/path/to/python3.11`。具体依赖始终以当前 `Makefile`、`go.mod`、`pyproject.toml` 和锁文件为准。

点云语义路线是在已有有效地图上添加版本化标注后执行，另见[点云语义操作](../guides/semantic-point-cloud.md)与[五站 Gazebo 实验](../experiments/2026-10-13-semantic-execution.md)。当前通过范围是人工标注驱动的地面导航和到位核验，不是全图自动识别或真机认证。

## 历史回归：v0.3.0 固定工位

下列命令保留原版本、任务和端口，适用于独立目录中的历史检出；**不是当前 Gazebo 的首次启动命令**。对应软件证据见 [v0.3.0 发布记录](../releases/v0.3.0.md)。

```bash
git clone --branch v0.3.0 https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
make setup
make build
./bin/robot-agent version
make rgbd-start
```

历史检出的依赖与启动行为以该 tag 的文件为准；不要将当前工作区的新依赖说明反套到原实验环境。

打开 [Local Console](http://127.0.0.1:8787/)，确认 MuJoCo 仿真，输入“把红色杯子放进右侧收纳盒”。创建后核对理解与计划，再批准。成功依据是任务终态和场景中的 `red-cup → right-bin`。默认确定性解析不需要 LLM 密钥。

```bash
make sim-status
make sim-logs
make sim-stop
```

`make demo` 是自动清理的命令行闭环；单机器人相机闭环浏览器调试用 `make rgbd-start`。两者不要同时占用默认端口。原始进程命令见[轻量仿真](../guides/quickstart.md)。

相机闭环的逐层源码、启动、恢复与证据接口见[单机器人 RGB-D 闭环](single-robot-loop.md)。`make rgbd-restart` 重置仿真世界，不能用于验证持物期间只重启 Agent 的恢复。后者使用 `scripts/run_rgbd_acceptance.py`，它在独立端口启动并清理自己的进程。

## 历史回归：从离桌位置完成导航任务

本节延续上面的历史 MuJoCo 固定工位路线及其验收范围。`make rgbd-start` 不执行接近导航。安装 Docker Compose 后，结束当前任务并运行：

```bash
make sim-stop
make navigation-start NAVIGATION_ARGS='--build --mode mapping'
make navigation-status
.venv/bin/python scripts/run_navigation_acceptance.py \
  --output artifacts/acceptance/navigation-v0.2-dev-1
```

执行验收前等待地图与定位就绪。移动配置把底盘初始化在世界坐标 `Y=-0.60 m`，接近目标 `Y=0.05 m`，名义移动约 65 cm；固定工位起点不变。上述脚本向当前 Local 仿真创建并批准“把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来”，检查 18 个工具步骤、原始观测与 PNG 哈希、真实底盘位移，以及两个物品各三帧的稳定放置。`--output` 必须使用新目录。

检查长暂停时，先完成任务并使用 `make navigation-restart NAVIGATION_ARGS='--mode mapping'` 恢复初始现场；然后换新目录加 `--pause-seconds 65`。此参数等待工具边界暂停，65 秒后显式继续，保留同任务记录。脚本不负责启动服务或清除地图；流程和通过门槛见[移动任务验收](single-robot-loop.md#移动任务与长暂停验收)，本轮是否通过以发布记录为准。

主 `.venv` 的引擎精确锁定为 MuJoCo 3.11.0。曾出现新装环境拉到 3.12 后关节枚举与 NumPy 比较不一致，导致自体过滤拒绝合法 CAD 的问题；修复与独立兼容验证见[MuJoCo 版本说明](mujoco-compatibility.md)。不要直接升级正在验收的运行环境。

## 理解一条任务链

先读[开发原则与代码地图](principles.md)，再沿以下路径阅读：

1. `web/console_ui.js` 管展示与导航；`web/app.js` 调 API。Local API 在 `console/server.go`，Fleet 在 `fleet/server.go`。
2. 先识别入口：`POST /v1/objectives` 保存并等待批准粗目标合同；原 `/v1/tasks` 与现有 UI 表单仍先规划、再冻结批准。两条路径不能混用预算与审批假设。
3. Local 的 `internal/localapp` 或 Fleet 的 `edge/worker` 装配共享 `internal/edgeagent.Host`。Host 调用 `internal/capabilityagent` 规划、验证子计划，使用持久 Task/Ops/Recovery 逻辑推进；`edge/agent` 继续承担复合机器人闭环，`tasks/` 管批准与不可变版本。
4. `edge/runtime` 定义语义命令，`edge/robotclient` 映射到 `proto/robot/v1/robot.proto`。
5. Python `service.py → SafetySupervisor → RobotBackend` 校验并执行；仿真世界返回观测，Fleet 的 `core/harness` 复核后置条件。

推荐先阅读[目标委托接入](../guides/delegated-edge-goals.md)，沿 `core/objective/contract.go → tasks/objective.go → internal/edgeagent/host.go` 追踪权限包络、子计划、检查点和完成核验，再比较 `internal/localapp` 与 `edge/worker` 的入口装配。模型调用在本机或云端不改变边缘的生命周期职责；Local 的事件式 `agentruntime` 与 Host 的监督逻辑也不是同一开关。

任务成功、Runtime 终态、世界观测和 UI 展示各有不同权威，排障用 task/revision/step/command/observation ID 对齐，不改事件来修补显示。

## 按工作内容扩展环境

移动机器人使用 [RTAB-Map / Nav2 导航](rtabmap-navigation.md)。先理解双 RGB-D、里程计和 `map/odom/base_link` 的区别，再运行导航容器；只启动固定工位 RGB-D Runtime 不会自动获得 SLAM 能力。

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
| ROS 导航与 Nav2 控制器插件 | 在已构建并加载 `robot/ros2_ws/install/setup.sh` 的 ROS Jazzy 工作区执行 `colcon test --packages-select tangying_navigation tangying_dwb_critics --return-code-on-test-failure`、`colcon test-result --verbose`；Linux CI 同样执行。导航节点测试需要真实 `rclpy`，不能把无 ROS 主机上的跳过记录当作通过 |
| 文档 | `.venv/bin/python -m pytest -q tests/docs/test_production_docs.py` |
| 目标委托与长步骤链 | `go test ./core/objective ./tasks ./internal/edgeagent ./edge/worker ./fleet/coordinator`；并发/持久化改动补相关 `-race`，实际实验用[鲁棒性协议](../experiments/2026-10-15-single-robot-robustness-protocol.md)的新目录 |
| 移动闭环与恢复 | [`run_navigation_acceptance.py`](../../scripts/run_navigation_acceptance.py) 检查现有导航仿真；长暂停使用新现场与 `--pause-seconds 65` |
| 安装脚本 | `make install-check` |
| 多机器人恢复 | `make fleet-chaos`；RoboCasa 用 `make test-robocasa-faults` |
| 自然语言理解与执行 | [13 项真实进程仿真评测](natural-language-evaluation.md)，覆盖口语、代词、拒绝路径和起点绑定 |
| 发布候选 | [发布检查](../production/testing-and-acceptance.md)，记录环境、提交、命令与跳过原因 |

ROS CI 另建带 `--system-site-packages` 的最小协议 venv，从 `pyproject.toml` 提取 `grpcio`、`protobuf` 的固定版本，并在加载 ROS/workspace 后将该层与仓库 `python/` 注入 `PYTHONPATH`。这样既能读取系统 `rclpy`，又能运行当前生成存根和 RGB-D 合同测试；仅有 `rosdep` 不会安装这层协议依赖，也不需要把整套仿真依赖装进 ROS 环境。

常规全套入口是 `make test`、`make lint`。`make test-go` 使用 Makefile 列出的本项目包；主 Python 未安装 RoboCasa 时会有条件跳过，不能把 skipped 写成已验证。完整 RoboCasa 验证使用隔离环境。`make sim2real-check` 是仿真测试与 30 轮模拟验收，不执行实机，也不会签发 PHYSICAL_GO。

`make robocasa-acceptance` 重验历史 `round4`；证明当前版本需重新 candidate 采集并按审计流程晋级。不同层的通过结果不能相互替代。

## 第一个改动的提交准备

先查 `git status --short`，保留已有工作区改动。选择明确的行为入口，修改实现及必要的回归测试，再更新对应指南。检查 `git diff --check`、相关验证结果、秘密与临时制品；不提交 `.env`、私钥、capture session 或上游检出目录。旧设计档案保留，新决策注明适用版本。
