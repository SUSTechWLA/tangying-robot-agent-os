# 云边完成回执与重连边界（2026-10-10）

本轮修复一个可复现的长任务阻断：边缘已成功执行并持久化 `EXECUTED`，云端接收完成、持久化成功并激活下一任务版本，但完成 HTTP 响应丢失。旧实现先用云端当前版本校验旧命令，导致合法完成 outbox 永久卡在版本不匹配，后续任务无法继续拉取。

本文件补充 `execution.context.v1` 的完成协议；[契约入口](../../core/contextcontract/README.md) 同步说明当前回执恢复能力，原来“完成同时激活新版本、响应丢失后必须停止审阅”的限制不再适用于具有精确回执的新 coordinator。历史验收证据保持不变。开发基线为 `e78e00abf`；本文件描述本次未提交增量，最终提交号由集成记录提供。

## 执行身份与完成身份

执行前的检查保持严格：任务和版本、aggregate 与 claim version、批准状态、机器人和 scope、intent/plan digest、目录版本、world basis 与资源 fencing 必须匹配。`ACCEPTED` 必须先以 CAS 持久提交，才能调用物理能力。

完成上报通过现有 `POST /v1/tasks/{id}/intents/{index}/complete` 发送原命令坐标和完整 `contextBasis`。完成的含义是确认既有执行结果，不是申请再次执行。服务端先校验请求中的任务、版本、索引、步骤、机器人、命令和 fence 与 Basis 一致，再执行下列之一：

1. 找到同一 Basis 的不可变完成回执，完成尚未提交的协调收尾，再返回当前快照；不再执行物理动作。
2. 没有回执，继续按当前 active claim 校验、验证结果并完成；旧版本、过期 claim、UNKNOWN 或不匹配上下文不会因此获准完成。

云端成功提交时，在同一 event-store 事务中保存 intent 成功 checkpoint、`pendingCompletion`、完成事件和 `fleet/context-finalization` outbox。完成事件额外保存 `completionReceiptJSON`：

| 字段 | 含义 |
| --- | --- |
| `schemaVersion` | `context.completion.v2`；读取兼容不带收尾描述的 v1 回执 |
| `contextBasis` | 原始完整 `execution.context.v1`，包含其校验 digest |
| `verificationBasis` | 云端原有完成依据，保留 `HARNESS_SATISFIED`、provider 验证、无 world 的 worker 报告等区别 |
| `completedAt` | 已提交完成的时间 |
| `effects` | 原机器人释放 grant、原共享转移 grant、是否发布后释放、原命令是否为最后 intent |

回执以精确 JSON 字符串存入通用事件 payload，避免 uint64 fence 经 `map[string]any` 投影后变成 float64 并丢失低位。读取从原 `claimVersion` 之后开始，按现有 immutable domain-event cursor 分页；只接受该 task aggregate、原 command 的成功 idempotency key、合法完成事件类型及完整匹配的回执。数据库读取失败、回执损坏、不同机器人或不同 digest 一律拒绝，不退回较弱的成功投影视图。

该 digest 用于发现混合版本和内容变化，并非签名。设备 HTTP 身份认证和原有 coordinator authority 检查继续生效。

## 边缘重连

| 持久阶段 | 本轮行为 |
| --- | --- |
| 无 checkpoint | 按现有批准、上下文、实时 claim 与资源校验后提交 ACCEPTED。 |
| ACCEPTED | 结果仍可能未知，停止并要求对账；不调用执行器来探测是否会重复。 |
| EXECUTED | 只重发原完成请求。云端当前 task revision 可以已经更新，由服务端精确回执确认旧命令。 |
| COMPLETED | 不再执行动作；重放或补 ack outbox 安全。 |

本地 `COMPLETED` 写入和 outbox ack 仍保持现有顺序；ack 响应丢失后，重启可读取 `COMPLETED` 再补 ack。并发完成上报不会制造新的 intent 成功事件，旧回执不会改变新版本的 READY/RUNNING 状态。

## 云端收尾的持久恢复

成功事务后的租约释放、共享资源投影、等待 revision 激活和 task 终态更新统一进入可重入协调收尾。完成请求重传、finalize outbox dispatcher 和下一次 `NextIntent` 都复用同一路径。`pendingCompletion` 尚未清除时，`NextIntent` 必须先完成收尾；收尾失败时不领取新命令。

收尾前从 immutable domain-events 重新读取原 receipt，比对 outbox/checkpoint 与整份 receipt 的 fingerprint，不能由队列内容单独赋予权限。各操作都使用已记录的原身份：

