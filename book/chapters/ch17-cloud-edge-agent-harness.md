# 第 17 章 云端大脑与单机边缘 Agent：同一内核，按角色装配

> **本章复核至 2026-09-26 出版快照。** 第 1–15 章及附录 A 的代码数量、版本叙事与实验数字保留原写作时点；本章描述后续云边升级。可部署的软件候选已经具备角色隔离、分阶段模型路由和容器入口，但还没有在 Orin NX、GPU 大模型服务器、真实机器人或目标规模机群上完成认证。证据见 `docs/production/cloud-edge-upgrade-acceptance.md` 和 `docs/production/agent-harness-docker-acceptance.md`。

## 17.1 两种任务范围

| 形态 | 运行位置与任务权威 | Agent 处理的问题 | 动作落点 |
| --- | --- | --- | --- |
| 单机自治 | Orin NX 的 `local-agent`、SQLite | 一台机器人的意图、规划、恢复与执行闭环 | 本机 Robot Runtime，必须经审批、Guard 和动作后新鲜观测 |
| 云端机群 | 云端 `fleet-control-plane`、MySQL/Coordinator/WorldHub；每台机器人有 `edge-worker` | 云端系统任务、机群状态和待审批草案；Fleet 意图与规划 | Worker 在机器人侧具象化和校验计划，再交给本机 Runtime |

系统 Agent 在云端可以看设备、任务、权威世界摘要和编排指标，并创建 `CREATED` 状态的草案。它不能批准、派单或直接调用 Runtime；operator 仍通过独立审批接口放行。边缘 Agent 只解决它所连接的那台机器人的任务；`edge-worker` 不因此获得系统任务工具。`local-agent` 和 `edge-worker` 不能同时控制同一 Runtime。同机进程按 Runtime 地址共享控制锁；跨主机或同一 Runtime 的地址别名仍需部署方保证单写。

**源码核对**：`fleet/systemagent.go`（鉴权、五个能力与未审批草案）；`internal/controllease/lock.go`、`cmd/local-agent/main.go`、`cmd/edge-worker/main.go`（单机控制锁）。规范：`docs/superpowers/specs/2026-09-25-cloud-edge-brain-upgrade-adr.md`。

## 17.2 一套决策循环，两套 Harness

`internal/agentharness.Profile` 把同一个 `internal/actionloop.Loop` 装配成 `edge` 或 `server`。它在模型看到工具列表之前校验类别、安全级别、是否改变世界、工具名和重复项。边缘可装 `robot.read`、`robot.local`、`robot.write`；服务器可装 `fleet.read`、`fleet.draft`。写工具不会因为换成更强的模型就跨过角色边界。Edge 最多 12 轮、Server 最多 8 轮，仍受审批、范围、证据和无效轮次限制。

这与第 6 章的事件式 `agentruntime` 有不同职责：那里讨论 Task/Ops/Recovery Agent 的注册、发布和订阅；这里讨论**一次模型决策循环可看见什么工具、能做什么动作**。两者共享底层闭环要求，不能把事件总线的订阅者误当成具备物理写权限的 Harness。

| 决策点 | 云端 Server | 单机 Edge |
| --- | --- | --- |
| `INTENT` | Fleet 可独立配置意图模型 | Local 可独立配置意图模型 |
| `PLANNING` | Fleet 可独立配置规划模型 | Local 可独立配置规划模型 |
| `SYSTEM` | 仅 Server，需显式配置 `openai` 才启用系统任务 API | 不存在 |
| `RECOVERY` | 不给 Server 装物理恢复工具 | Local 可独立配置恢复模型；写动作仍需授权 |

`AGENT_<STAGE>_PROVIDER/BASE_URL/API_KEY/MODEL` 覆盖传统 `AGENT_*` 默认值。`deterministic` 阶段不调用模型；`openai` 需要有效 URL 与模型名。非空阶段 URL 指向不同端点时，不继承旧来源的模型名或密钥，须显式指定该阶段模型，密钥按新端点需要配置。这是凭据隔离规则，不只是配置便利。策略模型 `EDGE_POLICY_*` 属于 Runtime 的独立 sidecar，不等同于语言模型路由。

**源码核对**：`internal/agentharness/profile.go`（角色模型、工具校验与轮次）；`internal/modelroute/modelroute.go`（阶段覆盖、换 URL 清密钥与校验）；`cmd/fleet-control-plane/main.go`、`cmd/local-agent/main.go`（角色装配）。配置：`docs/production/configuration-and-security.md`。

## 17.3 Orin 本地小模型与云端推理工具

Orin NX 上的语言模型应作为本机 OpenAI 兼容服务单独部署，按设备内存和时延选择经现场验证的量化权重。本仓库提供路由和容器入口，**不包含量化权重，也不保证某个模型能装入设备**。意图、规划、恢复可以分别选本机端点、确定性路径或云端 Assist。

