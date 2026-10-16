# Orin NX：单机器人边缘 Agent 与 Fleet Worker

Orin NX 有两种部署方式，**同一台 Robot Runtime 同一时刻只启用一种任务权威**。`local-agent` 管理该机器人的本机 SQLite 任务账本、量化模型和执行闭环；`edge-worker` 领取云端 Fleet 的获批目标或兼容旧计划，并与 Local 共享 `internal/edgeagent.Host` 在授权内编排、执行和核验。两种进程都在确认 Runtime 身份后按 Runtime 地址取得同机控制锁；连接同一地址的第二个进程启动会失败。两个生产配置必须使用相同规范地址（模板为 `127.0.0.1:50051`）。跨主机部署及同一 Runtime 的不同地址别名仍必须由运维保证控制权唯一，不能把同机文件锁当作分布式 fencing。

| 运行方式 | Orin NX 进程 | 任务权威 | 模型调用 |
| --- | --- | --- | --- |
| 单机器人自治 | `local-agent` + Robot Runtime + 可选本地量化推理服务 | Orin NX 的 SQLite | 意图、规划、恢复可分别用本地模型、确定性逻辑或 Fleet 大模型工具 |
| 云端机群 | `edge-worker` + Robot Runtime + 可选本地推理服务 | Fleet 拥有父目标批准/claim；边缘 SQLite 保存子计划与检查点 | 新目标由 Worker 的 `AGENT_GOAL_*` 在授权包络内规划；旧冻结计划仍可由云端先规划 |

实现参考 `821659df`，核对日期：2026-10-16。目标合同与新旧入口区别见[目标接入](../guides/delegated-edge-goals.md)，完整状态备份、续跑和回滚见[目标运维](../operations/delegated-goal-operations.md)。模型推理在本机或云端不改变边缘拥有子任务生命周期的职责；逻辑 Ops/Recovery 不要求分别运行 LLM，Worker 也没有直接托管 Local 的整套事件总线。

云端 Fleet 不持有 Runtime 凭据，也不把模型输出当动作授权。实机运动前仍需现场审批、标定、观测和急停验收。

## Docker 安装（云端与 Orin 使用同一镜像源码）

在 Orin NX 的本仓库检出固定 commit。将 `deploy/edge-orin/edge.env.example` 复制为私有 `deploy/edge-orin/edge.env`，或在 Fleet 模式将 `edge-worker.env.example` 复制为 `edge-worker.env`；填入实际 RobotID、mTLS 证书和模型路由。`edge.env` 是 Local Agent 读取的配置文件，`edge-worker.env` 由 Compose 注入环境。容器固定 UID 65534，必须通过 `deploy/edge-orin/.env` 的 `EDGE_CERT_GID` 指定专用宿主机证书组；两个私有文件及 `/etc/tangying/certs` 内证书只给管理员和该组读取。镜像、证书目录与数据卷均应固定在同一设备。

```bash
getent group tangying-agent-certs >/dev/null || sudo groupadd --system tangying-agent-certs
sudo install -m 0640 -o root -g tangying-agent-certs deploy/edge-orin/edge.env.example deploy/edge-orin/edge.env
# 编辑私有 edge.env 并安装真实证书；Fleet 模式同样保护 edge-worker.env。
sudo install -d -m 0750 -o root -g tangying-agent-certs /etc/tangying/certs
# CA/客户端证书/私钥均以 root:tangying-agent-certs、0640 安装，私钥不得设为 0644。
printf 'EDGE_CERT_GID=%s\n' "$(getent group tangying-agent-certs | cut -d: -f3)" | sudo tee deploy/edge-orin/.env >/dev/null
sudo chmod 0600 deploy/edge-orin/.env
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge config -q
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge build local-agent
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge run --rm --no-deps --entrypoint /usr/local/bin/local-agent local-agent --config /etc/tangying/edge.env --check-config
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge up -d
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge logs --tail=100 local-agent
```

Fleet 模式先运行 `sudo install -m 0640 -o root -g tangying-agent-certs deploy/edge-orin/edge-worker.env.example deploy/edge-orin/edge-worker.env` 并用 `sudoedit` 编辑，再用 `--profile fleet` 和服务名 `edge-worker`；无运动容器预检命令是 `sudo docker compose -f deploy/edge-orin/compose.yaml --profile fleet run --rm --no-deps --entrypoint /usr/local/bin/edge-worker edge-worker --check-config`。**切换前先停旧 profile**，核对持物、未知动作结果、Runtime journal 与 Fleet/本地任务，再启新 profile。Compose 的共享 `agent-state` 卷持久化 Local SQLite 与同机锁；切换或回滚不要执行 `down -v`。`network_mode: host` 是为了连接 Orin 的 `127.0.0.1` Runtime 和量化模型，Local Console 仍应监听 loopback；若需远程访问，使用受控 TLS/鉴权代理。容器入口先运行无运动配置预检，真实连接和模型推理仍需目标机验收。

