# 2026-10-28 边缘持续运行与成本实验

本目录保留当前流容量、长任务审计性能、规划经验和计量回归的证据。全部为 **Apple M5 / darwin-arm64 上的进程内软件 fixture**；HTTP 模型使用 httptest，机器人使用受控 fake provider，任务/交换恢复使用真实 SQLite。没有本轮 Gazebo、ROS 2、真实 LLM/GPU 或实体机器人测量。

## 1. 来源与复现

- 变更前主分支：`deb36504fee4f7281f1f35d0e434f41da168f075`。
- [计划](../../development/2026-10-28-edge-scalability-plan.md)、[实现与边界](../../development/2026-10-28-edge-scalability.md)。所有实测发生在工作树，精确代码用源文件 SHA 清单标识，不把后续提交反填成当时已经存在的提交。
- 性能原始结果：[summary.json](performance/summary.json)、[README](performance/README.md)、[命令](performance/commands.txt)、[来源/输出清单](performance/artifact-manifest.json)。before/after 测试二进制只留摘要，二进制本体在运行机临时目录，不进入 Git。
- 第一轮整合检查（包含失败）：[summary.json](verification/summary.json)、[全部 Go 源与依赖身份](verification/source-sha256.json)、[精确运行脚本](verification/runner-source.py.txt)、[输出摘要](verification/sha256.json)。每个命令前后检查源码不漂移。
- 最终冻结源码检查：[summary.json](verification-final/summary.json)、[源码 SHA](verification-final/source-sha256.json)、[运行脚本](verification-final/runner-source.py.txt)、[输出 SHA](verification-final/sha256.json)。两组命令均通过，运行前后源码一致。
- 完整任务的研究用摘要：[task-summary.json](task-summary.json)。由现有 `scripts/run_edge_robustness_campaign.py` 的 `extract_records` 拼接 Go 输出分块后提取，包含原日志/提取器 SHA、task ID、结果、调用与上下文指标。

复现最终软件矩阵（使用全新输出目录）：

```bash
python3 scripts/experiment_edge_scalability.py \
  --output artifacts/experiments/edge-scalability-my-run
python3 -m unittest discover -s scripts -p test_model_usage_report.py -v
```

脚本固定两个命令：16 个受影响 Go 包 `-race -skip Campaign`，再单独运行完整 `TestSingleRobot.*Campaign`。单命令 Go timeout 180 秒，外层进程 240 秒；超时/失败留日志，不记作任务成功。整包测试事件、顶层测试和子测试数量分别统计，不将它们当作独立实体任务样本。

最终第一组耗时 39.489 秒：**585 个顶层测试通过、967 个含父/子测试的 pass 事件、1 个 skip**；第二组完整 campaign 整包报告 168.887 秒（外层命令 169.405 秒）：**4 个顶层测试、12 个含父/子测试的 pass 事件**，无失败或未完成事件。两组共 589 个顶层通过，不把其中父子重复计数为独立样本。

补充检查见[命令与输出](checks-final/summary.json)：用量汇总 Python 9 项、文档 Python 23 项、Go 文档测试、31 节书稿来源、三个新脚本 Ruff 和 Go 格式检查。最终 [ARM64 构建](builds-final.json)覆盖 macOS/Linux 的 Local Agent、Edge Worker、Edge Exchange 六个二进制，仅证明编译成功；[初轮构建](builds.json)保留独立来源。没有调用完整发布流水线。[归档摘要](archive-sha256.json)覆盖本目录的最终附件与说明。

## 2. 长任务性能对照

固定 `TestSingleRobotLongHorizonCampaign/persistent_task_01_032_steps`，交替执行固定 before/after 二进制，各 3 次，均不带 race。指标只含初始 `Host.Run`，不含重放已完成结果的耗时。

| 配对轮 | before / ms | after / ms | fixture 动作数 | 重放新增动作/计划 |
| --- | ---: | ---: | ---: | --- |
| 1 | 807.731 | 249.948 | 各 32 | 均 0 / 0 |
| 2 | 785.163 | 245.779 | 各 32 | 均 0 / 0 |
| 3 | 803.859 | 245.041 | 各 32 | 均 0 / 0 |
| 中位数 | 803.859 | 245.779 | — | — |

中位数比约 3.27。样本少且确定性，不能推导总体可靠性置信区间；基准定位的是重复读取/重写事件的工程成本。另一个单次样本 821.012 → 257.805 ms 分别保存，未与配对样本混用。

带 race 的选择性 32→96 campaign 中，96 步初始 Host 25.075458 s；单独 128 步初始 Host 33.612406 s，均成功且重放新增动作/计划为 0。这些是新选择性验收，不是旧 96 步超时的同输入 before/after；不能用“600秒/25秒”宣称加速。最终完整 campaign 结果单列在 verification-final 中。

上面的配对实验发生在父审计优化之后、child writer 优化之前。child 优化的首次完整通过为 168.415 秒，见[原日志](child-writer/full-campaign.jsonl)、[摘要](child-writer/summary.json)；随后精度修复单独列在[变更身份](child-writer/final-precision-fix.json)，最终再次运行完整矩阵，不复用旧通过结论。

