# 工作台交互、投影与执行收尾审计

本目录保存 2026-10-28 至 2026-10-29 工作台验收中的有界辅助证据。主实验过程见[上级记录](../README.md)。起始基线由主实验记录为 `79a8c35535064fe0889980d5f37b3127079660e7`；本目录中的修复、测试和页面版本属于其后的工作区中间态，不能一概归到该提交。

这里区分三类材料：HTTP/SQLite/浏览器组件 **fixture**、对正在运行的 Gazebo Runtime 的**只读接口采样**、主实验执行者在真实浏览器中的**页面验证**。本审计子任务没有发出 Gazebo 移动或抓取命令。并发准入测试里的 7 次工具调用是本地模拟工具计数，不能算作 7 次实机或 Gazebo 运动。

## 发现、修复与证据

| 问题 | 已确认的错误表现 | 修复边界与验证 |
|---|---|---|
| 批准成功但入队失败 | `POST approve` 返回 `409 ENQUEUE_FAILED`，任务已批准且仍为 READY；页面原先没有合理的启动重试入口 | 保留批准事实，允许 READY 重试入队。16 个并发批准/重试请求只产生 1 条批准事件及一次模拟执行；执行中和最终态不能重启。见 `fixture-api/before.txt`、`after.txt`、正式 `admission_retry_test.go` |
| 单站导航已经结束，任务解释仍显示运行中 | 成功任务具有匹配的 `verify_arrival_00` 确认观测，但体验投影仍等待放置验证 | 导航路线只在任务 SUCCEEDED 且每个路线目标的当前、匹配到位验证有证据时完成；任意只读工具 CONFIRMED 不会替代到位证据 |
| GOAL 与旧规划器指标混用 | 新能力规划 trace 未进入旧候选统计，缺少规划失败档案入口；旧分数容易被误当跨架构能力评价 | 保留旧字段并明确范围；分别统计 GOAL 客户端尝试、提案、决策拒绝与经验命中。无样本通过率为 null，读取失败不是零；创建前规划失败独立归档，不混入已创建任务分母 |
| 取消被当作失败 | 未批准草案取消也增加 `failedTasks` | 新增 `cancelledTasks`，与 FAILED/RECOVERABLE_FAILURE 分开。页面明确“当前状态”和“已创建任务成功占比（含待执行、取消）”；历史失败次数仍必须从事件账本计算 |
| 准备状态和回放误导 | 已有标定被提示重做；Nav2 不可用被解释为地图不完整；`navigation.pre_position` 显示只读；UNKNOWN 结果旁边出现全部通过措辞 | 展示已有身份绑定标定；单列实际导航依赖并避免建议继续移动；物理动作和未知结果单独标示。投影修复不证明设备已经恢复 |
| 执行收尾与取消竞态 | `LOCAL_RUN_FINISHED` 的 Get→Append 间取消提交，导致 marker 状态过时；能力 runner 已结束但 active 尚在时接受无人处理的取消请求 | 结束快照、marker 持久化、释放 active 受同一生命周期锁保护；异常结束后的收尾窗口拒绝新的悬空取消。两个确定性屏障分别覆盖 Get 后与 Append 持久化前 |
| 人工 ABANDONED 被提示可恢复 | 原 Recovery 对所有 `Reconciled` 一概跳过，导致已放弃路径出现继续按钮 | ABANDONED 明确禁止继续原路径；不重复要求重做不可变对账。HAPPENED/NEVER_ACTED 也不是自动执行授权，仍须通过实际恢复检查。SQLite 重启后保持原 StepStarted 与人工结论 |
| 建图页相机无法显示 | 旧 `/v1/scene/frame` 返回 404，而同源原子相机接口返回新鲜 RGB-D | 改用登记相机接口；第一次修复仍漏掉直接进建图页的空 adapter，过程见下文。最终验证绑定 robot/source/变换版本/时间并严格过期清帧 |
| 成功只读任务仍被描述为搬运/运行中 | `mapping.inventory` 被描述为“到 contract 的任务”；已完成只读卡片的 purpose 仍是“机器人正在执行当前步骤” | 为 `navigation.status`、`mapping.inventory`、`mapping.status` 提供明确只读词汇；未知能力兜底不推断目的地或当前执行状态。只读结果返回不表述为机器人已经移动 |

