# 后续用户任务独立复核：task004 与 task005

实际复核时间为 2026-10-29（Asia/Shanghai）。此目录增补新结果，不修改父目录中 task001 的 45/65 事件历史审计。只读取已保存 API 快照、在线 SQLite backup 和明确给出的模型日志；未请求新模型、未调用任务控制接口、未发物理动作。

|样本|独立结果|计数边界|
|---|---|---|
|task004 `task-fd9e3d68478a5be33f728a8e`|未批准，READY→CANCELLED；3 事件、0 step run、0 capability 派发；草案未变|取消/准入测试通过，未执行机器人路线；不算执行成功或执行失败|
|task005 `task-472131e5530f56e8b6ddd152`|SUCCEEDED；API 前后快照和数据库 41/41 事件完全一致且连续|只读状态查询任务完成，不能算移动路线、探索或物理安全成功|

task005 的顺序为 seq39 `SUCCEEDED`、seq40 `LOCAL_RUN_SUCCEEDED`、seq41 `LOCAL_RUN_FINISHED`，revision=1。三个调用 `navigation.status`、`mapping.inventory`、`mapping.status` 的 call/receipt/verified 身份匹配且 `mutatesWorld=false`；数据库三个 step 均 COMPLETED。无新的独立观测记录；验证的是读取结果及持久收尾，并非传感器闭环动作。批准到成功状态约 309.779 ms，到显式收尾约 321.503 ms，不能用单样本推断总体 p95。

查询成功并不掩盖系统故障：task005 实际读到的 mapping 状态仍是 `failed / no_frames`。task004 没有启动 local run，缺少 LOCAL_RUN_FINISHED 不构成收尾合同失败。

## 用量分母

- task004：3 个唯一调用，3 个 HTTP200，1 个 invalid_decision；输入31,978 + 输出1,663 = **33,641 tokens**，cached 输入20,224已包含于输入。
- task005：1 个唯一调用、1 个 HTTP200；输入9,956 + 输出322 = **10,278 tokens**，cached 输入6,656已包含于输入。
- 本日志快照8行 = 4条 start + 4条 result = **4次调用尝试**，不是8次。合计输入41,934、输出1,985、总43,919、cached输入26,880。每个result按call_id和完整关键字段与任务trace匹配；所有start都有result，未完成数0。

start 中占位的 `request_error` 不等于请求最终失败；invalid_decision仍消耗模型用量。规划发生在任务分配前，ledger的task_id为空，因此通过task planningTrace中的call_id及一致元数据归属任务，不虚构ledger已有task绑定。此日志仅覆盖task004/005，不包括更早的task001。用量来自保留的供应商返回遥测，不是账单或网络抓包认证；output_tokens=4096是上限，cached是输入子集，不把字节数或缺失用量补成token估计。

## 当前状态与历史失败

task005在线backup时持久任务共3个：2 CANCELLED、1 SUCCEEDED。新口径重算为 `cancelledTasks=2 / failedTasks=0 / succeededTasks=1`，但事件历史仍有task001的 `RECOVERABLE_FAILURE`。后来的取消不会删除首次真实执行失败。

旧task004 `metrics.json` 曾将两个CANCELLED计入failedTasks=2；原件哈希及原值保留，未改写成新结果。新 `cancelledTasks` 统计**当前状态**，不是历史失败的替代指标。现有 `endToEndSuccessRate=1/3` 是所有已创建任务的当前成功占比，混合草案、只读任务和物理任务，不能解释成“机器人路线成功率33%”。

本次backup内task001已91事件、task004有3事件、task005有41事件。这些都是采集时点的快照；Ops可继续追加，较早维护快照69及本次91/41都不是永久长度。

## 复核入口

`report.json` 是独立结论；`source-manifest.json` 保留输入大小与SHA256；`task*-event-index.json` 是有界索引，非原始事件的替代品。`model-usage-snapshot.jsonl` 是本次读取的8行原始用量快照，`standard-ledger-report.json` 是仓库独立汇总工具的交叉结果。公开目录不复制数据库、完整重复API正文或凭据。明确allowlist的常见私钥/provider/AWS/bearer模式扫描零命中；运行元数据未完全匿名化。

保留本地原件时可从仓库根复核，输出目录必须不存在：

```sh
.venv/bin/python docs/experiments/2026-10-28-user-workbench-e2e/metrics-audit/new-user-tasks/audit.py --repo "$PWD" --output /tmp/new-user-task-audit-replay
(cd docs/experiments/2026-10-28-user-workbench-e2e/metrics-audit/new-user-tasks && shasum -a 256 -c SHA256SUMS)
```

脚本以只读immutable方式打开已完成的backup，不写活库，不发网络请求。若后续model-usage.jsonl追加，输入哈希和样本范围将不同；本目录快照保持不变。SHA校验仅证明字节一致性，不认证物理动作真实发生。
