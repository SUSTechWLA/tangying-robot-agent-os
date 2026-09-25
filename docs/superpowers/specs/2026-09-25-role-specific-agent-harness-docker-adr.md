# 同一 Agent 核心的云端与边缘 Harness / Docker 升级规范（ADR，2026-09-25）

状态：**软件候选实现，待目标硬件和负载认证**。本 ADR 追加在[云边大脑升级规范](2026-09-25-cloud-edge-brain-upgrade-adr.md)之后，保留原规范与[当时的验收记录](../../production/cloud-edge-upgrade-acceptance.md)。本轮实测和未测项目单独写在[Harness / Docker 验收记录](../../production/agent-harness-docker-acceptance.md)；后续变更须新增记录，不能覆盖历史失败。

## 决策和边界

1. **共享决策内核，按部署角色提供能力。** `internal/actionloop` 保持一个有轮次上限、审批、范围、证据门和决策轨迹的实现；`internal/agentharness` 在运行前校验角色、模型路由和工具类别。Edge 上限 12 轮，Server 上限 8 轮，连续无效决策仍受原内核限制。模型只选择已展示的工具；工具回执和有界数据回传下一轮。换模型不能增加权限。
2. **Edge Harness 管一台机器人。** `cmd/local-agent` 使用 Edge profile。意图、规划、恢复分别配置 `AGENT_INTENT_*`、`AGENT_PLANNING_*`、`AGENT_RECOVERY_*`，可指向 Orin 本机 OpenAI 兼容量化服务，或通过设备专属 Assist 凭据调用云端只读推理。恢复目录中的只读、本地和物理写工具按 manifest 分类；物理写仍受已审批范围、逐次批准、Guard/Runtime 和新鲜观测约束。`edge-worker` 是云端派单模式在机器人端的执行器，不获得 Server Harness 的系统任务工具。
3. **Server Harness 管系统任务。** `cmd/fleet-control-plane` 的 `AGENT_SYSTEM_*` 独立选择云端大模型。仅当配置为 `openai` 时启用 `POST /v1/agent/system`，仅 operator JWT 可调用。它只展示 `fleet.devices.read`、`fleet.tasks.read`、`fleet.world.read`、`fleet.metrics.read`、`fleet.task_draft.create`。读取按页或有界返回，草案通过 Task Service 写入 `CREATED`，需要另一次 operator 审批；系统 Agent 没有 approve、dispatch、Runtime 命令、急停复位工具。原来的 Fleet 意图和规划模型配置仍独立。
4. **同一源码、同一多架构镜像，不共享任务权威。** 根目录 `Dockerfile.agent` 构建 fleet-control-plane、local-agent、edge-worker 三个角色入口；云端 Compose 执行 `server`，Orin Compose 的 `edge` 与 `fleet` profile 二选一。镜像在 amd64/arm64 上分别构建，Orin 使用 host 网络访问本机 Runtime 和量化模型 loopback，两个边缘容器共用持久锁卷，启动前进行无运动 `--check-config`。容器以非 root UID 65534 运行；云端仅挂载服务器证书/密钥，`fleet-up.sh` 将密钥设为 0640 并把其宿主机 GID 传给容器，CA 和机器人私钥保持 0600 且不挂入控制面。边缘容器还使用只读根文件系统和最小能力；证书与 env 文件必须可被 UID 65534 读取。

## 不变量与故障处理

- `AGENT_<STAGE>_BASE_URL` 改到另一个来源时，不继承旧模型名或密钥；本机无密钥服务可显式配置。Server 的 `SYSTEM`、Edge 的 `RECOVERY` 都有独立路由。无有效 Server 系统模型时接口返回 503，不能把确定性模式伪装成可工作的系统大脑。
- 系统草案不是已批准任务。只有原 operator 审批路径能放行，边缘随后重新校验目录、权限、计划、世界版本和 Runtime 状态。云端机群任务与单机 SQLite 任务不能同时控制同一 Runtime；共享文件锁仅覆盖同机相同地址，跨主机控制仍需运维 fencing。
- 模型不可用、工具越权、工具类别与安全级别不符、超大请求或未知物理结果按现有闭环规则拒绝/升级。工具返回数据最多保留有界摘要，防止读取大量机群状态导致无限上下文；不得把摘要当作完整分页结果。
- Docker 部署先固定 Git commit、镜像 digest、模型/量化权重哈希、机器人身份与证书。切换角色时先冻结任务、确认持物与未知物理终态、停旧 profile、再启新 profile；回滚保留 SQLite、Fleet 事件、World 快照、Runtime journal 与锁，不清除证据。

## 验收门禁

软件：角色工具隔离、跨阶段模型路由、operator/设备/Assist 鉴权、草案审批门、工具回执上下文、Compose 角色与卷合同、`make test`、Go race 重点包、Linux arm64 构建、格式与生成检查。目标环境：Orin 上真实容器/量化模型的内存与 p95/p99；GPU 服务器实际工具调用协议与并发；TLS/文件权限、重启/断网/回滚、真实机器人安全、MySQL/Redis/WorldHub 在数百至数千台下的容量及 HA。软件通过不等于这些目标环境已签字。