最后一项真实 API 原始投影在 [readonly/readonly-experience-before.json](readonly/readonly-experience-before.json)。HTTP 回归先失败，日志见 [readonly/readonly-experience-regression-before.log](readonly/readonly-experience-regression-before.log)，随后同一断言通过。此项的最终浏览器重测由主实验记录；本目录不把组件测试说成已经部署后通过。

## 建图相机：一次真实漏测

1. 在线 Runtime 下，旧图片缓存接口返回 `404 SCENE_FRAME_UNAVAILABLE`；同一时刻，指定 `gazebo` 和 `gazebo-home_furnished/head-rgbd` 的 `/v1/scene/camera` 返回 200。该对比发生在 Runtime 已恢复之后，不把部署停机当作产品故障。
2. 第一次修复改用了原子 RGB-D 接口，显式 adapter 的真实调用成功，单测也通过。但它的 fixture 错误地假设：`/v1/telemetry?adapter=&history=false` 也会返回 latest。**真实后端按 adapter 分桶，空 adapter 返回 `hasLatest=false`。**
3. 浏览器直接打开 `#mapping` 时，工作台的隐藏 adapter 选择器尚未初始化。因此 005 页面仍无画面。已核对 005 提供的 JS 与本地源码哈希一致，排除了“仍在加载旧页面”这一解释。失败截图保留为 [browser-005-still-unavailable.jpg](camera/browser-005-still-unavailable.jpg)，不能被后续成功截图替换。
4. 第二次修复在 adapter 为空时先读取 `/v1/runtime`，验证其 `RobotID` 等于当前工作空间机器人，再取明确的 `Adapter`。它不从历史 `adapters` 列表中随意选择，也不增加后端无身份默认值。测试改为忠实模拟空桶，并覆盖 Runtime 身份缺失、不匹配。
5. 当前相机有效期为 1000ms，原建图状态轮询为 1500ms。相机改为独立 500ms 轮询、单请求去重，状态轮询仍为 1500ms；过期硬上限没有放宽。离页取消、请求失败、超时、来源变化会清帧，迟到响应不能覆盖新选择，页面只使用经过核验的 blob 图片。
6. 最终空绑定真实调用依次 `/v1/runtime`→明确 adapter 的 telemetry→明确 source 的 camera，均 HTTP 200；记录中 RGB 为 69,351 bytes，读取结束时 age 为 491ms、上限 1000ms。准确源码哈希、时间和请求路径见 [mapping-camera-direct-after-final.json](camera/mapping-camera-direct-after-final.json)。这是一帧采样，不是连续可用率或帧率测量。
7. 主实验执行者在 **006** 浏览器中验证 `hidden=false`、`complete=true`、原始尺寸 `320×240`，并检查 robot/source 身份正确；审计者复看保存截图确认画面可见。[006 页面截图](camera/browser-006-image-visible.jpg) 与 005 失败截图同时保留。截图本身不能证明持续新鲜性或空间定位精度，身份/过期约束另由接口验证和回归覆盖。

第一轮“单测通过但真实页面失败”说明：身份绑定接口的测试替身必须保持真实选择规则，不能替后端补不存在的默认行为。显式参数调用成功也不足以覆盖直接导航进入页面的初始化路径。

## 取消统计和收尾锁的独立复核

`cancelledTasks` 与 `failedTasks` 按**读取时的任务状态**互斥。一个曾 RECOVERABLE_FAILURE、后来被操作员取消的任务，现在进入取消桶；它先前的失败仍在事件中，不能因此从论文失败样本里删除。兼容字段 `endToEndSuccessRate` 的分母仍为全部持久化任务，含待执行、取消，不能解读为“已执行任务的成功率”。GOAL 客户端 ModelCall 尝试也不等于供应商实际收到的 HTTP 请求或计费 token。

