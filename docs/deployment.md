# 部署目标与代码归属

同一个仓库同时包含云端、机器人端和开发机三部分代码。本页是**唯一的归属判据**：要装到某台机器上的东西，在哪个目录、由谁启动、看哪份配置，都在这里。

部署前先明确一件事：**云端与机器人端是两项独立的放行结论**。云端部署成功不代表机器人可以动；机器人端软件安装成功也不代表现场已验收。实机动作仍需负责人完成[发布检查清单](operations/release-checklist.md)与现场制动、标定、监护验收。

## 1. 三个运行位置

| 目标 | 跑什么 | 典型主机 | 部署入口 |
| --- | --- | --- | --- |
| **云端 Cloud** | Fleet 控制面、MySQL、Redis、nginx（HTTPS + mTLS） | 云主机 / 容器平台 | [`deploy/cloud/`](../deploy/cloud/) · `./scripts/fleet-up.sh up` |
| **机器人端 Robot** | Edge Worker、ROS 2 网关与安全监督、xlerobot 适配器、导航栈 | 树莓派 / 机器人上位机 | [`deploy/robot/`](../deploy/robot/) · `./install.sh robot-pi` |
| **本地单机 Local** | Local Agent（含工作台控制台与任务账本） | 开发机 / 单台机器人（不接云端） | [`deploy/local/`](../deploy/local/) · `./install.sh local` |

仿真（MuJoCo、Gazebo、RoboCasa）不是第四个交付目标，而是**开发机上的验证环境**：它替代真实机器人供上面三个目标联调与验收，安装入口是 `./install.sh sim`。

选择哪条路线：

- 只有一台机器人、不需要云端调度 → **本地单机**。控制台在 `http://127.0.0.1:8787/`。
- 多台机器人、要统一派单与审计 → **云端 + 机器人端**。云端不直接控制硬件，动作仍由机器人端的 Runtime 与安全监督执行。
- 只做开发或验收 → **仿真**，不需要任何真实硬件。

## 2. 源码目录归属

Go 与 Python 的包路径是模块内部接口（`go.mod` 模块路径 + 跨语言生成代码），因此**源码目录不按部署目标搬动**；归属由下表声明，并由 `tests/deploy/test_deployment_layout.py` 校验每个顶层目录都被分类。

| 目录 | 归属 | 内容 |
| --- | --- | --- |
| `cmd/fleet-control-plane/` | 云端 | 云端控制面进程入口 |
| `cmd/edge-worker/` | 机器人端 | 边缘执行进程入口 |
| `cmd/local-agent/` | 本地单机 | 单机 Agent 进程入口（内嵌工作台） |
| `cmd/robot-agent/` | 本地单机 | 运维 CLI：`doctor`/`configure`/`pair`/`start`/`status`/`logs`/`demo` |
| `fleet/` | 云端 | 认证、协调器、事件日志、网关、租约、MySQL/Redis 适配、注册表、世界中枢 |
| `fleet/worldhub/` | 云端 + 本地 | 共享世界状态（云端控制面与单机 Agent 都用） |
| `edge/` | 机器人端 + 本地 | 执行循环：`agent`（闭环步进）、`runtime`（命令校验与执行）、`robotclient`、`worker`、`cloudclient`（连云端）、`policy`（策略契约与 Provider） |
| `agent/` | 共享运行时 | Harness Agent：理解、编排、完成判定 |
| `orchestration/`、`tasks/`、`skills/` | 共享运行时 | 任务模型与状态机、技能与工具实现 |
| `core/` | 共享运行时 | 闭环门禁、世界模型、观测、遥测等不依赖具体基础设施的契约 |
| `middleware/` | 共享运行时 | 存储与消息适配（memory、sqlite） |
| `internal/` | 本地单机 | `localapp` 组装与 `localconfig` 配置、运维 CLI 实现 |
| `console/` | 本地单机 | 控制台 HTTP API 与内嵌前端资源 |
| `web/` | 本地单机 + 云端 | 控制台前端静态资源；被 `console/` 与 `fleet/` 同时嵌入 |
| `robot/gateway/` | 机器人端 | Python 工具层、语义地图、Gateway 适配器 |
| `robot/mcp/` | 机器人端 | 对外 MCP 服务 |
| `robot/ros2_ws/src/` | 机器人端 | ROS 2 包：xlerobot 适配器、网关、安全监督、导航、消息、DWB 评价器 |
| `policy/sidecar/` | 机器人端 | 策略推理 HTTP 服务（VLA / IL / RL 候选动作），供边缘 Worker 调用 |
| `sim/mujoco/`、`sim/robocasa/` | 仿真 | 仿真世界与 gRPC 适配器、RoboCasa 夹具 |
| `proto/`、`gen/`、`python/` | 共享 | 协议定义与生成代码（Go / Python），三个目标共用 |
| `deploy/` | 部署 | 按目标分目录的部署文件，见 [`deploy/README.md`](../deploy/README.md) |
| `scripts/` | 工具 | 生命周期、验收、安装与数据脚本（按目标见 `scripts/install/`） |
| `tests/` | 工具 | 单元、契约、架构、E2E 与安装测试 |
| `docs/` | 文档 | 当前指南与历史档案，索引见 [`docs/README.md`](README.md) |
| `examples/` | 工具 | 适配器示例 |

## 3. 云端：装了哪些进程