云端使用 `deploy/cloud/docker-compose.yml` 的同一 `Dockerfile.agent`，`command: [server]`；设置 `AGENT_SYSTEM_PROVIDER=openai`、独立的 `AGENT_SYSTEM_BASE_URL`、`AGENT_SYSTEM_MODEL` 与必要的 `AGENT_SYSTEM_API_KEY` 后，operator 可调用系统任务 API 读取机群状态并创建待审批草案。系统工具目前创建旧冻结草案；新粗目标另通过 `POST /v1/objectives` 创建并由原任务 API 批准。边缘负责目标内的子任务规划、执行和核验，云端保留全局分工与授权。设计与验收见[角色 Harness / Docker ADR](../superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md)和[本轮记录](../production/agent-harness-docker-acceptance.md)。

## 二进制与 systemd 安装

在开发机执行 `make edge-orin-build`，输出 `bin/orin-arm64/local-agent` 与 `bin/orin-arm64/edge-worker`。这只验证 Linux arm64 可编译；目标 JetPack、CUDA、模型运行时、内存与推理时延需在 Orin NX 验证。将两个二进制传到目标机并校验 SHA-256，按下面示例安装；生产部署要固定 Git commit、二进制哈希和模型权重哈希。

```bash
id -u tangying-robot >/dev/null 2>&1 || sudo useradd --system --home /var/lib/tangying-robot-agent-os --shell /usr/sbin/nologin tangying-robot
sudo install -d -m 0750 -o root -g tangying-robot /etc/tangying-robot-agent-os
sudo install -d -m 0700 -o tangying-robot -g tangying-robot /var/lib/tangying-robot-agent-os
sudo install -m 0755 bin/orin-arm64/local-agent /usr/local/bin/tangying-local-agent
sudo install -m 0755 bin/orin-arm64/edge-worker /usr/local/bin/tangying-edge-worker
sudo install -m 0640 -o root -g tangying-robot deploy/edge-orin/edge.env.example /etc/tangying-robot-agent-os/edge.env
sudo install -m 0640 -o root -g tangying-robot deploy/edge-orin/edge-worker.env.example /etc/tangying-robot-agent-os/edge-worker.env
sudo install -m 0644 deploy/edge-orin/tangying-orin-local-agent.service /etc/systemd/system/
sudo install -m 0644 deploy/edge-orin/tangying-orin-edge-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
```

将证书存入 `/etc/tangying/certs` 并给运行用户最小读取权限，再编辑两个私有 env 文件。`edge.env` 由 Local Agent 自己解析；`edge-worker.env` 由 systemd 的 `EnvironmentFile` 读取。不要把模板中的设备令牌、模型密钥、开发 CA 或证书路径直接当成现场配置。

## 单机器人自治配置

模板：[edge.env.example](../../deploy/edge-orin/edge.env.example)。`LOCAL_ROBOT_ID` 必须与 Runtime `Info` 返回的 `RobotID` 一致；`ROBOT_CA`、`ROBOT_CERT`、`ROBOT_KEY`、`ROBOT_SERVER_NAME` 构成 Runtime mTLS。建议 `LOCAL_LISTEN=127.0.0.1:8787`，经受控代理访问控制台。Orin NX 本机量化推理服务只监听 loopback，例如 `http://127.0.0.1:8000/v1`；本仓库只调用兼容接口，不下载、量化或加载模型权重。

`AGENT_GOAL_*` 配置注册能力目标规划，`AGENT_INTENT_*`、`AGENT_PLANNING_*`、`AGENT_RECOVERY_*` 分别配置旧意图理解、任务规划和恢复工具选择，并回退到传统 `AGENT_*`。阶段 URL 改变时**不继承**默认模型名或 API Key，必须显式填阶段 `MODEL`，按新端点需要填写 `API_KEY`；本地推理端点可无密钥。某个阶段设 `PROVIDER=deterministic` 就不调用模型。缺 URL 或模型名时启动失败，模型响应大小限制为 1 MiB。控制台显示对应阶段的模型路由；控制台保存的 `AGENT_*` 是默认值，已显式覆盖的阶段保持原配置。控制台切换 URL 时旧 API Key 不会继承，清除同一 URL 上的旧密钥可勾选“清除已保存的 API Key”。