| 收尾步骤 | 幂等与拒绝条件 |
| --- | --- |
| 释放机器人租约 | 只以原 resource/owner/token 调用 Release；已不存在、过期或被更新 fence 取代均视为原租约已失效，不释放后来的租约。其他错误保留 pending。 |
| 发布共享资源 | 要求原 world scope；同 fence 同 owner 已发布时跳过，更高 fence 已存在时保持新投影；只有原 grant 仍有效才补写缺失的投影，固定 coordinator resource source 的单调 sequence 拒绝旧写入。 |
| 发布后释放 | 只释放 receipt 内的确切共享 grant，绝不再次 Transfer 或 Acquire。 |
| 激活 revision | 仅原 revision 仍 active 时调用任务库已有幂等激活；已切换新 revision 的旧 receipt 跳过这一步。 |
| 更新 task 终态 | 仅原 revision 的最后 intent、且全部 intent 成功才推进并回读确认持久状态；尊重后来已发生的取消、失败或安全停止，不把新 revision 标成完成。 |

最后以一个事务清除 pending、写 `COMPLETION_FINALIZED`（绑定整份 receipt fingerprint）并生成当前 revision 的 ready outbox，再 ack finalize outbox。crash 在任一步之后均可重入；若 FINALIZED 已写、ack 未确认，只补 ack。ready 通知失联仍由现有 outbox 重传。

自动化范围有意收缩：共享 grant 已失效且从未投影时，无法证明旧所有权仍是当前事实，因此保留 pending 并要求对账。缺失 world writer、world scope 改变、数据库错误或 receipt 不匹配同样停止。资源投影依赖现有固定 `coordinator/resources/<resource>` 权威来源及其单调 fence；不会从其他来源猜测新的 grant。本轮不解决成功事务之前、外部 Transfer 已发生但成功事务尚未持久化的跨存储窗口；那仍属于未闭合的协调状态，不能假称动作结果已落盘。

## 兼容策略

- 历史 `context.completion.v1` 回执：仍需完整 Basis 和不可变成功事件匹配；可确认原结果，但没有 `effects`，因此不能据此补造 v2 的租约/投影/版本收尾。v1 读取兼容不表示历史崩溃窗口已经自动修复。
- 旧客户端未提供 Basis：维持现有 active claim 坐标验证；不能利用新历史回执跨 revision 确认完成，不能借用新版本节点身份。
- 新客户端连接旧 coordinator：服务端可能忽略新增字段；如果旧完成响应丢失且版本已变化，会继续拒绝，本地保持 EXECUTED，不误报成功。需要升级云端才获得本轮恢复能力。
- 升级前已完成的事件没有精确回执：不从旧通用 payload 或聊天记录倒推、补造 Basis。当前 claim 的原有重复完成行为保留；已被取代的版本需要审阅。
- 旧 RUNNING claim 没有 Basis：仍不允许新 worker 自动续跑。
- 上述兼容指 HTTP 消费者和历史 receipt 读取；新增 pending/finalization checkpoint 需要支持 v2 的 coordinator 领导者恢复。旧 coordinator 不理解 pending 字段，不能作为新数据库的混合版本 failover 领导者；部署必须同时升级可接管该数据库的 coordinator，并排除旧版本重新接管。当前没有自动二进制版本互斥或数据库 schema 门禁，不能宣称旧二进制启动会自行拒绝；新 worker 对无 Basis 的旧 RUNNING claim 的拒绝是另一层执行检查。

### Leadership 启动身份修复

部署审查发现，原入口把 `FLEET_COORDINATOR_ID`（默认 `control-plane-1`）直接用作
leadership owner。现有 Redis Acquire 对同 owner 会续租并返回原 token，因此两个
相同配置的进程，包括滚动升级中重叠的新旧进程，可能共用一份 leadership grant。
新入口每次启动都生成独立随机 incarnation，配置 ID 仅作为可读前缀；实际 owner 为
`<配置前缀>/incarnation/<随机值>`。只有当前进程保留该身份用于续租，重启不得沿用。
相同配置的新进程必须等待旧租约到期后重新领取更大的 fence；旧 owner/token 不能
续租或释放替代它的新 grant。租约存储和完成回执中原有精确 owner/token 的释放语义
不变，没有重新获取旧完成命令的租约。

`FLEET_PRODUCTION=1` 现在还要求非空 `REDIS_ADDR`，在打开数据库前拒绝缺失或仅空白
的配置，避免生产入口落入进程内 leadership/resource lease。现有 Compose 已提供
Redis 地址、AOF 和持久 volume，无需为了此修复改变运行命令。该配置检查并不验证远端
Redis 的持久化设置或备份；部署仍须保留租约与 fence 存储，不能清空后续跑旧任务。

