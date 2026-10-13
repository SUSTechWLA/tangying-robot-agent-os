# 边缘 Agent 数据交换与云端对接

[开发导航](README.md) · [模块化边界](../architecture/modular-extensibility.md) · [长程协作](../architecture/long-horizon-collaboration.md)

`edge-exchange` 是可独立部署的边缘数据伴随进程。Local Agent、Fleet Worker 的本地业务通过 loopback API 发布数据；另一台边缘 Agent 或云端同事实现相同 HTTP 合同即可收发。它不依赖机器人 Runtime、不拥有运动租约，也不把收到的数据直接灌入任务上下文、地图、任务完成状态。当前任务命令、批准和执行回执仍走原有 Local Agent / Fleet 协议。

这让单个执行 Agent 能独立保持执行权，同时与其他 Agent 交换有身份、时效、空间版本和回溯依据的数据。程序名称为 `edge-exchange`，合同版本为 `agent.data.v1`；新协议没有冒充现有 Fleet 云端已经实现的接口。

## 职责与接口

| 层 | 实现 | 职责 |
| --- | --- | --- |
| 共享合同 | [`core/exchangecontract`](../../core/exchangecontract/contract.go) | 身份、数据类型、TTL、空间基准、确定性内容 ID、Journal 端口 |
| 边缘服务 | [`edge/exchange`](../../edge/exchange/service.go) | 本地发布、认证主体匹配、收件去重、远端回执验证；没有机器人控制依赖 |
| 存储适配器 | [`middleware/sqlite/exchange.go`](../../middleware/sqlite/exchange.go) | WAL / FULL 持久化、事务内分配序号、收件与游标原子提交、数据库身份绑定、过期保留清理 |
| 进程组装 | [`cmd/edge-exchange`](../../cmd/edge-exchange/main.go) | 两个监听器、TLS、独立凭据、受控发送循环、配置检查、信号退出 |

| API | 身份与网络边界 | 语义 |
| --- | --- | --- |
| `POST /v1/exchange/receive` | 网络接收端；TLS 1.3；每个 peer 独立 `Authorization: Bearer ...` | 验证整个 Envelope，原子持久化后返回 Receipt |
| `POST /v1/exchange/publish` | 单独的 literal loopback 管理端；独立 admin token | 接受 Request，由本地身份与持久序号构建 Envelope，先写 outbox |
| `GET /v1/exchange/inbox` | loopback + admin token | 最近 100 个收件，逆写入顺序；包含保留期内的历史数据，消费者仍须检查过期时间 |
| `GET /v1/exchange/status` | loopback + admin token | 本机身份、收件/待发送/已送达/过期计数，以及累计清理计数 |

接收端不暴露本地发布接口；peer token 不能发布本机数据或调用机器人。当前认证使用 TLS 加独立随机 bearer token，**不是 mTLS 实现**。HTTPS 客户端验证服务端证书和名称，禁止跳转以免凭据转发；接收 token 绑定配置中的完整 Agent、robot、domain 身份。云端虚拟主体仍须显式 robotId，例如 `fleet`，不能省略身份字段。

## 发布与数据合同

本地发布请求示例：

```json
{
  "requestId": "mission-42-observation-17-cloud",
  "peerId": "cloud-brain",
  "stream": "mission-status",
  "kind": "mission_status",
  "ttlMs": 60000,
  "payload": {
    "taskId": "mission-42",
    "status": "awaiting-local-verification",
    "evidenceReference": "local-task-event-817"
  }
}
```

`requestId` 必须由生产者生成并持久保存。同一次提交的网络重试复用同一个 ID；不同提交用新 ID。重复 ID 携带不同内容或换了 peer 的完整身份会返回冲突。响应是已经持久化的完整 Envelope，而非最终送达保证。

Envelope 包括：