复杂任务可按阶段使用云端只读大模型工具：先在 Fleet 设置 `FLEET_ASSIST_DEVICE_CREDENTIALS=robot-7:<独立随机令牌>`，不要复用 Edge Worker 的数据面令牌；在 Orin 设置 `AGENT_CLOUD_ASSIST_URL=https://fleet.example/v1/assist`、`AGENT_CLOUD_ASSIST_DEVICE_TOKEN` 为此 Assist 令牌、可选 `AGENT_CLOUD_ASSIST_CA`，把对应阶段的 `BASE_URL` 设为这个**完全相同**的 URL，`MODEL` 设为 `cloud-goal`、`cloud-intent`、`cloud-planning`、`cloud-recovery` 或默认 `cloud-assist`。Assist 令牌只能调用固定模型接口，不能取队列或修改任务。Fleet 在云端把别名换成运营方配置的模型；请求为文本、非流式且有大小、输出 token、响应和并发上限。模型只给建议，物理动作照常走审批与证据门禁。云端推理不可用时能否使用确定性解析取决于请求和相应阶段的已有语法；不能保证任意目标或剩余进度都能回退完成。模型故障不产生写权限，Fleet 父 claim 失效后也不能离线执行。

## 云端机群配置

模板：[edge-worker.env.example](../../deploy/edge-orin/edge-worker.env.example)。`EDGE_ROBOT_ID`、`EDGE_DEVICE_TOKEN` 与 Fleet roster 对应；`EDGE_FLEET_URL` 必须是 HTTPS（仅本机开发允许 loopback HTTP）。`EDGE_FLEET_CA` 错误会在启动时拒绝，设备凭据不跟随 HTTP 重定向。`EDGE_FLEET_GRPC` 和 `EDGE_MTLS_*` 建立在线租约、心跳及停止命令链路。`EDGE_RUNTIME_*` 连接本机 Runtime mTLS；`EDGE_RUNTIME_INSECURE` 不允许用于部署预检。新目标在 Worker 私有配置中设置 `AGENT_GOAL_PROVIDER/BASE_URL/MODEL/API_KEY`，由共享 Host 读取本机目录和获准观测后编排；默认 `deterministic` 仅支持已有离线语法，复杂目标和剩余计划需要实际 GOAL 模型。云端 `AGENT_INTENT_*` 与 `AGENT_PLANNING_*` 继续服务旧 `/v1/tasks` 冻结路径。两条路径的动作均经过本地 Guard、执行验证和 Runtime 安全门禁；新目标的目录、预算、claim、子计划与完成证明按[目标指南](../guides/delegated-edge-goals.md)核对。

## 无运动预检、切换与回滚

两条预检只读取配置与 TLS 文件，不连接模型服务器或 Robot Runtime，也不会发送运动命令；成功不等于证书主机名、模型 API 或现场硬件通过认证。

```bash
sudo -u tangying-robot /usr/local/bin/tangying-local-agent --config /etc/tangying-robot-agent-os/edge.env --check-config
sudo systemctl start tangying-orin-local-agent.service
sudo systemctl status tangying-orin-local-agent.service
```

选择 Fleet 模式时，先冻结/完成本地任务，确认无未知物理终态或持物，再停止 Local Agent；预检 Worker 后启动：

```bash
sudo systemctl stop tangying-orin-local-agent.service
sudo systemctl start tangying-orin-edge-worker.service
sudo systemctl status tangying-orin-edge-worker.service
```

两个 systemd 单元都固定共享 `/var/lib/tangying-robot-agent-os` 控制锁目录。手动运行二进制时设置相同的 `ROBOT_CONTROL_LOCK_DIR`；切勿绕开锁或从另一台机器同时连接该 Runtime。回滚时冻结云端派单、停止 Worker、核对 Runtime journal 与现场持物/任务状态，再启动已审阅的旧版本；不删除 SQLite、journal、Fleet 事件、World 快照或 fencing token 来“清空”冲突。

软件预认证与硬件认证项目、复现命令及当前结论见[云边升级验收记录](../production/cloud-edge-upgrade-acceptance.md)与[2026-09-26 现场就绪审计](../production/field-readiness-2026-09-26.md)。
