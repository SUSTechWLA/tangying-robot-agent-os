# 边缘 Worker 的持久接收、运行契约与退出语义

本文说明 Fleet 模式下单个边缘 Worker 的可靠性实现、部署配置和故障测试。适用代码为 `cmd/edge-worker`、`edge/worker`、`edge/tasksource` 与 `edge/cloudclient`。Local Brain 使用另一入口；两种模式不能同时控制同一个 Runtime。本文记录的软件验证不等于无人机、无人车或足式机器人已经完成实机验收。

## 1. 任务通知与执行授权分开持久化

通知只携带 task ID，表示需要向云端查询。它不能直接授权运动。Worker 使用同一个 SQLite 日志中的两个独立聚合：

| 聚合 | 保存内容 | 恢复含义 |
| --- | --- | --- |
| `edge-notification/<robot digest>` | `edge.notification.v1`、robot ID、当前 task ID | 重启后先处理已接收通知，再拉取下一条 |
| `edge-context/<task and robot digest>` | 审批、任务版本、claim、命令、目录、资源与 fencing token，以及执行阶段 | 决定是否可以首次执行、仅重传完成通知，或必须核对未知结果 |

每次接收通知都会原子提交聚合状态、事件和 checkpoint，写入 `TASK_NOTIFICATION_ACCEPTED`。任务处理返回成功后写入 `TASK_NOTIFICATION_FINISHED` 并清空当前通知。事件保留 task ID，便于串联接收与处理历史。数据库写入失败时不继续查询执行，也不发送支持延迟确认的传输 ACK。

原先固定重试 60 次后放弃通知的行为已移除。云端暂时不可访问时，Worker 保留当前通知，按间隔重试控制面查询，进程重启后继续读取同一条持久通知。此重试不绕过执行 checkpoint：

- `ACCEPTED` 表示已经跨过写入先于执行的边界；如果缺少后续完成证据，再次收到同一 claim 会返回 `ErrHandoffUncertain`，不会重放物理动作。
- `EXECUTED` 只重发准确绑定的云端完成确认。
- `COMPLETED` 不重复执行。

未知物理结果会阻塞该 Worker 当前通知的继续处理，直到云端与现场证据完成核对、原任务状态得到处理。这样可以保留执行所有权，但也会产生队首等待；它不是跨任务的自动公平调度器。

若云端在核对过程中暂时不返回可派发节点，Worker 仍检查此前的 `ACCEPTED`，不能把“没有节点”当作已完成。当前协议允许云端终态结束该通知，因此操作人员必须先确认真实机器人状态，再在云端关闭原任务；终态本身不是停止证据。当前没有额外的、独立于云端任务状态的全局未知运动锁存或现场核对签名接口。

## 2. Redis 延迟 ACK 与 HTTP 边界

`worker.NotificationAcknowledger` 是可选接口：

```go
type NotificationAcknowledger interface {
    AckNotification(context.Context, string) error
}
```

`RedisTaskSource` 在 `Next` 返回 task ID 时保留 Redis receipt；只有 Worker SQLite 提交成功后才调用 `XACK`。读取顺序先检查同一消费者的 pending entries，再读取新消息。沿用既有 stream `<REDIS_STREAM>.<robotID>`、group 和消费者 `edge-<robotID>`，避免升级时丢失原有消费者的待确认消息。不要在有未完成任务时随意修改这些配置。

进程在接收后、SQLite 提交前退出，Redis pending entry 可在重启后重新投递。SQLite 已提交而 ACK 丢失，Worker 优先处理持久通知；之后重复收到 Redis entry 时，云端 claim 与执行 checkpoint 仍阻止重复运动。两边没有分布式事务，协议采用可重复通知、不可重复物理执行的方式处理这个窗口。

HTTP `/NextTask` 当前没有独立 delivery receipt/ACK 协议。HTTP 响应到达后、SQLite 提交前若进程突然退出，边缘端不能独自证明该通知一定会重新投递。SQLite 提交之后的重启恢复已经覆盖；要消除之前的窗口，需要云端提供可确认、可重投递的接收协议。要求通知全流程可恢复的 Fleet 试点应使用受控网络内的 Redis 通道，或先由云端补齐该协议。Redis 不能因此暴露到公网；公网互联仍使用现有 mTLS Link 与 HTTPS 控制面。

