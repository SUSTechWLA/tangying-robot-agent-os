# 第 17 章 云端大脑与单机边缘 Agent：同一内核，按角色装配

> **本章是 2026-09-25 的增量说明。** 第 1–16 章及附录 A 的代码数量、版本叙事与实验数字保留原写作时点；本章描述后续云边升级。可部署的软件候选已经具备角色隔离、分阶段模型路由和容器入口，但还没有在 Orin NX、GPU 大模型服务器、真实机器人或目标规模机群上完成认证。证据见 `docs/production/cloud-edge-upgrade-acceptance.md` 和 `docs/production/agent-harness-docker-acceptance.md`。

## 17.1 两种任务范围

| 形态 | 运行位置与任务权威 | Agent 处理的问题 | 动作落点 |
| --- | --- | --- | --- |
| 单机自治 | Orin NX 的 `local-agent`、SQLite | 一台机器人的意图、规划、恢复与执行闭环 | 本机 Robot Runtime，必须经审批、Guard 和动作后新鲜观测 |
| 云端机群 | 云端 `fleet-control-plane`、MySQL/Coordinator/WorldHub；每台机器人有 `edge-worker` | 云端系统任务、机群状态和待审批草案；Fleet 意图与规划 | Worker 在机器人侧具象化和校验计划，再交给本机 Runtime |

系统 Agent 在云端可以看设备、任务、权威世界摘要和编排指标，并创建 `CREATED` 状态的草案。它不能批准、派单或直接调用 Runtime；operator 仍通过独立审批接口放行。边缘 Agent 只解决它所连接的那台机器人的任务；`edge-worker` 不因此获得系统任务工具。`local-agent` 和 `edge-worker` 不能同时控制同一 Runtime。同机进程按 Runtime 地址共享控制锁；跨主机或同一 Runtime 的地址别名仍需部署方保证单写。

**源码核对**：`fleet/systemagent.go:34-95,208-227`（鉴权、五个能力与未审批草案）；`internal/controllease/lock.go:24`、`cmd/local-agent/main.go:378`、`cmd/edge-worker/main.go:122`（单机控制锁）。规范：`docs/superpowers/specs/2026-09-25-cloud-edge-brain-upgrade-adr.md`。

## 17.2 一套决策循环，两套 Harness

`internal/agentharness.Profile` 把同一个 `internal/actionloop.Loop` 装配成 `edge` 或 `server`。它在模型看到工具列表之前校验类别、安全级别、是否改变世界、工具名和重复项。边缘可装 `robot.read`、`robot.local`、`robot.write`；服务器可装 `fleet.read`、`fleet.draft`。写工具不会因为换成更强的模型就跨过角色边界。Edge 最多 12 轮、Server 最多 8 轮，仍受审批、范围、证据和无效轮次限制。

这与第 6 章的事件式 `agentruntime` 有不同职责：那里讨论 Task/Ops/Recovery Agent 的注册、发布和订阅；这里讨论**一次模型决策循环可看见什么工具、能做什么动作**。两者共享底层闭环要求，不能把事件总线的订阅者误当成具备物理写权限的 Harness。

| 决策点 | 云端 Server | 单机 Edge |
| --- | --- | --- |
| `INTENT` | Fleet 可独立配置意图模型 | Local 可独立配置意图模型 |
| `PLANNING` | Fleet 可独立配置规划模型 | Local 可独立配置规划模型 |
| `SYSTEM` | 仅 Server，需显式配置 `openai` 才启用系统任务 API | 不存在 |
| `RECOVERY` | 不给 Server 装物理恢复工具 | Local 可独立配置恢复模型；写动作仍需授权 |

`AGENT_<STAGE>_PROVIDER/BASE_URL/API_KEY/MODEL` 覆盖传统 `AGENT_*` 默认值。`deterministic` 阶段不调用模型；`openai` 需要有效 URL 与模型名。阶段 URL 指向新来源时，不继承旧来源的模型名或密钥，须显式指定该阶段模型，密钥按新端点需要配置。这是凭据隔离规则，不只是配置便利。策略模型 `EDGE_POLICY_*` 属于 Runtime 的独立 sidecar，不等同于语言模型路由。

**源码核对**：`internal/agentharness/profile.go:45-159`（角色模型、工具校验与轮次）；`internal/modelroute/modelroute.go:36-95`（阶段覆盖、换 URL 清密钥与校验）；`cmd/fleet-control-plane/main.go:70-99`、`cmd/local-agent/main.go:131-143`（角色装配）。配置：`docs/production/configuration-and-security.md`。