- `schemaVersion`、内容校验 `messageId`、`sender` / `recipient` 的 `agentId`、`robotId`、`domain`。
- `stream`、`kind`、`sequence`，其中序号是**十进制 JSON 字符串**，范围 1～2^63−1，避免跨语言 2^53 精度损失。每个发送者/接收者/域/stream 独立单调递增。
- `createdAtMs`、`expiresAtMs` 是 Unix 毫秒整数；TTL 为 1 毫秒至 24 小时，接收方允许发送方最多领先 30 秒。设备须维护时钟同步；错误时间拒绝新数据。TTL 不是原始传感器的采样新鲜度，生产者还须在 payload 中保留原始 observedAt、来源和证据引用。
- `payload` 为不超过 64 KiB 的 JSON object，整体 HTTP Envelope 上限为 72 KiB。重复键、尾随 JSON、超过 32 层嵌套、未知顶层字段都会拒绝。
- 允许的数据类型只有 `mission_status`、`evidence`、`observation`、`map_reference`。`command`、`execute`、`lease` 等不属于协议。
- `observation` 与 `map_reference` 必须带 `basis`：`worldId`、`frameId`、`transformRevision`；`mapId` 与 `mapRevision` 成对出现，map_reference 必须带两者。

空间基准只声明来源。接收不同 domain / frame / revision 的数据不会自动合并坐标、替换本机地图或刷新历史证据。消费端必须找到经验证的变换、匹配任务依据，或显式拒绝/请求新证据。仅仅字符串 `frameId=map` 相同不意味着两个机器人的地图同坐标。

## 乱序、重复、重启与保留

SQLite 事务在读取游标之前取得写锁，所以同一 journal 的并发进程不会给一个 requestId 分配两个消息，也不会给同一 stream 分配相同的新序号。数据库绑定完整本机身份；换 Agent/robot/domain 复用旧库会拒绝启动。

1. 本地发布先原子写 outbox 与发送游标，然后才联网。断网、退出或重启不会重建内容 ID。
2. 收件校验认证主体、域、机器人、接收者、版本、校验值和时效，然后原子写 inbox 与接收游标，再返回 `accepted`。同一持久化消息重放返回 `duplicate`，包含完全相同的 ID 和序号。
3. 相同 stream/sequence 但不同内容拒绝为冲突；从未见过的旧序号拒绝为 stale。协议允许跳号，适合状态、证据和引用；它不是要求所有中间事件都必须送达的事件日志。
4. 发送队列只选每个 stream 的最早有效消息，并按 peer 取一个待发项。每个配置 peer 至多一个并发发送；最多 256 个 peer。断连 peer 不会占满待发送批次、阻止健康 peer 通讯。单 peer 的吞吐目标是状态/证据交换，不适合点云、视频或实时控制流。
5. 远端 Receipt 的版本、ID、序号和 disposition 必须全部匹配，才把 outbox 记为送达。丢失 HTTP 响应会重发原消息，接收端去重；错误 ACK 不会删除待发记录。
6. TTL 到期即停止发送新副本。每分钟清理**到期后再保留 24 小时**的 inbox / outbox body，并累计记录清理计数。未送达且过期的数据也会结束保留；这是明确的数据有效期，不是无限保证送达。

准确 ID 回执与 requestId 幂等窗口持续到原消息到期后的 24 小时；清理以后仍保留 stream 游标，旧序号不能重新入库。生产者必须永不把历史 requestId 当新提交复用，超出保留窗口重放同一个 requestId 会视作新发布。应用需要无限期去重的业务事项，应另存不可复用业务 ID 和处理结果；机器人动作必须继续使用执行 journal/租约机制。

容量达到配置上限会显式拒绝新数据，状态计数可用于告警。游标数量也有限额（配置容量的两倍），因此 stream 使用稳定类别，如 `mission-status` / `map-references`，把 taskId 放 payload，不能每次请求发明新 stream。**不要删除或轮换数据库来解决容量问题**，那会丢失身份/序号连续性；先核对过期时间、归档需求与清理计数，必要时调高容量。不可依靠数据回执判定机器人完成动作；完成事实仍需本地执行记录及物理验证证据。

## 云端同事的最小实现

云端作为一个配置 peer，按同一 Request / Envelope / Receipt 合同实现收件及发送。边缘收到云端数据也只存为 advisory inbox；若云端要批准/调整任务，必须走现有受控任务协议，不能在 payload 中塞指令让边缘绕过批准与准入。

与 Go 无关的固定互通样例：