## 3. Runtime 运行契约跨进程固定

Worker 启动先执行只读 Runtime 信息发现，获得真实 robot ID，保持此前按 robot ID 派生的数据库路径。随后建立带持久契约校验的连接，在注册到云端和领取任务前再次准入。没有修改旧数据库的默认位置，也没有自动删除旧日志。

`EDGE_RUNTIME_CONTRACT_FILE` 保存 `edge.runtime-pin.v1`，默认是 `<EDGE_EXECUTION_DB>.runtime-contract.json`。它绑定 robot、adapter、adapter version、protocol、完整 profile 摘要、能力目录与默认 safety profile。临时 readiness、available 与 blockers 不属于契约变更。执行时再次读取 Runtime 信息，因此即使进程不重启，也不能在原任务下静默更换 profile、运动限值或目录。

共享 physical readiness 可以来自纯导航机器人的可用导航能力，不能据此推断抓取或其它能力可用。每个工具派发前，Worker 会对最新 Runtime snapshot 调用 `CanExecute(该工具)`；工具缺失、不可用，或 `Available=true` 但仍有 blockers，都会在 Runtime Invoke 前拒绝。随后还要验证物理资源、fencing token 和最新云端 claim。整个长程任务没有被要求所有未来工具始终可用，检查发生在各步骤真正派发之前。

首次部署没有历史数据库时，准入会原子创建并同步契约文件到磁盘；已有文件不自动覆盖。如果已有非空历史数据库但契约文件缺失，普通启动拒绝自动重新绑定，避免丢失 pin 后把新硬件当成旧硬件。首次创建是对当前 Runtime 声明的记录，不是外部安全认证。首次部署应先审核硬件 profile、限值、驱动行为和证书身份，再启动有运动权限的任务。

升级已有日志的部署，完成现场核对后可以单独运行 `edge-worker --admit-runtime-contract` 记录缺失的 pin。该模式只读取 Runtime 信息，在云端注册、领取任务和运动之前退出；它不会替换已存在但不匹配的 pin，也不代表已验证物理 readiness。使用前保留原数据库与契约的审计副本。

升级或更换硬件时，要保留执行数据库和契约文件，核对在途命令与物理状态，记录新旧契约差异并重新进行现场准入。不得通过删除文件、清空数据库或新建任务来绕过未知运动结果。

## 4. 取消与退出需要独立证据

关闭执行 gRPC stream 不证明机器人已经停止。Worker 跟踪当前命令直到清理结束：

1. 任务取消、进程收到退出信号或命令 deadline 到达，使用独立、未被原任务取消的 context 发出受控 `Cancel`。
2. 物理命令的执行 stream 返回传输错误，也会尝试受控 `Cancel`。
3. 等待取消 RPC 完成或 `EDGE_CONTROL_TIMEOUT` 到期，随后释放本地当前命令记录。
4. 即使取消被确认，丢失原始物理结果仍保留为未知；不能从取消 ACK 推断动作之前没有执行。

物理结果不确定会记录 `outcomeUnknown=true`、`automaticRetryForbidden=true`，并保留 `ACCEPTED`。它不会被 Worker 改写成普通的云端执行失败或成功完成。能力服务和子任务 Runner 返回的未知结果也走同一保护路径。已有可验证、带所有权的正常取消仍可完成云端取消流程。

RPC 正常返回也不一定代表物理结果明确。Runtime 的终态如果携带 `EXECUTION_OUTCOME_UNKNOWN`、`POSTCONDITION_OBSERVATION_TIMEOUT`、`RUNTIME_JOURNAL_UNAVAILABLE`、`BACKEND_ERROR` 或停止失败标记，物理步骤仍按未知结果处理，不能降级成普通失败。

Worker 返回前会等待 telemetry 和 claim-renewal goroutine 退出，再由入口关闭数据库和 Runtime client。Link 的发送没有单独的 gRPC message context，因此发送者取消时会取消所属 stream，解除阻塞；接收 goroutine 也随该 stream 结束。Link 随后可按既有逻辑重连。

