# 2026-10-18：异构边缘 Agent 的持久收件与有作用域的协作建议

本次沿现有 `edge-exchange`、SQLite journal 和 `SpatialMissionRunner` 扩展。三个机器人身份（UGV、UAV、legged）能够向彼此及云节点发送有任务和坐标依据的覆盖报告，本地读取报告后可以减少重复探索的候选优先级。消息本身不能写入本机 `CoverageLedger`，不能宣告完成，也不能授予控制权。

这项升级验证的是通信与规划接线；`loopback HTTP` 测试不等于实际无线网络、DDS、飞控、足式步态或实机集群测试。现有空间协调器仍为单进程的执行权威，不能在多台机器上分别启动独立协调器后声称拥有全局互斥。

## 发现和实现

已有协议已经具备身份/域绑定、独立凭据、TLS、持久 inbox/outbox、TTL、序号去重、准确回执、发送端重启重试等。主要缺口是原 `GET /v1/exchange/inbox` 只返回最近 100 条历史消息，没有用于可靠消费的分页游标，业务消费者还需自行处理作用域和时效。

新增文件与职责：

| 文件 | 职责 |
| --- | --- |
| `core/exchangecontract/feed.go` | 本地收件游标合同、保留期缺口错误、可选 journal 读侧接口 |
| `middleware/sqlite/exchange_feed.go` | 事务一致的分页和有界快照 |
| `core/exchangecontract/coverage.go` | 任务、地图和目标集合明确的覆盖建议合同 |
| `edge/exchange/coverage.go` | 将持久收件校验成建议视图，保留拒绝原因 |
| `robot/gateway/tangying_robot_gateway/spatial_peer_exchange.py` | 本地 HTTP 客户端、真实 capture 发布适配、现有 explorer 的建议评分包装器 |
| `scripts/run_edge_cooperation_harness.py` | 4 个真实独立进程的通信与 Python 消费实验 |

## 游标、重启与缺口恢复

`GET /v1/exchange/feed?after=0&limit=100` 是本机 admin token 保护的读接口。`after/next/head/prunedThrough/revision` 全部使用 JSON 十进制字符串，避免 JavaScript 大整数精度损失。游标是本机持久收件序号，与每个发送者自己的 `sequence` 不同。精确重发不再占新序号。

SQLite 的 `exchange_feed` 采用 `INTEGER PRIMARY KEY AUTOINCREMENT`。旧数据库启动时按现有 inbox 写入顺序迁移；新收件、其 stream cursor 和 feed 序号同一个事务提交。GC 同一事务删除正文与 feed 行，保存最高被删除的 `feed_pruned_through`；自增高水位保留，因此收件箱全部清空后也不会重用序号。

如果 `after < prunedThrough`，接口返回 HTTP 409 和缺口元数据，禁止静默跳过已丢失消息。覆盖业务的恢复流程是重新调用 `POST /v1/exchange/coverage-view`，取得事务一致的保留数据快照，并重新验证当前任务/坐标/时效。只有 `completeRead=true` 才能将 `cursor` 当作这次完整快照的消费水位。该快照是当前有效建议，不声称重建已删除的历史；完整历史需要外部实验归档。

单次快照最多读取 1000 条收件。若截断，`completeRead=false`、提议集合为空，客户端保留原本的本机规划。此时不能推进完成性假设。数据量持续超过上限时需等待保留策略释放或实现受控分区/快照压缩；本次没有把这个限制隐藏成“已全部读取”。

## 覆盖建议协议

信封仍为 `agent.data.v1`，`kind=observation`，`basis` 必须包含本次读侧要求的 `worldId/frameId/transformRevision/mapId/mapRevision`。payload 为：

```json
{
  "schemaVersion": "agent.coverage.advisory.v1",
  "missionId": "survey",
  "missionRevision": "approved-r1",
  "observedAtMs": 1792296000000,
  "geometryId": "shared-grid-1",
  "observedTargetIds": ["1", "2"]
}
```

本地查询 `agent.coverage.query.v1` 显式传入上述任务身份、完整 basis、geometryId、目标全集 `targetIds`、`maxAgeMs`、`limit`。目标 ID 以字符串跨语言传输；Python 适配器只导出原整数体素 ID 的十进制形式。

读侧依次拒绝已移除身份、校验损坏、同来源/stream 已被新消息替代的报告、过期信封、不匹配坐标依据、不支持的 payload、不同任务修订/geometry、陈旧或未来超限的观测、重复目标和范围外目标。报告是来源与 stream 的最近值；传感器应使用独立 stream。拒绝原因和 messageId 可回溯；过期后的重复 ACK 仍仅表示历史上已持久收件。

输出固定 `advisoryOnly=true`、`grantsExecution=false`、`physicalCompletion=false`。多个来源的 `proposedCoveredTargetIds` 并集可辅助规划，但它不是本机实测真值、融合占据图或物理完成证据。对端身份认证也不证明对端传感器测量真实。

