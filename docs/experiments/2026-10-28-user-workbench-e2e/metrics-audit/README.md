# 独立指标审计与只读 task 收尾校验

目录日期沿用2026-10-28实验计划；审计与归档跨2026-10-28–29（Asia/Shanghai）。本子目录只归档指标审计、负例、源码与只读collector。**旧field仿真实验复算不是本轮家庭工作台机器人任务成功的证据。**

|证据|结论|限制|
|---|---|---|
|旧field公开归档独立复算|3/3固定路线、30导航+30确认、6/6停止，原数值不变|旧robot.task deterministic/legacy链；UAV接触覆盖不足，安全指标仍false|
|本轮真实task001，较早45事件快照|RECOVERABLE_FAILURE，UNKNOWN需对账，API/DB45一致|没有显式新finish marker，legacy_no_explicit_finish；不是成功|
|同task001，稍后65事件快照|状态/UNKNOWN仍相同，API/DB65一致|Ops在终态后继续追加；45、57、65均不是永久不变的历史长度|
|LLM记录|4轮、4个唯一model-call、4个HTTP200；1次invalid_decision；供应商总usage47,638 tokens|不是网络抓包或供应商账单认证；请求成功与任务成功分母不同|

父任务曾在中间观察57事件；本collector后一次只读采样已65事件，因此不伪造57快照。此前45快照原件与65快照原件都保留在压缩包中，带原采集时间/哈希，不覆盖旧final或把它改名为永久final。

## 阅读入口

- `field-reanalysis.json`：旧归档183个原成员校验、原值一致性、6项停止严格复算。主任务3/3只属于该旧证据。
- `negative-case-summary.json`：错误identity/NaN/缺robot/不完整分母/矛盾SQLite等负例的before/after结果。完整输入、错误输出和源码在压缩包里。
- `task001-45-report.json`、`task001-65-report.json`：两个时间点的任务收尾、在线SQLite备份比对、观测绑定、usage和时延。
- `task001-independent-metrics.json`：4份wire request字节数/哈希匹配、分阶段延迟复核、分别定义的任务/决策/HTTP分母。
- `collect_task_closure.py`：可复用只读收集器；`test_task_collector.py`与`fixtures/`：14项可移植正负测试（不访问API、不启动机器人）。

## 指标口径

本轮task001任务成功0/1；模型决策拒绝1/4；记录的HTTP非2xx为0/4；供应商有效usage覆盖4/4。输入46,135 + 输出1,503 = 总47,638 tokens；cached输入20,352是输入的子集，不能再次相加。`output_tokens=4096`是每请求输出上限，不是实际用量；context字节数不是tokens估算。无账单价或对照实验，不推算费用金额或KV-cache收益。本地cache_mode=off与供应商implicit cached_tokens并不矛盾。

审批至失败状态转换约181.599秒，navigation.pre_position约180.179秒；4次模型记录时长合计11.647秒。该任务主要在导航可用性等待/超时后进入UNKNOWN，不能把约180秒归因于LLM慢。每capability仅1个延迟样本，p95等于该样本不是总体性能结论。`orchestrationScore=15`不代表物理成功，`llmRejectionCount=0`不能直接替代planningTrace中的决策拒绝计数。

新完整收尾合同为终态 + 最新terminal transition之后且state/revision匹配的LOCAL_RUN_FINISHED；成功还需LOCAL_RUN_SUCCEEDED。再核对API、只读在线SQLite backup中的plan/state/revision/全部事件，以及robot/call/观测身份。稳定两次轮询不能替代显式finish。旧代码并未定义LOCAL_RUN_FAILED，不能把task001缺该事件写成旧合同违规。

## 校验与复用

在仓库根执行：

```sh
python3 docs/experiments/2026-10-28-user-workbench-e2e/metrics-audit/verify.py
(cd docs/experiments/2026-10-28-user-workbench-e2e/metrics-audit && shasum -a 256 -c SHA256SUMS)
.venv/bin/python -m pytest -q docs/experiments/2026-10-28-user-workbench-e2e/metrics-audit/test_task_collector.py
python3 docs/experiments/2026-10-28-edge-task-validation/gazebo/public/verify.py
```

14项collector测试和Ruff均通过，原始输出为`tests.txt`/`tests.xml`/`ruff.txt`；69项field指标/停止测试及Ruff输出在压缩包。此前所有错误负例、失败日志和原始NaN/Infinity输出均保留在包内，未改写成成功。

新task使用新输出目录，例如：

```sh
python3 docs/experiments/2026-10-28-user-workbench-e2e/metrics-audit/collect_task_closure.py --task-id TASK_ID --task-dir artifacts/acceptance/user-e2e-20261028-001/task-002 --journal artifacts/acceptance/user-e2e-20261028-001/local-agent/agent.db --output artifacts/acceptance/user-e2e-20261028-001/task-002-closure --wait-seconds 600
```

退出0仅表示收尾证据验证通过，失败任务也可能完整收尾；必须另看taskSucceeded。退出2表示尚未证实。仅GET授权的127.0.0.1:8897、读取明确给出的任务文件及mode=ro SQLite在线backup；不读console-session/env/config，不调用approve/resume/retry/reconcile，不对活库做checkpoint或写入。备份含WAL的已提交状态；API与DB不一致或当前finish缺失则不通过。collector只证明记录一致性与收尾，不解释图像内容、不独立证明到达目标。

## 归档边界

`raw-evidence.tar.gz`约1.15MB，包含127个有界原始成员；`evidence-manifest.json`逐成员记录原路径、大小、SHA256，已逐成员读取校验。目录不复制旧/tmp/archive全量数据、任何SQLite数据库、WAL/SHM、原始传感器大文件或完整重复轨迹。旧物理原始数据复用`docs/experiments/2026-10-28-edge-task-validation/gazebo/public/evidence.tar.gz`及其独立verify.py。

task001在线backup的哈希与对比结果保留，数据库本体仍在本地实验目录之外的/tmp审计原件，不作公开复制。API45/65快照压缩原件仍包含用户任务、模型prompt和运行元数据，不能称为已完全匿名化的论文数据。只复制明确allowlist文件；常见私钥/provider/AWS/bearer/secret赋值扫描零命中，未读复制真实凭据文件；这不是任意未知秘密格式的普适检测保证。SHA校验只证明字节身份，不认证物理实验或供应商调用真实发生。