这里调用的是 Runtime 的受控取消接口，不能替代硬件急停或飞控 failsafe。无人机、无人车、足式机器人的驱动必须分别实现已验收的停止/保持/降落等动作；Worker 不会在超时后自动改为切断电机，也不会自动解除急停。

## 5. 部署配置与兼容变化

| 配置 | 默认值 | 校验 |
| --- | --- | --- |
| `EDGE_STARTUP_TIMEOUT` | `30s` | 1 秒至 5 分钟，约束 Runtime 发现和服务目录读取 |
| `EDGE_CONTROL_TIMEOUT` | `10s` | 1 秒至 1 分钟，约束取消和急停 RPC |
| `EDGE_TELEMETRY_INTERVAL` | `2s` | 100 毫秒至 1 分钟 |
| `EDGE_HEARTBEAT_INTERVAL` | `5s` | 100 毫秒至 1 分钟 |
| `EDGE_LEASE` | `15s` | 1 秒至 5 分钟，至少容纳两个 heartbeat interval |
| `EDGE_RUNTIME_INSECURE` | `0` | 必须是合法布尔值；部署检查拒绝明文 Runtime |
| `EDGE_TASK_SOURCE` | `http` | 只允许 `http` 或 `redis` |
| `EDGE_WORLD_POSE` | 不设置 | 设置时必须恰好为 4 个有限数值 `[x,y,z,yaw]`，不再忽略错误或截断多余值 |

`--check-config` 校验这些配置和既有 TLS 配置，不连接或移动机器人。收到正常退出信号后不再以 `log.Fatal(context canceled)` 报告成崩溃。systemd 示例增加 `TimeoutStopSec=45`，并将数据库与契约文件置于实际允许写入的 `/var/lib/tangying-robot-agent-os`。Docker Compose 使用已挂载的 `/var/lib/tangying-agent`，覆盖相应路径；不要复制一套路径到未挂载的另一种部署环境。

新版本会拒绝以前被忽略的错误 duration、未知任务源和非法 world pose。升级前检查环境文件，备份数据库及契约文件，并保持 robot ID、stream、group 不变。Go 嵌入调用 `Worker.Run` 现在必须提供 robot identity、source、cloud、Runtime 和支持原子上下文写入的 execution store。

## 6. 可回溯验证

新增回归覆盖如下真实边界：

| 故障/场景 | 测试与判定 |
| --- | --- |
| 控制面连续失联超过旧重试上限 | 65 次失败，第 66 次恢复；原通知只接收一次并完成 |
| SQLite 提交失败 | 不 ACK 传输，也不执行任务 |
| 本地提交成功、ACK 返回丢失、进程重启 | 关闭并重新打开 SQLite；先恢复原 task ID，无需新的通知 |
| 命令在执行中取消或 deadline 到达 | 取消 RPC 使用有效 deadline；等待取消尝试结束；原 context 不污染清理 |
| 命令在进入调用前已过期 | 不发生 Runtime Invoke |
| 物理动作返回 stream 错误 | 尝试受控取消、报告未知、保持 ACCEPTED；重启后不重放，不报告普通失败 |
| 能力服务启动后返回值丢失 | 真实 HTTP/SQLite 测试确认云端不被写成普通 FAILED；之后云端无可派发节点也不能释放未知通知 |
| RPC 正常结束但 Runtime 报告物理结果未知 | 5 类终态错误回归仍要求核对，包含停止失败后缀 |
| 导航就绪但抓取工具不可用/有 blockers/缺失 | 最新 snapshot 的逐工具检查拒绝；Runtime Invoke 为 0，ACCEPTED 仍保留 |
| 纯导航 profile，旧 manipulation-ready 字段为 false | 对应导航工具可用时正常通过共享与逐工具检查 |
| Telemetry 或 Link.Send 阻塞 | 退出取消可以解除发送，并等待后台任务退出 |
| Redis 未 ACK 后进程丢失、ACK 已提交但响应丢失 | 真实 Redis 7 的 pending 数量和重复确认验证；无第二条队列消息 |

回归命令：