最终完整连续任务共享同一个 robot episode 和数据库，保留累计访问历史；48/64 步各注入 3 次确定无效果的拒绝。下表只统计初始 Host.Run，完整测试还验证完成回传和重建后的重放：

| 顺序 | 目标站点数 | 初始 Host / s | 本任务确认动作 | 重启新增动作/计划 |
| --- | ---: | ---: | ---: | --- |
| 1 | 32 | 2.199581 | 32 | 0 / 0 |
| 2 | 48 | 6.122282 | 48 | 0 / 0 |
| 3 | 64 | 14.209507 | 64 | 0 / 0 |
| 4 | 96 | 40.480361 | 96 | 0 / 0 |
| 5 | 128 | 95.388433 | 128 | 0 / 0 |

五个任务累计确认 368 次 fixture 动作。这组仍有明显的历史增长成本；测试通过距离 180 秒上限约 11 秒，不能视为充足的生产性能余量。加上独立 close/reopen，共 6 个成功场景；unknown 1 个保持 RECONCILIATION；三种预算场景各在 16 次动作后 BOUNDED_STOP。后四项是预期停止测试，不是目标完成成功率的样本。

unknown 样本目标 64 站，第 25 写丢响应：只确认前 24 站，进入 RECONCILIATION，重启不重发。预算样本按剩余额度停止。测试通过表示拒绝危险重放或正确停止，不表示这些任务全部完成。

## 3. 规划经验与 token 计量

`TestRecipeHTTPFixtureReuseEliminatesModelRequestsButRefreshesRead` 输出 `RECIPE_EXPERIMENT`：首次 2 次模型 HTTP 请求、1 次新鲜 Provider 读；SQLite 重开后精确复用 0 次模型 HTTP 请求、1 次新鲜 Provider 读；两次物理写均 0。仅验证规划通道节省调用；不以 fixture 中的用量常数声称真实商业费用下降。

recipe 负例涵盖：完整语义字段变化、robot/catalog/model/grants/progress 隔离、无查询/空投影/相似目标只能提示、六读配额不重置、查询取消/权限/预算/无效结果不能回退旧值、存储损坏可降级、CAS 冲突上限、32 条容量与 24 小时时效。

计量测试覆盖：显式输出额度与请求一致、长度截断候选拒绝、每次独立 call ID、并发 start/result 配对、缺失/零/部分/非法 usage、日志失败及残行、汇总去重/冲突/unfinished/孤立 result，以及报告不能通过硬链接覆盖账本。JSONL 没有 prompt、响应原文、URL 或凭据。`usage` 来源是服务报告，非独立 GPU 测量。

## 4. 故障与限制留存

性能探索最初保留了 CPU profile、相同任务原始日志、并发追加和停止/恢复回归。开发中一份 128 测试日志记录的是另一个并行文件尚未写完时的构建失败，后续完成文件组后重跑通过；原失败未删。recipe HTTP 夹具最初使用含点工具原名，修正为实际可逆 wire alias 后验证，这不记作真实机器人故障。

第一轮整合中的 16 包 race 回归通过（581 个顶层测试、960 个含父/子测试的 pass 事件、1 个 skip）；紧随其后的完整 campaign **失败**：32/48/64/96 步子测试依次耗时 3.00/7.69/17.59/49.58 秒，128 步场景已运行约 97 秒时达到整包 180 秒上限。超时栈显示工作 goroutine 仍在执行 SQLite checkpoint 写入，没有证据表明死锁。累计任务的观测与审计历史增大，触发 child journal 全量快照反复读取/序列化的剩余瓶颈。该日志有 6 个 pass 事件，却没有完成全部测试，不能据此记作整组通过。后续复测使用新目录，保留本次原始失败。

唯一跳过项 `TestRun002ActualRecoveryContextReplay` 需要另行提供 `TANGYING_RUN002_CONTEXT_FIXTURE` 指向历史 run-002/final-task.json；本轮未提供该外部文件，不计入通过分母。文档检查先因验证输出尚未生成、后因英文说明被 make 命令扫描误识别而失败；输出完整后调整说明并重查通过。它们与运行时故障分开记录。

补充检查的[初次目录核对](checks/summary.json)也保留失败：生成 catalog 后才新增检查日志，导致附件清单过期；最终先创建完整附件集合，再生成并核对 catalog，没有修改目录检查规则来绕过失败。

[README 声明核对](checks-readme-count/summary.json)发现精简文案时漏掉原有“八类”失败计数，已补回与代码一致的数量后重新运行文档检查；失败日志保留，未修改计数测试。

复核还修复 Local recipe 模式热加载不生效，以及 child writer 重启后追加时可能舍入历史大整数的问题。前者由真实 Settings 更新回调的定向测试覆盖，后者验证关闭重开后再追加仍保留原始整数；均已进入最终矩阵。

软件容量测试不证明无线网络可用；计量日志 fsync 增加客户端开销；当前授权读取和子任务 journal 仍随历史增长。recipe 仅保留验证过的草案，不能证明原模型读取了全部必要业务条件。控制器、停止、标定、跨机地图对齐、动态避碰与真实 API 成本仍按独立实验验收。