- [`agent-data-v1.json`](../../core/exchangecontract/testdata/agent-data-v1.json)：含中文、HTML 字符、U+2028/U+2029 与超过 2^53 的字符串序号。
- [`verify_agent_data.py`](../../scripts/verify_agent_data.py)：独立 Python 标准库校验实现；执行 `python3 scripts/verify_agent_data.py core/exchangecontract/testdata/agent-data-v1.json`。

内容 ID 为 `sha256:` 加十六进制摘要。计算时去掉 `messageId`，递归按对象键排序，使用紧凑 UTF-8 JSON；字符串按 Go encoding/json 规则转义 `<`、`>`、`&`、U+2028、U+2029；保留 JSON 数字 token 的拼写，序号使用字符串。可选 basis/map 字段按样例和 Go 合同省略空项。这个算法**不是 RFC 8785**。摘要仅用于确定性身份和完整性检查，认证依靠 TLS 与 peer 凭据。

必须测试：错误身份/域、错误目的地、版本不兼容、重复键、同序号不同内容、乱序、过期、未来时钟、丢回执、进程重启和双方退化。不能把收到的 mission_status=`completed` 直接当作本机任务物理完成的依据。

## 部署和验证

构建：`go build -o edge-exchange ./cmd/edge-exchange`。复制 [`exchange.json.example`](../../deploy/edge-orin/exchange.json.example)，配置本机/peer 的实际身份、DNS/SAN 匹配证书、可信 CA、绝对数据库路径和端口。每个 token 文件至少 32 个非空白字节，用随机值生成并设为 0600；admin token 不得提供给其他 Agent，接收 peer token 互不复用。生产网络只开放接收端口，管理端固定 127.0.0.1 或 ::1。

```sh
edge-exchange --config /etc/tangying/exchange.json --check-config
edge-exchange --config /etc/tangying/exchange.json
```

`--check-config` 不打开数据库、监听端口或访问机器人；它检查配置、凭据权限、证书有效期和传输边界。它不证明远端可达、云端已实现协议或真机已通过验收。原生服务模板 [`tangying-orin-edge-exchange.service`](../../deploy/edge-orin/tangying-orin-edge-exchange.service) 使用现有 `tangying-robot` 用户，二进制安装为 `/usr/local/bin/tangying-edge-exchange`；根据部署路径检查 unit 和 JSON 后启用。终止进程会取消在途发送并保留未确认 outbox，等待最多 5 秒关闭 HTTP 服务。

配置示例的路径与 Compose 挂载直接一致：token 放 `deploy/edge-orin/exchange-secrets/`，容器中为 `/etc/tangying/exchange-secrets/`；证书放宿主机 `/etc/tangying/certs/`；数据库为 `/var/lib/tangying-agent/exchange.db`。容器的 0600 token 文件须由 UID 65534 所有；用于原生 systemd 时同一路径下的凭据改由 `tangying-robot` 用户拥有。原生部署若改变数据库目录，需同时调整 JSON 的 `database` 和 unit 的 `StateDirectory`/可写目录，保留原库与序号，不让路径变更生成另一个空库。

只在显式 `allowLoopbackHttp=true` 且所有 HTTP 地址都是 literal loopback 时支持无 TLS 开发运行；`localhost` 名称及远程 IP 不能绕过这个约束。开发模式不能配置容易引起误解的 TLS 证书字段。

复验：

```sh
go test -race ./core/exchangecontract ./edge/exchange ./cmd/edge-exchange ./middleware/sqlite
.venv/bin/python -m pytest -q tests/install/test_edge_exchange_contract.py
.venv/bin/python scripts/run_edge_exchange_harness.py --output artifacts/edge-readiness/20261013/exchange
```

2026-10-13 的本地两进程实验通过离线排队、发送者重启、收件者重启、序号连续和双向数据交换；日志与 summary 位于上面输出目录。该实验为 loopback HTTP 开发模式，未连接机器人，没有任何运动。Go 测试另外覆盖 HTTPS、独立 token、禁止跳转、丢回执去重、错误 ACK、跨连接并发发布、GC 后旧序号拒绝、容量阻断及空间依据保留。它们是通讯合同与软件恢复证据，不能代替实机、实网时延、证书运维和现场机器人联合作业验收。
