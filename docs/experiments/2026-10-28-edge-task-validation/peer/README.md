# 通信与云边故障验收（2026-10-28，Asia/Shanghai）

基线 `a98c0681d524a6db7036a7a5bbe121df6f8e239d`；工作分支 `codex/edge-long-horizon-scalability`。所有场景均为进程内脚本机器人/模型夹具、真实临时 SQLite、回环 HTTP/TLS。没有 Gazebo、ROS、真机、真实 LLM，也没有外网服务。测试 DB 由 Go t.TempDir 清理，本目录只留日志、JSON、复现脚本和差异。

## 实际发现并修复的问题

1. **小批次发送饥饿**：`Dispatch(limit=1)` 面对两个离线 peer 和一个健康 peer，旧实现连续 6 次只选择最老离线 peer，尝试分布 `[6,0,0]`，交付 `0/6`。SQLite 先按完整当前身份选出每 peer 的 FIFO 头；现在 Service 读取至多 256 个配置 peer 的头，再以加锁的进程内游标分配发送预算。修复后首次 6 次发送分布 `[2,2,2]`，健康 peer 的 2 条消息及时交付，离线消息保持待发；双端重启并恢复网络后其余 4 条全部交付，收件历史无重复、同 stream 序号保持 1→2。当前 CLI 调用 limit=1000，原缺陷针对公开 Service 的较小 limit，不应描述为当前 CLI 默认必然饥饿。
2. **ACK 歧义、长度与日志边界**：重复 `disposition`、大小写别名 `Disposition`、4096 字节外的空白/隐藏第二 JSON 曾被接收并错误退役 outbox；未知字段名曾进入错误日志。现在完整读取最多 4097 字节后拒绝超过 4096 字节的回执，检查重复键，要求恰好 4 个精确 wire 字段、严格类型与单个 JSON 文档；错误统一 `invalid acknowledgement body`，不携带远端字段名/值。ACK 的 schema/messageId/sequence/disposition 与发送信封的匹配仍由 Dispatch 检查。

## 公平性与一致性边界

- 游标只在同一 Service 生命周期内生效，重启会重置；**未宣称在每次重启之间持续公平**。持久化的仍是原始信封、序号、inbox/outbox 与 ACK。并发 Dispatch 可重复发送同一原始信封，这是已有 at-least-once 语义；接收端持久去重，不能宣传网络 exactly-once。
- 无 PendingFor 的旧 Journal 保留原 Pending 契约和有界行为；不赋予其新的公平性保证。
- 这些 advisory peer 数据不会直接授予物理执行权限；云边 claim/Basis/revision 权限与 UNKNOWN_OUTCOME/reconciliation 的独立防线通过既有 worker 测试复核。

## 每阶段结果

|阶段|顶层通过/失败|叶场景通过/失败|Go 子进程耗时秒|源码稳定|用途|
|---|---:|---:|---:|---|---|
|baseline|15/0|34/0|5.818|True|升级前既有路径|
|reproduce|0/2|1/5|2.284|True|保留真实失败证据|
|corrected|2/0|6/0|2.838|True|首次修复验证|
|alias-reproduce|0/1|0/1|2.112|True|大小写别名独立失败证据|
|repeated-http-restart|40/0|100/0|8.506|True|4 顶层场景 × 10 次|
|final-packages|65/0|100/0|14.332|True|通信 4 个包定向 race|
|repeated-cloud-reconciliation|80/0|190/0|17.250|True|8 顶层场景 × 10 次|

`reproduce` 与 `alias-reproduce` 为修复前/修复中有意保留的失败，不包含在最终通过率分母。最终验收三阶段为 `repeated-http-restart`、`final-packages`、`repeated-cloud-reconciliation`，总计 185/185 顶层测试执行通过，无跳过。阶段之间有重叠用例，不应当作 185 个不同任务或真实机器人样本。

## 重复场景及断言