收尾复核确认：

- marker 位于 incident 写入尝试之后、active 所有权释放之前；其 state 是当前实际状态，失败/暂停也写 marker，marker 不代表成功。
- Get、Append 与释放 active 共用生命周期锁。并发 Cancel 不能在快照和持久化之间改变本地任务状态；最终态 Cancel 提前返回，不触发额外重放。
- marker 不进入当前 `STATE_CHANGED`/`TOOL_ACTIVITY` 观察者通知分支，未发现由本次 marker 引入的观察者回调重入锁问题。
- 5 秒是审计调用的 context 预算；并非对任意外部实现、锁等待和所有运维收尾总时间的硬实时保证。写入失败仍可能缺少 marker，不能伪装为已成功记录。
- `PAUSED` 状态可早于收尾落盘出现，恢复测试必须等待旧执行所有权释放。归档的首次 `go-race.txt` 保留了过早按状态断言账本不再变化的失败，不把它改写成初次就通过。

本次独立复核未发现新的必须修复的取消统计或 marker 锁边界问题。此结论只覆盖所审查路径和下列测试，不等于全系统无竞态。

## 验证与重现

从仓库根目录运行：

```sh
go test -race ./internal/localapp ./orchestration ./tasks ./console -count=1 -timeout=120s
go test -race ./internal/localapp ./orchestration ./tasks ./console -run 'Test(CancelSerializesWithFinishSnapshotAndAppend|EndedCapabilityRunnerRejectsCancellationDuringIncidentClosure|RunFinishedAuditsActualOutcomeAndDoesNotReplay|CurrentOutcomeMetricsSeparateCancellationFromFailure|CapabilityContractPostconditionIsNotPresentedAsTransport|ReadOnlyCapabilityExperienceNamesReadsWithoutInventingTransportOrProgress|RecoveryReconciliationDoesNotInventPermissionOrCompletion)$' -count=3 -timeout=90s
node --test web/*test.mjs
```

本次结果：四个 Go 包 race 通过；重点回归连续 3 轮通过；全部 Web **569/569** 通过。完整输出在 [verification](verification/)。新增未知能力解释测试在完整 Go 包检查之后补入，已被随后 3 轮定向检查覆盖。测试时间和更早轮次不合并成性能统计。

API fixture 可复制至临时 `.go` 文件后运行，不访问机器人或模型服务：

```sh
cp docs/experiments/2026-10-28-user-workbench-e2e/ui-audit/fixture-api/repro.go.txt /tmp/workbench-ui-api-repro.go
go run /tmp/workbench-ui-api-repro.go
```

`fixture-session` 是上述本地 httptest 自建的公开固定测试值，不是实际工作台凭据。归档源码使用 `.go.txt`，避免被 `go test ./...` 当成新的测试包。

`finish-race/marker_review_test.go.txt` 和 `reproduction.log` 保存未修复窗口的确定性负例。负例发生在未独立提交的中间态，**原 app.go 全文件快照未保存**，不能声称仅 checkout 起始提交即可完整重建该中间态。当前可重复的正式双屏障回归位于 `internal/localapp/cancel_finish_race_test.go`；其源码哈希在本目录清单中固定。

## 归档边界

- [import-manifest.json](import-manifest.json) 列出每份复制材料的来源、字节数和 SHA256；大体积原子相机 JSON、重复全量日志和原始 RGB 图片只列哈希与本机位置，没有复制。
- [source-manifest.json](source-manifest.json) 固定本次复核所用源码/测试的哈希。它描述采样时工作区内容，不冒充一个已发布提交。
- 两份浏览器截图各约 170KB，其余主要是文本日志。未复制 `.env`、真实会话凭据、数据库、损坏 SLAM 地图或运行时模型密钥。
- 测试与只读采样未证明真实厂商硬件、跨机定位、动态避碰或陌生环境全覆盖。物理实验结论及失败样本留在主实验记录中解释。