此修复只隔离采用新入口的进程身份，**不提供 schema 版本门禁，也不让旧二进制理解
v2 pending/finalization**。旧进程仍可能在新进程退出或租约到期后获得 leadership，
因此升级时必须继续排除旧版本接管。单实例重启可能在旧租约存活期间启动失败，部署
应按现有 restart 策略重试，不能以复用上次 incarnation 来绕过等待。

针对性回归使用生产 `lease.Manager` 的内存实现和可控时钟，调用真实
`Coordinator.WithLeadership/RenewLeadership`：两个默认或相同自定义配置的独立
实例与重启实例无法共享原 grant；到期后才取得新 fence；旧 owner/token 无法影响新
grant。该实现与 Redis 保持“同 owner 续租、不同 owner 拒绝”的契约；测试未启动
Redis/MySQL，不应标注为实际 Redis、HA 或混版部署验收。

本次补修执行 `go test -race ./cmd/fleet-control-plane ./fleet/coordinator ./fleet/lease
./fleet/redis -count=1` 四包通过，日志为
`artifacts/acceptance/long-horizon-20261010/cloud-leadership-incarnation-race.log`。
修改范围仅 cloud 入口、针对性测试和本说明，未变更 Local Agent/Gazebo 执行源码或
原完成回执收尾逻辑。

## 启动、持久配置与审批边界

`cmd/fleet-control-plane/main.go` 把同一持久 repository 作为 coordinator 的
event/checkpoint/outbox store，配置 world、resource lease、catalog 与 leadership
后，每 500 毫秒调用 `DispatchOutbox`。服务启动无需扫描或重新执行物理图；待处理
finalization 从 outbox 恢复，`NextIntent` 还会检查每个 task 的 pending checkpoint。
目前命令入口的 `FLEET_STORE=memory` 是开发配置，进程重启会失去这层证据；需使用
`FLEET_STORE=mysql` 保留 task 与协调状态，并保留对应 world/lease/queue 存储。
本轮 crash 测试使用真实 SQLite 实现测试相同 store 接口，不是该命令入口的 MySQL
部署验收。不要以新建空数据库替代丢失的协调记录并继续旧任务。

`cmd/edge-worker/main.go` 打开 SQLite `EDGE_EXECUTION_DB` 并注入 `ExecutionStore`。
`edge/worker.taskLoop` 每次拉取队列前先调用 `dispatchContextOutbox`，因此完成响应
丢失后无需新物理命令即可重报。持久路径须跨重启保留；同一机器人并发 worker
必须共享该数据库，独立数据库不提供跨进程 CAS 互斥。默认路径以 robot ID 哈希区分，
但相对工作目录变化仍可能指向新数据库，部署应固定绝对路径和机器人身份。
队列通知只是唤醒，不能代替 checkpoint、精确 context 或当前 claim 校验。

`pendingCompletion` 表示已提交成功之后尚未完成的协调收尾，不是等待用户批准。
finalization 只自动激活已确认的 `WAITING_SAFE_POINT` revision，不激活 `PROPOSED`
或 `WAITING_APPROVAL` revision，也不设置 `task.Approved`。旧回执即使在当前 task
批准状态变化后仍可确认既有结果，这不授予新动作权限：`NextIntent` 和 edge 的每次
物理派发仍拒绝未批准或不可派发状态。尚未提交成功的新完成请求则必须通过原 active
claim 及当前批准/内容检查；没有回执时不能用完成重传绕过这些检查。

## ACCEPTED 的未完成对账能力

本轮不把 `StepCompleted` 单独当成恢复授权。现有 StepRun 主要绑定 task/step/idempotency/capability，缺少原 claim 的 ContextBasis、完整调用参数 digest 和验证契约 digest。`Task.Events` 是任务视图，不能作为跨域不可变 journal 的替代来源。即使视图包含 `CAPABILITY_VERIFIED`，也不把 ACCEPTED 自动改为成功。

后续若扩展自动对账，需要在逐调用 durable journal 中绑定原 command、原 claim digest、参数与验证契约，验证不可变 cloud domain-events 来源与顺序，并确认所有子调用均有明确终态且不存在 UNKNOWN/STARTED。对账路径只能恢复已证明的完成、或继续有明确 operation ownership 的只读查询；不得重新派发结果未知的物理动作。还需覆盖持久化失败、证据缺失、混合 claim、事件乱序和每个 crash window。