## 与现有单机器人 Runner 接线

下面的接线使用现有 `explorer_factory(snapshot)` 扩展点，不创建第二套执行器：

```python
from tangying_robot_gateway.spatial_peer_exchange import (
    PeerExchangeClient, PeerAdvisedExplorer, coverage_query, publish_capture,
)

client = PeerExchangeClient("http://127.0.0.1:8789", private_admin_token)
query = coverage_query(
    local_ledger, mission_revision="approved-r1",
    basis={"worldId": "site", "frameId": "shared-map",
           "transformRevision": local_ledger.world_revision,
           "mapId": "shared-grid", "mapRevision": "registered-r1"},
)

# 将此 factory 传给已有 SpatialMissionRunner 的 explorer_factory 参数。
# local_explorer_factory 仍负责路径与体形/支撑面的认证。
def explorer_factory(snapshot):
    return PeerAdvisedExplorer(
        local_explorer_factory(snapshot), client, query,
        weight=0.25, journal=peer_advice_journal,
    )

# 在本地 capture 已通过地图/ledger 校验后发布同一个 packet。
# 发布失败应记录并重试相同 evidenceId；不影响本机执行账本。
publish_capture(client, query, measured_capture_packet, ["edge-uav", "edge-legged"])
```

配置的 transformRevision 必须与本机 ledger 的 world revision 对应；接线者必须完成真实坐标注册。地图 revision 不同会拒绝建议，不能只因为 geometry 相同就把两个时刻的地图当成同一观测。

包装器每次提出候选时读取新快照。它仅按与 peer 报告重叠程度给予有限分数惩罚，不删除候选，不修改传入的 `observed_targets`，不覆盖原路径，不调用控制器。`weight=0` 是协作建议评分的消融入口。网络异常、失效视图、超限读侧均回退到本机原分数；本地 Runner 的预算、fresh pose、租约、未知结果隔离和停止确认照常执行。所有目标被 peer 宣称已覆盖时，本机 ledger 仍不能因此变成完成。

本次未把任意自然语言直接转换成跨机任务分配，也未自动打开每台 Local Agent 的此项配置。生产部署应由固定配置组装 factory、任务作用域和现有授权控制通道；云端任务目标仍走现有受控目标委托协议，数据交换不能代替批准。

## 验证与结果口径

执行：

```bash
go test -race ./core/exchangecontract ./edge/exchange ./cmd/edge-exchange ./middleware/sqlite
.venv/bin/python -m pytest -q robot/gateway/tests/test_spatial_peer_exchange.py
.venv/bin/python scripts/run_edge_cooperation_harness.py --output artifacts/experiments/edge-cooperation-NEW
```

4 进程测试覆盖三机器人向 peer/云发送真实 HTTP 请求、持久收件到有效覆盖提议的消费、Python explorer 实际重排、离线云期间其他 peer 继续收件、发送/接收进程重启、分页游标续读、陈旧观测/错误 frame/任务修订/越界目标/过期数据拒绝，以及超限快照空提议。Go 测试另验证重发不占新游标、全清理后游标不重用、GC 缺口、未来游标拒绝、认证与严格参数。Python 测试验证 peer “全覆盖”不能触发 Runner 完成、未知执行仍保持隔离。

首次 campaign `edge-cooperation-20261018-001` 保留失败记录：短 TTL 消息排队后在发送端过期，没有抵达接收端；几个负例共用的 5 秒观测窗口互相干扰。修正实验注入顺序和显式声明窗口后，在新输出目录重跑。001 不能计为通过样本。重复 campaign 是同一通信场景的稳定性检查，不是新的独立环境或机器人动力学实验。

公开输出仅包括去密进程日志、原始任务建议与收发事件、source hashes、二进制摘要；token、配置、SQLite 数据库仅在私有临时目录中存在，退出后删除。真实无线延迟/丢包、ROS2 QoS、跨设备时钟、异构驱动与全局冲突协调仍须分别在部署中验证。

本轮结果：002、003、004 三次相同四进程通信场景均通过，耗时分别 13.893、14.750、13.620 秒；这些时间含构建和固定故障注入等待，不作为算法性能比较。通信相关四个 Go package 的 `-race -count=1` 通过，Python 适配器及原 Runner 共 62 项通过（其中新适配器 21 项）。

源码保留：002–004 的 `recorded-source.tar.gz` 逐文件校验到各自 `summary.json` 的原始 sourceHashes；三次编译产物摘要相同。补充归档 `edge-cooperation-source-20261018-001` 保存 428 个实际 Go 编译依赖/接线源文件、vendor 依赖与许可证、go.mod/go.sum、工具链环境，以及 16 个相关测试源文件。标准库按 Go 版本固定，不复制源码。补充的 005 尝试在运行场景前因 vendored `go list` 无 `Module.Dir` 而中止；已修正归档器并单独校验完整归档，005 不计作通信任务样本。保留其失败脚本与 failure.json，没有覆盖失败历史。
