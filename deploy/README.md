# 部署目录

本目录按**运行位置**分为云端、机器人端、Orin NX 边缘端和本地开发机。归属判据、进程与端口见[部署目标与代码归属](../docs/operations/deployment.md)。

| 目标 | 目录 | 内容 | 入口 |
| --- | --- | --- | --- |
| 云端 Cloud | [`cloud/`](cloud/) | Fleet 控制面 Compose、统一 Agent 镜像、nginx mTLS、环境模板 | `./scripts/fleet-up.sh up` |
| 机器人端 Robot | [`robot/`](robot/) | 树莓派 systemd 单元与 udev 规则、导航容器栈 | `./install.sh robot-pi` |
| 本地单机 Local | [`local/`](local/) | 开发机上的 Local Agent 后台单元与环境模板 | `./install.sh local` |
| Orin NX Edge | [`edge-orin/`](edge-orin/) | 单机器人自治或 Fleet Worker 的 Compose profile、systemd 单元与环境模板 | `docker compose --profile edge -f deploy/edge-orin/compose.yaml up -d --build` → [Orin NX 安装](../docs/install/edge-orin.md) |

## cloud/

| 文件 | 用途 |
| --- | --- |
| `docker-compose.yml` | MySQL、Redis、控制面、nginx 四个服务；控制面在容器内 `:8443`，nginx 终止 `:443` |
| [`Dockerfile.agent`](../Dockerfile.agent) | 云端与 Orin 共用的 amd64/arm64 Agent 镜像；入口脚本按 `server/edge/worker` 选角色 |
| `nginx.conf` | 客户端证书校验、gRPC 透传与来源白名单 |
| `allowed.conf` | 允许访问的来源白名单（由 `fleet-certs.sh` 生成，不进入 Git） |
| `.env.example` | 端口、数据库口令等模板；`.env` 由 `fleet-up.sh` 生成，不进入 Git |
| `certs/` | 本地开发用的 mTLS 证书集（生产证书不进入 Git） |

## robot/

### robot/raspberry-pi/

| 文件 | 用途 |
| --- | --- |
| `tangying-xlerobot.service` | 真机驱动单元：Feetech 舵机与相机，`SupplementaryGroups=dialout` |
| `tangying-robot-edge.service` | 经 ROS 2 启动网关与安全监督 |
| `tangying-robot-edge-direct.service` | 不依赖 ROS 2 的直连边缘单元 |
| `99-tangying-xlerobot.rules` | udev 规则：固定 `/dev/tangying-left`、`/dev/tangying-right`，权限 `0660` |
| `robot-pi.env.example` | 机器人端环境模板，安装到 `/etc/tangying-robot-agent-os/robot-pi.env` |

### robot/navigation/

RTAB-Map 建图定位与 Nav2 移动的容器栈，含持久地图卷。Compose 的构建上下文是**仓库根目录**（`context: ../../..`），因此镜像内可以直接取到 ROS 2 工作区与资产。使用说明见 [`robot/navigation/README.md`](robot/navigation/README.md)。

```bash
bash scripts/navigation-stack.sh restart --mode mapping
docker compose -p tangying-navigation -f deploy/robot/navigation/compose.yaml down   # 不要加 -v，会删除地图卷
```

## local/

| 文件 | 用途 |
| --- | --- |
| `com.tangying.robot-agent.plist` | macOS launchd 单元 |
| `tangying-robot-local-agent.service` | Linux systemd 用户单元 |
| `local.env.example` | Local Agent 环境模板 |

## edge-orin/

| 文件 | 用途 |
| --- | --- |
| `compose.yaml` | 与云端同一镜像；`edge` 与 `fleet` profile 互斥，host 网络访问本机 Runtime/量化模型，共享持久控制锁卷 |
| `edge.env.example` | Orin NX 单机器人自治模型与 Runtime 配置 |
| `edge-worker.env.example` | Orin NX 接入云端 Fleet 的设备、Runtime 和 mTLS 配置 |
| `tangying-orin-local-agent.service` | 单机器人自治 systemd 单元；启动前只读预检 |
| `tangying-orin-edge-worker.service` | Fleet Worker systemd 单元；启动前只读预检 |

容器与 systemd 是两种安装方式；同一 Runtime 只能启动一种任务权威。容器配置、权限和切换命令见[Orin NX 安装](../docs/install/edge-orin.md)，设计与回滚边界见[角色 Harness / Docker ADR](../docs/superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md)。

## 约定

- 部署文件按目标分目录，**不再新增跨目标的公共目录**：共享的样例配置放在需要它的目标目录里，避免出现“这份配置到底装到哪台机器”的问题。
- 新增部署文件时同时更新本表与[部署目标与代码归属](../docs/operations/deployment.md)，`tests/deploy/test_deployment_layout.py` 会校验目录归属与文档一致。
- 生产密钥、证书与白名单一律不进入 Git。