- 10 轮三 peer、每 peer 两消息、小批次 limit=1、两个 peer 暂时 503；健康 peer 不饥饿，恢复与四个 SQLite 库重开后共 6 条交付，FIFO 与原 messageId 不变。
- 10 × 6 种 ACK 场景：重复字段、大小写别名、超长空白、隐藏第二 JSON、未知字段、错误字段类型。接收端已持久接受，但发送端拒绝异常回执并保持两条待发；双端重开后首次重发得到 duplicate，下一条再推进，最终两条历史且无重复。
- 10 轮并发 9 次 Dispatch，每个 peer 正好尝试 3 次；错误 ACK 均不退役；invalid limit、旧 Journal 兼容、4096/4097 精确字节边界也复核。
- 10 轮 `TestContextConcurrentWorkersOnlyOneAccepts`：CAS 并发 claim 仅一个接受。
- 10 轮 `TestContextRejectsMixedMissingAndForeignBasisBeforeInvoking`：缺失/跨机器人/跨作用域/旧 revision 等 Basis 不得触发执行。
- 10 轮 `TestPhysicalResultLossCannotBecomeCloudFailureOrReplayAfterRestart`：动作结果未知后保留对账状态，重启不重放动作。
- 10 轮 `TestCompletionResponseLostAfterRevisionActivationSurvivesBothRestarts`：完成回执丢失、revision 已推进、双端数据库重开后仍保留原 claim 身份和完成证据。
- 10 轮 `TestObjectiveResumedClaimReportsUnknownOutcome`：恢复后动作响应丢失，最终预期 UNKNOWN_OUTCOME；每轮实际动作启动 1 次，duplicateStarts=0。这是正确待对账，不是物理完成。
- 10 轮 `TestObjectiveLostReceiptResumesAfterWorkerRestartWithoutActionReplay`：提交前/后完成回执丢失，重启只补交证据，不重放动作。
- 10 轮 `TestWorkerRefusesClaimFromSupersededTaskRevision`：旧任务版本不得执行。
- 10 轮 `TestControlPlaneOutageDoesNotDiscardNotificationAfterSixtyRetries`：65 次失败后恢复，第 66 次收敛，通知不丢。

## 可追溯材料

每个阶段包含 `metadata.json`（精确命令、Go/平台、环境覆盖、开始时间）、`source-before.json`、`source-after.json`（Go 源码 + go.mod/go.sum SHA-256）、`go-test-events.jsonl`（原始 go test -json 输出）及 `result.json`（退出码、时间、失败分母和源码变化）。`summary.json` 汇总阶段与原始日志 SHA-256；`exchange-changes.patch` 固化本次 3 个文件的实际变更。所有 phase 目录创建时拒绝覆盖已有证据。

## 精确执行命令

### baseline

```sh
go test -json -race -count=1 -timeout=120s ./edge/exchange ./middleware/sqlite ./edge/worker -run 'Test(RestartLostAcknowledgementAndFIFO|OfflineBacklogDoesNotBlockHealthyPeer|ScopedDispatchSurvivesPeerReconfigurationAndRestart|LatestExchangeSnapshot|DurableFeed|ContextConcurrentWorkersOnlyOneAccepts|ContextRejectsMixedMissingAndForeignBasisBeforeInvoking|PhysicalResultLossCannotBecomeCloudFailureOrReplayAfterRestart|CompletionResponseLostAfterRevisionActivationSurvivesBothRestarts|ObjectiveResumedClaimReportsUnknownOutcome|ObjectiveLostReceiptResumesAfterWorkerRestartWithoutActionReplay|WorkerRefusesClaimFromSupersededTaskRevision|ControlPlaneOutageDoesNotDiscardNotificationAfterSixtyRetries)'
```

### reproduce

```sh
go test -json -race -count=1 -timeout=60s ./edge/exchange -run 'TestDispatchSmallBatchRotatesOfflinePeersAndRestarts|TestMalformedAcknowledgementRetainsOutboxAcrossHTTPRestart'
```

### corrected

```sh
go test -json -race -count=1 -timeout=60s ./edge/exchange -run 'TestDispatchSmallBatchRotatesOfflinePeersAndRestarts|TestMalformedAcknowledgementRetainsOutboxAcrossHTTPRestart'
```

### alias-reproduce

```sh
go test -json -race -count=1 -timeout=60s ./edge/exchange -run '^TestMalformedAcknowledgementRetainsOutboxAcrossHTTPRestart$/case-alias-disposition$'
```

### repeated-http-restart