| 进程 | 来源 | 对外 | 说明 |
| --- | --- | --- | --- |
| `fleet-control-plane` | `cmd/fleet-control-plane` | `:8443`（容器内 HTTP）、`nginx` 终止 `:443` | 任务、世界、租约、审计的唯一权威 |
| `mysql` | 官方镜像 | 容器内 `:3306` | 任务、事件、世界快照持久化 |
| `redis` | 官方镜像 | 容器内 `:6379` | 队列与租约 |
| `nginx` | `deploy/cloud/nginx.conf` | `${FLEET_HTTPS_PORT:-443}`（HTTPS + mTLS）、`${FLEET_GRPC_PORT:-8444}`（gRPC 透传）、`127.0.0.1:${FLEET_LOOPBACK_HTTP_PORT:-18080}` | 客户端证书校验与来源白名单 |

```bash
make fleet-build              # 构建 bin/fleet-control-plane 与 bin/edge-worker
./scripts/fleet-up.sh up      # 生成 .env、mTLS 证书与白名单，启动 Compose 并等待就绪
./scripts/fleet-up.sh env     # 打印机器人端接入所需的凭据
./scripts/fleet-up.sh down
```

生产 `.env`、证书与白名单不进入 Git（`.gitignore` 已覆盖）。云端只下发命令与租约，**不直接驱动硬件**；`emergency_stop` 一类动作始终由机器人端执行。

## 4. 机器人端：装了哪些进程

| 进程 / 单元 | 来源 | 说明 |
| --- | --- | --- |
| `tangying-xlerobot.service` | `deploy/robot/raspberry-pi/` | 真机驱动：Feetech 舵机与相机，需要 `dialout` 组与 udev 规则 |
| `tangying-robot-edge.service` | 同上 | 经 ROS 2 启动网关与安全监督 |
| `tangying-robot-edge-direct.service` | 同上 | 不依赖 ROS 2 的直连边缘模式 |
| `99-tangying-xlerobot.rules` | 同上 | 固定 `/dev/tangying-left`、`/dev/tangying-right`，权限 `0660` |
| 导航容器 | `deploy/robot/navigation/` | RTAB-Map 建图定位 + Nav2 移动，含持久地图卷 |
| 策略 sidecar | `policy/sidecar/` | 可选；边缘 Worker 通过 HTTP Provider 调用 |

```bash
./install.sh robot-pi                 # 安装机器人端角色
./scripts/robot-pi-preflight.sh       # 上电前离线检查
./scripts/robot-pi-quick-deploy.sh    # 快捷部署
bash scripts/navigation-stack.sh restart --mode mapping   # 建图/定位栈
```

机器人端配置位于 `/etc/tangying-robot-agent-os/robot-pi.env`（模板：`deploy/robot/raspberry-pi/robot-pi.env.example`）。

## 5. 本地单机：装了哪些进程

| 进程 | 来源 | 对外 | 说明 |
| --- | --- | --- | --- |
| `local-agent` | `cmd/local-agent` | `http://127.0.0.1:8787/` | 任务账本、Agent 循环、工作台与历史观测 |
| 仿真适配器（可选） | `sim/mujoco` | `127.0.0.1:50051` | 无硬件时替代真实机器人 |

```bash
./install.sh local        # 开发机上的 Local Agent
make rgbd-start           # 构建并启动仿真 + Local Agent（固定工位）
```

后台单元：macOS 用 `deploy/local/com.tangying.robot-agent.plist`，Linux 用 `deploy/local/tangying-robot-local-agent.service`；配置模板 `deploy/local/local.env.example`。

## 6. 一次启动全部组件

```bash
./scripts/start-all.sh up            # 仿真 + Local Agent + 控制台
./scripts/start-all.sh up --demo     # 再跑一遍命令行任务演示
./scripts/start-all.sh up --with-cloud --with-navigation
./scripts/start-all.sh status
./scripts/start-all.sh down
```

`start-all.sh` 只负责按顺序调用各目标已有的生命周期脚本（`sim-stack.sh`、`navigation-stack.sh`、`fleet-up.sh`），并在结束时打印每个组件的地址与健康状态；它不重复实现启动逻辑。各组件也可以单独启动，命令见上面各节。云端组件需要 Docker，缺失时脚本会明确说明跳过原因，而不是静默继续。

## 7. 端口一览

| 端口 | 归属 | 用途 |
| --- | --- | --- |
| `8787` | 本地单机 | 工作台控制台与 HTTP API |
| `50051` | 仿真 | MuJoCo gRPC 适配器 |
| `18790` / `18791` | 机器人端 | 导航栈 / Gazebo 家庭场景容器 |
| `443` / `8444` | 云端 | 控制面 HTTPS + mTLS / gRPC 透传 |
| `18080` | 云端（仅回环） | 本地调试入口 |
| `3306` / `6379` | 云端 | MySQL / Redis（仅容器网络） |

## 8. 相关文档

- 安装：[Local](install/local.md)、[树莓派](install/robot-pi.md)、[树莓派快捷部署](install/robot-pi-quick.md)、[阿里云](install/alicloud-cloud.md)、[Fleet 云端](fleet-cloud.md)、[安装排障](install/troubleshooting.md)
- 运维：[部署与容量](production/deployment-and-capacity.md)、[配置与安全](production/configuration-and-security.md)、[异常运维](production/operations-and-failures.md)
- 硬件与放行：[Sim2Real 接入](production/sim-to-real.md)、[XLeRobot 集成边界](xlerobot-setup.md)、[安全检查表](safety-checklist.md)
- 组件内部结构：[完整架构](production/architecture.md)、[分布式成熟度](distributed-agentos.md)、[多机器人](multi-robot.md)