本轮 receipt 解决已提交完成的传输重试，不宣称任意 ACCEPTED 崩溃可以自愈，也不改变旧的物理未知结果禁止自动重放规则。

成功事务后、收尾前崩溃已由上述持久收尾覆盖；ACCEPTED 未知结果和事务前跨存储窗口仍独立保留，不由成功回执的恢复能力推论为已解决。

## 验证范围

新增测试包括真实 HTTP 连接在完成提交后被切断，云端已激活 revision 2，然后云端任务/协调日志与边缘 checkpoint SQLite 全部关闭重开；旧 outbox 完成确认和重复派发无动作，新版本保持 READY 并可领取。测试同时使用生产量级的 uint64 fence，验证精确数值经过 HTTP 与 SQLite 后不失真。

负向测试检查跨机器人、scope、版本、step、command、fence、claim、execution digest、world、catalog 的拒绝，及分页读取、事件类型/aggregate 错误、回执 schema/digest/验证依据/完成时间损坏、数据库读取错误。原 checkpoint 并发 CAS、UNKNOWN 不重放、各阶段重启、commit/ack 失败测试保留。

新增收尾测试在真正的协调代码写入路径注入 panic，随后关闭并重开 SQLite task repository/coordinator：成功事务后、lease Release 后、revision 激活持久化后、FINALIZED 提交后、task 终态已持久化但 FINALIZED 尚未提交，五个边界均恢复并且成功/收尾事件各恰好一条。每次恢复前都创建一个更晚的 robot lease，验证收尾不误释放；新 revision 只通知一次。共享世界测试使用 SQLite 协调日志及独立 world/lease fixture，覆盖缺失投影补写、更新 fence 不回退、已失效未投影 grant 保持 pending 且拒绝下一命令。测试中没有 Runtime 对象或物理调用。

这些是模拟 provider/命令边界的协议测试，使用真实本地 HTTP 和 SQLite。它们不构成真机或本轮真实 Gazebo 现场验收证据。真实 Gazebo 的任务、故障注入和结果须单独记录。

复现命令：

```sh
go test ./core/contextcontract ./fleet/coordinator ./edge/worker ./edge/cloudclient ./fleet -count=1
go test -race ./fleet/coordinator ./edge/worker ./edge/cloudclient ./fleet -count=1
```

本轮执行结果：`go test ./fleet/coordinator ./edge/worker ./fleet ./edge/cloudclient -count=1` 四包通过；`go test -race ./core/contextcontract ./fleet/coordinator ./edge/worker ./edge/cloudclient ./fleet -count=1` 五包通过；`git diff --check` 通过。并发工作树其他文件短暂缺少 `fmt` import 导致一次编译阻断，所属代理补齐后重新执行上述测试通过；初次大整数测试使用只发放小 token 的 memory lease fixture，改为保持其租约语义、发放生产量级 token 的测试包装后通过，未降低大整数断言。

加入持久收尾后再次执行上述五包 race 命令，全部通过（coordinator 40.523s、worker 5.530s、cloudclient 4.528s、fleet 4.206s、contextcontract 1.711s）。新增 5 个协调崩溃边界与 3 个共享投影场景均通过，`git diff --check` 再次通过。

开发基线 PR [#21](https://github.com/SUSTechWLA/tangying-robot-agent-os/pull/21) 的 head 精确为 `e78e00abfef4384371ed7c588c182d61a4ac987b`；检查时 [CI run 37879691803](https://github.com/SUSTechWLA/tangying-robot-agent-os/actions/runs/37879691803) 的 12 项检查均为 `COMPLETED/SUCCESS`，包括 test、Gazebo contract、ROS build 和 release gate。这是本次新增代码提交前的基线 CI 状态，不代表本增量已在远程 CI 验证。

## 新 Gazebo 验收的故障约束

现有 `scripts/gazebo_read_fault_proxy.py` 启动时要求 loopback、`RuntimeInfo.adapter == gazebo`，并确认目标 service 存在且不修改世界。只在指定只读 service、request ID 前缀 `task-`、arm marker 已创建且故障次数未耗尽时注入；JSONL 记录明确 `forwarded=false`。

新验收应使用独立 arm marker 和新 JSONL 文件，在规划与 baseline 完成后才 arm，确保故障落到任务执行期而非规划、校验读。保留原 task ID、故障 request ID、重试/诊断消息和最终验证证据关联。代理和协议测试不能替代现场实际故障发生记录，也不能作为实体机器人证据。
