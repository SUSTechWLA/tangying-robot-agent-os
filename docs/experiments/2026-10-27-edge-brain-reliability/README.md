# 边缘大脑重复故障实验（2026-10-28）

计划日期：2026-10-27；实际运行日期（Asia/Shanghai）：2026-10-28。目录沿用计划日期，采集器起止时间使用 UTC，Go 原始事件保留 Time 字段中的 +08:00 时区偏移；两者均表示明确的绝对时刻。

类型：有界、确定性软件合同回归；无 Gazebo、无实机、无外部模型推理。执行前已逐项检查 [测试与替身实现](scope-review.md)。

实际执行 10 轮，顶层测试失败 0/60；各轮前后源码摘要一致：True；验收结论：True。

任务结局按实际 ROBOT_EXPERIMENT 日志分别报告；未知效果案例通过测试意味着保持待核对并拒绝重放，不意味着完成了任务。

完整命令、环境和起止时间见 [run.json](run.json)；分母、逐测试耗时与限制见 [summary.json](summary.json)。每轮 result.json 指向原始 go-test-events.jsonl 的 SHA-256；sources 保存实际源文件哈希集合。临时 SQLite 只由 t.TempDir 创建，不保留数据库。

| 轮次 | 软件断言通过 | 源码稳定 | 耗时（秒） | 证据 |
| --- | --- | --- | --- | --- |
| 1 | True | True | 30.350 | [结果](run-01/result.json) · [原始事件](run-01/go-test-events.jsonl) |
| 2 | True | True | 29.839 | [结果](run-02/result.json) · [原始事件](run-02/go-test-events.jsonl) |
| 3 | True | True | 29.591 | [结果](run-03/result.json) · [原始事件](run-03/go-test-events.jsonl) |
| 4 | True | True | 29.960 | [结果](run-04/result.json) · [原始事件](run-04/go-test-events.jsonl) |
| 5 | True | True | 29.787 | [结果](run-05/result.json) · [原始事件](run-05/go-test-events.jsonl) |
| 6 | True | True | 33.393 | [结果](run-06/result.json) · [原始事件](run-06/go-test-events.jsonl) |
| 7 | True | True | 33.960 | [结果](run-07/result.json) · [原始事件](run-07/go-test-events.jsonl) |
| 8 | True | True | 29.643 | [结果](run-08/result.json) · [原始事件](run-08/go-test-events.jsonl) |
| 9 | True | True | 30.408 | [结果](run-09/result.json) · [原始事件](run-09/go-test-events.jsonl) |
| 10 | True | True | 29.709 | [结果](run-10/result.json) · [原始事件](run-10/go-test-events.jsonl) |

重复执行使用：

```sh
python3 scripts/experiment_edge_brain_reliability.py --output <全新证据目录> --repeats 10
```

该脚本限定 4 个包中的 6 个顶层测试，拒绝覆盖已有目录。源码哈希包含维护目录的 Go 源码、go.mod/go.sum 和脚本本身；未执行 Git 操作。

采集器精确原文保存在 [runner-source.py.txt](runner-source.py.txt)，其 SHA 与 sources 清单中的脚本摘要一致。运行完成后仅修正本页的原始日志时区说明；当前维护脚本同步修正该措辞，并自动归档自身源码。原始 Go JSON、各轮结果和汇总未改写。