## 17.3 Orin 本地小模型与云端推理工具

Orin NX 上的语言模型应作为本机 OpenAI 兼容服务单独部署，按设备内存和时延选择经现场验证的量化权重。本仓库提供路由和容器入口，**不包含量化权重，也不保证某个模型能装入设备**。意图、规划、恢复可以分别选本机端点、确定性路径或云端 Assist。

复杂任务需要云端推理时，Orin 使用专属 Assist 设备令牌访问 `https://<fleet>/v1/assist/chat/completions`。本机路由的 `BASE_URL` 固定为 `https://<fleet>/v1/assist`，模型名是 `cloud-assist` 或 `cloud-intent`、`cloud-planning`、`cloud-recovery` 阶段别名；Fleet 在服务器端把别名映射到真实上游模型，设备拿不到 GPU 模型密钥。Assist 只接受受限文本请求，按机器人鉴权、限制大小和并发；它只返回模型建议，**不会代替审批或 Runtime 的物理证据**。远端模型或网络失败时，不能把未证实的动作说成成功。

**源码核对**：`internal/modelroute/modelroute.go:103-130`（仅指定 Assist URL 获得设备凭据）；`fleet/assist.go:44-84,100-201`（别名和限额）；部署：`docs/install/edge-orin.md`。

## 17.4 一个镜像的三个入口

根目录 `Dockerfile.agent` 从同一源码构建 `fleet-control-plane`、`local-agent` 和 `edge-worker`，由 `scripts/agent-entrypoint.sh` 的 `server`、`edge`、`worker` 入口选择角色。云端 Compose 使用 `server`，Orin Compose 的 `edge` 与 `fleet` profile 分别使用 `edge` 和 `worker`。`edge` 与 `fleet` 是互斥的任务权威，切换前必须处理已有任务、持物和结果未知的动作，再停旧入口、启新入口；数据卷、Runtime journal、Fleet 事件和 World 快照不能为求启动而删除。

```text
云端：nginx → fleet-control-plane [Server Harness] → Fleet 状态/草案
                                         │
                        已审批任务 → 每台机器人的 edge-worker → Runtime

Orin 自治：local-agent [Edge Harness] → 单台 Runtime
                         └─ 可选：本机量化模型 / 云端只读 Assist
```

Orin Compose 使用 host 网络接本机 Runtime 和量化服务的 loopback；容器以非 root 用户运行，根文件系统只读，两个 profile 共享持久控制锁卷。启动前有无运动配置检查。云端 Compose 不把内部 `:8080` 发布到宿主机；对外通过 nginx 入口及 mTLS 机器人链路。现场必须为私有 env、证书、镜像 digest 和模型哈希建立独立记录。

**源码与复现入口**：`Dockerfile.agent:1-23`、`scripts/agent-entrypoint.sh:1-24`、`deploy/edge-orin/compose.yaml:5-49`、`deploy/cloud/docker-compose.yml:30-116`；操作指南 `docs/install/edge-orin.md` 与 `docs/architecture/fleet-cloud.md`。

## 17.5 证据强度与下一步

软件验收覆盖角色工具隔离、设备/operator 权限、阶段模型路由、草案审批门、Compose 配置、Go/Python/前端回归、重点 race 检查与 Linux arm64 交叉编译；还构建并运行过本机 arm64 Agent 镜像。**这些结果只证明软件候选**，不是 Orin NX 的 JetPack/CUDA/内存认证，不是 GPU 服务真实模型协议或数千机器人容量证明，也不是真实机器人安全放行。

现场继续工作应按这四类证据逐项记录：Orin 的量化权重哈希、内存和 p95/p99；GPU 服务的真实工具调用、并发、限流与失败注入；真实机器人标定、实体急停、断网与结果未知处置；按目标机器人数量对 MySQL、Redis、WorldHub、网关和推理池做长稳与故障恢复。记录须关联 Git commit、镜像 digest、设备与模型制品，不覆盖 `docs/superpowers/specs/2026-09-25-*-adr.md` 或此前失败记录。

**复核材料**：`docs/production/cloud-edge-upgrade-acceptance.md`、`docs/production/agent-harness-docker-acceptance.md`、`docs/superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md`。2026-09-26 的生产就绪复审和现场证据字段另见 `docs/production/field-readiness-2026-09-26.md`；云端部署要求强凭据，模型与 HTTP 策略端点拒绝重定向，Orin 容器通过专用证书组读取私有文件。这些加固仍须在目标设备重建镜像和验证。
