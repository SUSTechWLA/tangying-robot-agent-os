# 云端机群与 Orin NX 边缘升级：软件预认证记录

日期：2026-09-25。范围为本次云边双模式升级，设计与回滚约束见 [升级规范 ADR](../superpowers/specs/2026-09-25-cloud-edge-brain-upgrade-adr.md)。本记录随代码提交保留；后续目标环境认证应新增带 commit、设备序列号、模型制品哈希与签字的记录，不覆盖本页的软件结果。

## 软件验证结果

| 项目 | 复现命令 / 范围 | 结果与证据边界 |
| --- | --- | --- |
| 全量软件门禁 | `make test` | 最终重跑退出码 0；Go、Python 主套件及独立仿真/真机边界合同均通过，前端 479 通过、0 失败。Python 可选环境项有跳过；通过不代表 Orin NX/GPU 实机认证 |
| 全量 Go 单元与合同 | `go test ./...` | 通过；含云端/边缘模型路由、设备认证、错误拒绝、同机控制锁、计划物化与千台身份队列隔离 |
| 并发检查 | `go test -race ./agent ./orchestration ./internal/actionloop ./internal/controllease ./internal/localconfig ./internal/modelroute ./cmd/local-agent ./cmd/edge-worker ./edge/cloudclient ./edge/worker ./fleet ./fleet/auth ./fleet/queue ./fleet/redis ./tasks` | 15 个重点包通过；认证包在其余 14 包之后单独补跑 |
| 前端 | `make test-web` | 479 项通过，0 失败 |
| 部署与文档合同 | `.venv/bin/pytest -q tests/deploy/test_deployment_contract.py tests/install/test_readme_contract.py tests/install/test_precheck.py tests/install/test_release_gate.py` | 31 项通过 |
| 协议生成 | `make generate-check` | 通过，生成文件无变化 |
| Linux arm64 | `make edge-orin-build`、`sha256sum bin/orin-arm64/*` | 两个静态 aarch64 二进制交叉编译通过；`local-agent` SHA-256 `4e4e42fcd5eb7dd7ea14ec30960b4e9b0ce34453e4dbc5d41100eadc7a15f1a2`，`edge-worker` `4b47421fc84fc120da83746ede9f01c7f0c5be39197bf9b878371998213ec28f`。不是目标 JetPack/CUDA 兼容证明 |
| 源码格式 | `make lint`、`git diff --check` | 通过；初轮 lint 找到新 Redis 队列文件未 gofmt，已修正并复验 |
| 仿真端到端 | `.venv/bin/pytest -q tests/e2e/test_fleet_handoff.py`、`.venv/bin/pytest -q tests/e2e/test_rgbd_recovery.py` | Fleet 两项交接通过；单机暂停重启与未知物理结果两项通过。首轮合跑为 3 通过、1 失败，根因与修复见下文 |

关键拒绝/故障路径：错误机器人身份、设备令牌、Assist 独立令牌跨机器人或访问任务数据面、跨权限复用令牌、未经配置的模型别名、非文本/流式/过大模型请求、上游拒绝、CA 无效、远端明文 Fleet URL、设备令牌重定向、缺 Runtime mTLS 与同时连接同一 Runtime 地址的两个同机控制器。云端推理工具只返回建议，原有 Guard、Runtime 审批与物理结果证据仍是执行边界。千台测试为内存队列的身份隔离；共享 Redis 连接池消除了每台设备一个连接池的启动配置成本，但没有证明目标环境吞吐。

初轮端到端合跑时，“未知物理结果”场景的 Local Agent 在 Runtime 监听前一次性调用 `Info`，收到 `connection refused` 后退出。该回归来自本轮新增的启动前身份核验。现改为最多 25 秒、每 250 ms 重试 Runtime `Info`；仍须在任务权威接管前核对 RobotID。单独重跑 RGB-D 恢复两项均通过。原始失败及重跑结论同时保留，避免把“只重跑通过”误写成初轮全绿。

完整 `make test` 首次在文档合同发现新 Assist 路由尚未列入 API 参考；补充后文档合同 5 项通过。第二次在仿真栈生命周期测试发现多个独立 Runtime 使用相同 RobotID、不同端口，按 RobotID 的同机锁误拒绝这些合法实例；当时 468 通过、7 失败、9 跳过后主动停止以定位。锁键已改为 Runtime 地址，且加入“同 RobotID、不同 Runtime 地址可并行”的回归测试。后续完整结果以再次执行为准。

第三次全量回归在早段通过 88 项后主动停止：代码复核发现“阶段 URL 改为新端点但未显式填 Key”会继承默认模型的旧 Key。修复为新 URL 必须显式填阶段模型名、阶段 Key 默认清空，并同时覆盖 Local 配置解析与 Compose 注入空值的环境解析。这个安全问题先修复再继续全量验收，不能以中途已通过项代替完成状态。

第四次 `make test` 的 Go 与 Python 主体完成，Python 结果为 **2341 通过、40 跳过、1 失败**（耗时 498.40 秒），因此未进入前端阶段。唯一失败是新增 `deploy/edge-orin/` 后，`tests/install/test_start_all.py` 的部署目标合同清单仍写三项目标；部署文档已经列出 Orin。已将测试清单增为四项、让 Orin 的入口断言检查 `make edge-orin-build`，并修正文档“三个运行位置”的陈旧表述。单独重跑该合同文件 13 项通过。第五次最终 `make test` 全部阶段通过，结果见上表；保留本次失败作为回溯证据。

## 目标环境仍需签字的门禁

| 环境 | 必须获得的实际证据 |
| --- | --- |
| Orin NX | 指定 JetPack、内核、CUDA/推理引擎、量化权重哈希；各阶段本机真实输入的内存峰值与 p50/p95/p99 时延；服务用户权限、冷启动、热重启、断网与模式切换；本机模型失败时的明确降级或拒绝 |
| GPU 大模型服务器 | 与真实 OpenAI 兼容服务的文本/工具调用协议，阶段别名、token 上限和 1 MiB 响应合同；并发/限流、密钥隔离、超时、重启和故障注入；实际机器配置与模型权重哈希 |
| 真实机器人 | Runtime 身份、标定、传感器新鲜度、真实碰撞/刹车/急停、断网归零、未知物理终态、持物恢复与人工接管；按现场范围重复任务并签字 |
| 机群基础设施 | 在目标 MySQL、Redis、WorldHub、网络与推理池上按计划的机器人数量、观测频率、任务混合压测，保存 p95/p99、队列积压、世界新鲜度、连接数、CPU/内存和长稳/恢复记录；单主故障与分片/HA 仍需单独工程和验收 |

因此本次结论是**软件预认证候选**：已提供可部署二进制、配置、故障拒绝与验证入口，但未在 Orin NX、GPU 大模型服务器或目标机群基础设施上执行认证；也未完成真实机器人安全放行。不能把本页解释成数千台生产容量或无人值守运行证明。