```sh
go test -json -race -count=10 -timeout=120s ./edge/exchange -run 'TestDispatchSmallBatchRotatesOfflinePeersAndRestarts|TestMalformedAcknowledgementRetainsOutboxAcrossHTTPRestart|TestDispatchConcurrentFairnessAndLegacyJournal|TestAcknowledgementByteLimitIncludesWhitespace'
```

### final-packages

```sh
go test -json -race -count=1 -timeout=120s ./core/exchangecontract ./edge/exchange ./middleware/sqlite ./cmd/edge-exchange
```

### repeated-cloud-reconciliation

```sh
go test -json -race -count=10 -timeout=120s ./edge/worker -run 'Test(ContextConcurrentWorkersOnlyOneAccepts|ContextRejectsMixedMissingAndForeignBasisBeforeInvoking|PhysicalResultLossCannotBecomeCloudFailureOrReplayAfterRestart|CompletionResponseLostAfterRevisionActivationSurvivesBothRestarts|ObjectiveResumedClaimReportsUnknownOutcome|ObjectiveLostReceiptResumesAfterWorkerRestartWithoutActionReplay|WorkerRefusesClaimFromSupersededTaskRevision|ControlPlaneOutageDoesNotDiscardNotificationAfterSixtyRetries)'
```


## 仓库归档与校验

本归档位于 `docs/experiments/2026-10-28-edge-task-validation/peer/`。前面的实验记录与命令从原报告逐字保留；历史命令中的 `/tmp/edge-task-validation-peer-20261028` 是实际采集位置，不表示需要重新生成已有证据。

- `summary.json` 可直接阅读；`exchange-changes.patch.gz` 解压后为逐字节原补丁（完整归档内仍保留原文件名）。压缩保存补丁语法要求的前导/行尾空白，不修改原始证据。
- `raw-evidence.tar.gz` 完整保存原目录的 39 个文件（包含所有修复前失败、修复中验证、最终验证的原始 Go JSON、run.py、source-before/after 清单及原 README），原始内容共 12,973,556 字节；没有裁剪失败日志，也没有数据库。
- `evidence-manifest.json` 记录每个原始文件的路径、字节数、SHA-256，以及归档整体 SHA-256。`SHA256SUMS` 覆盖仓库内归档、清单和辅助文件。
- 归档已逐成员读取校验：文件集合完全相同，39/39 文件的大小与 SHA-256 一致，均为普通文件，无软链接。打包后也重新核对了原目录字节未变。

在仓库根目录运行：

```sh
(cd docs/experiments/2026-10-28-edge-task-validation/peer && shasum -a 256 -c SHA256SUMS)
python3 docs/experiments/2026-10-28-edge-task-validation/peer/verify_evidence.py
```

如需展开审计，请使用一个新建空目录，避免覆盖历史实验：

```sh
peer_audit_dir=$(mktemp -d /tmp/edge-peer-audit.XXXXXX)
tar -xzf docs/experiments/2026-10-28-edge-task-validation/peer/raw-evidence.tar.gz -C "$peer_audit_dir"
```

复现测试应先使用报告中的基线及 `exchange-changes.patch` 核对待测代码，再从项目根目录执行上述各阶段的精确 `go test -json -race ...` 命令，并将新日志写入新目录。`source-before.json`/`source-after.json` 是本次共享工作区的实际源码身份；它们包含同轮其他模块修改，因此不能把单独套用 exchange patch 宣称为全部源码完全一致。`run.py` 保留了当时绝对项目路径和目录拒绝覆盖逻辑；移植执行前应在副本中调整 ROOT/OUT，不修改原归档或覆盖旧阶段。此复现不会调用真实机器人或真实 LLM。

已检查全部 UTF-8 文件、metadata 与测试/脚本：未发现真实密钥或私密环境导出；常见私钥、provider/GitHub token、AWS key、JWT、长字面 bearer 及密钥环境赋值扫描均为零命中。metadata 仅含明确的安全环境覆盖名称、源码路径/哈希和运行平台信息；`secret-must-not-appear` 等为夹具断言标记。该检查不是对任意格式秘密的普适检测保证。归档保留原始本机项目路径和运行元数据，供审计溯源。