复杂任务需要云端推理时，Orin 使用专属 Assist 设备令牌访问 `https://<fleet>/v1/assist/chat/completions`。本机路由的 `BASE_URL` 固定为 `https://<fleet>/v1/assist`，模型名是 `cloud-assist` 或 `cloud-intent`、`cloud-planning`、`cloud-recovery` 阶段别名；Fleet 在服务器端把别名映射到真实上游模型，设备拿不到 GPU 模型密钥。Assist 只接受受限文本请求，按机器人鉴权、限制大小和并发；它只返回模型建议，**不会代替审批或 Runtime 的物理证据**。远端模型或网络失败时，不能把未证实的动作说成成功。

**源码核对**：`internal/modelroute/modelroute.go`（仅指定 Assist URL 获得设备凭据）；`fleet/assist.go`（别名和限额）；部署：`docs/install/edge-orin.md`。

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

**源码与复现入口**：`Dockerfile.agent:1-23`、`scripts/agent-entrypoint.sh`、`deploy/edge-orin/compose.yaml`、`deploy/cloud/docker-compose.yml`；操作指南 `docs/install/edge-orin.md` 与 `docs/architecture/fleet-cloud.md`。

## 17.5 证据强度与下一步

软件验收覆盖角色工具隔离、设备/operator 权限、阶段模型路由、草案审批门、Compose 配置、Go/Python/前端回归、重点 race 检查与 Linux arm64 交叉编译；还构建并运行过本机 arm64 Agent 镜像。**这些结果只证明软件候选**，不是 Orin NX 的 JetPack/CUDA/内存认证，不是 GPU 服务真实模型协议或数千机器人容量证明，也不是真实机器人安全放行。

现场继续工作应按这四类证据逐项记录：Orin 的量化权重哈希、内存和 p95/p99；GPU 服务的真实工具调用、并发、限流与失败注入；真实机器人标定、实体急停、断网与结果未知处置；按目标机器人数量对 MySQL、Redis、WorldHub、网关和推理池做长稳与故障恢复。记录须关联 Git commit、镜像 digest、设备与模型制品，不覆盖 `docs/superpowers/specs/2026-09-25-*-adr.md` 或此前失败记录。

**复核材料**：`docs/production/cloud-edge-upgrade-acceptance.md`、`docs/production/agent-harness-docker-acceptance.md`、`docs/superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md`。2026-09-26 的生产就绪复审和现场证据字段另见 `docs/production/field-readiness-2026-09-26.md`；云端部署要求强凭据，模型与 HTTP 策略端点拒绝重定向，Orin 容器通过专用证书组读取私有文件。这些加固仍须在目标设备重建镜像和验证。

## 17.6 每个模型调用点的配置契约

“同一 Agent 内核”表示决策、授权、证据和回放机制复用，不表示所有业务入口都直接调用 `Profile.Run`。当前 Profile 的有界动作循环用于角色化的恢复/系统决策；意图与规划仍通过各自服务接入分阶段路由。Worker 消费 Fleet 任务，不因此成为 Server 系统 Agent。

| 调用点 | 配置范围 | 切换时核验 |
| --- | --- | --- |
| 意图解释 | `AGENT_INTENT_*` | 实体/目标消歧、结构化输出、拒绝未知意图 |
| 规划 | `AGENT_PLANNING_*` | 工具目录、世界基线、参数 schema、计划可执行性 |
| 本地恢复 | `AGENT_RECOVERY_*` | 恢复目录、禁止自动重试标志、范围和批准 |
| 云端系统决策 | `AGENT_SYSTEM_*` | 只读 Fleet 与草案工具，不能自审批 |
| 云端 Assist 上游 | Fleet 的 Assist 配置与阶段别名映射 | 请求协议、容量、每设备鉴权、数据传输边界 |
| 低层策略 sidecar | `EDGE_POLICY_*` | 型号、观测合同、标定、动作边界、停止响应 |

前四项中的 `*` 为 `PROVIDER`、`BASE_URL`、`API_KEY`、`MODEL`。`openai` 是兼容协议适配器名称，不表示只支持某个厂商。不能把意图模型的 JSON 输出能力当作规划工具调用或视觉策略协议已经适配。

配置解析须区分两层：`Resolve(map, stage)` 支持显式空值清除继承；当前 `Environment(stage)` 只收集非空的阶段环境变量，所以**在 shell 中设置空的阶段变量不一定清除传统默认值**。更换阶段 URL 会清除未显式指定的旧模型与旧密钥。若要确保本地无密钥，移除全局 `AGENT_API_KEY`，按阶段单独设置，核验实际解析结果而不打印密钥。未知 provider 或缺少 URL/模型会使配置校验失败。

`deterministic` 不调用语言模型，但只具备已实现的规则能力；不能把它当成任意任务的通用离线替代。配置多条路由也不表示运行时已具备自动故障转移。切换模型、端点或云边模式应作为受控变更，保留在途命令与未知结果。

下面是**待填写的配置片段**，模型名必须替换为服务实际加载且验证通过的标识。网络、证书与 Runtime 参数仍按安装指南填写；这不是完整启动配置：