```bash
go test -race ./cmd/edge-worker ./edge/worker ./edge/tasksource ./edge/cloudclient
EDGE_REDIS_TEST_ADDR=127.0.0.1:<disposable-port> \
  go test -race -v ./edge/tasksource -run TestRedisEnginePendingRestartAndLostAck
```

Redis engine 测试只创建和删除自己的唯一 stream，不执行 FLUSHDB；未指定专用地址时跳过。另有不需要 Redis daemon 的命令 hook 协议夹具测试，不能把该夹具结果当作 Redis 引擎实验。

本次实际日志在 `artifacts/edge-hardware-candidate/20261013/worker/` 下的 `go-race-final.log`、`redis-engine.log` 与 `unknown-outcome-final.log`。新增能力服务测试首次失败，发现云端无可派发节点可能清除未知通知，捕获的首次失败输出保留在 `unknown-outcome-before.log`；补上持久上下文检查后同一回归通过。真实 Redis 试验使用独立 `redis:7-alpine` 临时容器及 loopback 随机端口，试验后移除容器；没有改动已有 Redis 容器。上述结果验证软件接收、恢复和退出协议，没有验证实机刹车距离、无人机降落、足式稳定性、实地网络覆盖或长时间电源故障。

## 7. 最新实际 Fleet 进程兼容性验收

2026-10-13 在升级代码下重新运行原 `scripts/run_fleet_harness.py --scenario normal`。场景为 MuJoCo shared-block 软件夹具、固定 seed 7、Fleet memory store、两个真实 edge-worker 进程、HTTP task source、mTLS Link 和本地 Runtime gRPC。解析与动作策略均为确定性夹具，没有调用新的大模型，也没有启动 Gazebo 或连接真实硬件。

输入为“让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区”。任务 `task-19156932a73eb85b635f2dda` 最终 `SUCCEEDED`，9 项原有不变量全部通过，包含唯一所有者、交接/交付事件各一次、目标位置以及 4 个策略动作的环境确认证据。任务记录时间为 `2026-10-13T14:11:38.624984Z` 至 `2026-10-13T14:11:40.080153Z`；这只是单次任务记录区间，不作为性能或成功率结论。

包装脚本只增加证据保留：在原 harness 停止所建进程后，复制两个 Worker 的 SQLite、运行契约 pin 和进程日志，不复制证书私钥。只读审核显示两个数据库完整性均为 `ok`；每个机器人有且仅有一次 `ACCEPTED → EXECUTED → COMPLETED`，通知均已清空，待发送 outbox 均为 0。机器人 2 收到两次任务通知，但其上下文只接受并执行一次，符合“通知可重复、执行受 checkpoint 约束”的语义。新 pin 与持久通知没有阻断既有正常云边协议。

证据位于 `artifacts/experiments/edge-field-candidate-20261013/fleet-normal/`：`summary.json`、`journal-audit.json`、`process-evidence-manifest.json`、`task.json`、`events.jsonl`、world/intents/devices/experience 和 `process-evidence/`。首次尝试遇到团队并行改动中的 `admittedRobotID` 字段尚未落盘，编译阶段退出且未启动场景，保存在 `attempt-01-build/`；字段落盘后第二次通过。第二次完整记录已移入 `attempt-02-normal/` 保留，顶层目录保存下面的最新复验。正常验收没有注入运动故障，未知结果与断连恢复的结论来自前述专项回归，不能由这次成功案例代替。所有本次创建的进程和临时目录由 harness 回收，没有使用或修改已有 Redis/MySQL 容器。

补齐最新 snapshot 的逐工具 `CanExecute` 检查后再次复验，同样输入的任务 `task-6ddd38ce0342adca7ca12a53` 于 `2026-10-13T14:22:57.07053Z` 创建、`2026-10-13T14:22:58.268229Z` 完成，状态为 `SUCCEEDED`，9 项不变量全部通过；两个 Worker 的持久上下文、outbox 和通知审计结果仍全部通过。相关 Go race 回归记录在 `artifacts/edge-hardware-candidate/20261013/worker/tool-readiness-race.log`，覆盖 Worker、Runtime、edge-worker 入口和架构边界。