```dotenv
AGENT_PROVIDER=deterministic
AGENT_INTENT_PROVIDER=openai
AGENT_INTENT_BASE_URL=http://127.0.0.1:8000/v1
AGENT_INTENT_MODEL=REPLACE_WITH_VALIDATED_LOCAL_MODEL
AGENT_PLANNING_PROVIDER=openai
AGENT_PLANNING_BASE_URL=https://fleet.example.invalid/v1/assist
AGENT_PLANNING_MODEL=cloud-planning
AGENT_RECOVERY_PROVIDER=deterministic
```

Assist 的设备 ID、令牌、CA 和独立配置 URL 须同时配置，按 `docs/install/edge-orin.md` 操作。不能把占位域名作为可用端点，也不能用 GPU 服务密钥直接替代设备令牌。

## 17.7 Orin NX 的预算与量化评测

量化降低权重存储和部分算子开销，但不自动保证峰值内存、实时性或质量。参数数目、权重位宽、运行框架、上下文长度和并发都影响占用。

以 70 亿参数为**估算例**：仅按每参数 4 bit 计算，原始权重约 3.5 GB（十进制）。这不包括量化比例因子、未量化层、KV cache、工作区、框架及操作系统；不能据此承诺某台 Orin 可以运行。KV cache 还受序列长度、层数、KV 头、精度与并发影响。设备内存通常与其他机器人进程竞争，须保留运行余量。

| 现场必须测量 | 为什么 |
| --- | --- |
| 冷启动、加载耗时与失败行为 | 重启时不能清空动作账本或自动使能 |
| 空闲/峰值内存、上下文与并发 | 能加载不等于长任务不溢出 |
| 完整决策 p50/p95/p99、超时比例 | 首 token 延迟不能代表工具调用完成延迟；分位数也不是最坏时限证明 |
| 功耗、温度、降频、持续运行 | 短时桌面结果不能代表封闭设备热条件 |
| 工具名、参数、拒绝、证据判读 | 量化前后应使用同一分割与判据，检查危险建议而非只看平均准确率 |
| 与感知/导航/控制同时运行 | 本机推理不能饿死关键控制与停止链路 |

云端 GPU 模型同样需要峰值内存、排队、并发和协议测试。数千机器人容量取决于每台请求频率、阶段 token、上下文和任务突发，不应只按 GPU 数量推断。Assist 默认全局并发槽为 16、输出上限为 4096 tokens，请求最多 64 KiB、响应最多 1 MiB，且同设备并发也受限制；这些是软件限额，不是吞吐实测或集群公平调度证明。

## 17.8 信任与数据边界

用户输入、工具文本、物体标签和云端回答都可能含错误或诱导内容。把它们放入模型上下文不应授予新的工具、审批身份、设备凭据或坐标控制权限。工具 schema、角色校验、作用范围与 Runtime 准入由受信代码执行；提示词不是这些约束的替代品。

当前模型客户端与 HTTP 策略客户端拒绝重定向，阶段换端点避免继承旧密钥；这并不构成完整的出网沙箱。运维方仍须控制允许的端点、TLS 信任、环境代理、DNS 和文件访问。Assist 是“无物理写权限的推理服务”，不是“任意调用都没有风险”：上传现场文本、付费推理和资源占用仍有隐私与成本影响。

上传内容应只包含该任务必要的摘要与证据引用，不把完整家庭影像、住址、令牌或操作员信息混入请求。审计保存脱敏的路由、模型与权重/镜像哈希、协议版本、输入输出引用、耗时、token 和判定结果；完整敏感证据置于受控本地存储，另定保留周期。第三方模型服务的数据保留行为须由部署方按所选服务核实。

## 17.9 模式切换和升级演练

1. 记录任务权威、模型版本、Runtime 地址与唯一写者；检查是否有持物、在途或未知命令。
2. 在受控状态停旧 Agent，并保留 journal、数据库、证书和现场证据。旧 Agent 失联不代表它已经停止动作。
3. 新入口先只读核验设备身份、持物、租约、证据新鲜度与任务版本；完成必要对账后再允许新任务。
4. 在隔离仿真验证错误模型名、错误 CA、限流、超时、服务不可达、非法工具及进程重启；实机复测按现场范围和实体安全流程开展。
5. 回滚镜像或模型时，同时验证状态/schema 兼容性。回滚二进制不能让数据库、fencing 或物理世界倒退。

## 17.10 教学练习

**题目**：Orin 本地模型无法完成规划，工程师把 `AGENT_PLANNING_BASE_URL` 改为云端 GPU 地址，保留全局密钥与模型名，并启用了第二个 Worker。列出上线前要纠正的配置和权威问题。

**答案要点**：阶段 URL 改变后须显式设置该阶段模型与正确凭据；优先通过专属 Assist 凭据访问受限端点并校验 CA。不得通过增加 Worker 让同一 Runtime 出现两个写者；同机锁也不覆盖跨主机或地址别名。核验模型协议与工具输出、容量和隐私，处理旧任务/未知结果，重新验证最终执行门禁。更强模型不增加动作权限。

[术语与时钟假设](../appendix/D-glossary-and-assumptions.md) · [参考资料](../appendix/E-references.md)
