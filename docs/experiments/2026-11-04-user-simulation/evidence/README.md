# 可复核实验数据包

本包保存 2026-11-04 四次真实 Gazebo 工作台试验的冻结记录：前两次失败、修复后的两次连续成功。四次使用相同自然语言和严格四站验收要求；world 没有重置，起点、模型规划和代码版本存在差异。因此它们是连续调试及重复执行观察，不能作为独立随机实验或统计可靠率证明。

先读 `campaign-metrics.json` / `.csv` 和各 `audit-NNN/summary.json`。计时一律从 `TASK_APPROVED` 至 `LOCAL_RUN_FINISHED`，不把规划或用户等待时间混入机器人执行时长。模型消耗按 call_id 去重，只用供应商实际 usage；缓存输入属于输入总量，不重复相加，也不是独立账单。RPC 数是已记录的能力 RPC，不能解释为所有 Nav2/中间件通信次数。

| 内容 | 用途 |
|---|---|
| `audit-001` 至 `audit-004` | 原始 summary 和 manifest，失败样本保留原貌 |
| `raw/audit-NNN.tar.xz` | 完整原始审计目录，逐文件 SHA-256 与原始 manifest 一致；长字典无损压缩减少大模型上下文/快照重复 |
| `collectors/` | 对应 manifest 中 collectorSHA256 的精确采集代码；第一轮使用的旧采集器也保留 |
| `raw/inputs-and-navigation.tar.xz` | 批准前计划/遥测、运维对账、UI 导出 trace、Nav2 诊断和观察记录 |
| `odometry/` | 执行期摘要、冻结 JSONL 窗口、停止状态窗口、观察器起始及续接证据 |
| `images.tar.gz` + `images-manifest.json` | 46 个捕获身份中 36 个完整 RGB-D 捕获，共 72 张原始 PNG、3,327,266 字节；只读定向导出，逐张 task/step/capture/SHA 绑定 |
| `versions/` | 初始镜像、agent cohort、导航/gateway补丁及实时代码核验元数据 |
| `tests/` | 本轮选定最终后端、前端、文档、审计器回归日志 |
| `post-restart-traces.json` | UI-only 重启后成功任务摘要稳定、失败任务对账仍保留的记录 |
| `PACKAGE.json` / `VERIFICATION.json` | 本包文件校验清单及已执行的独立复核结果 |

`images-manifest.json` 中 `not_declared` 表示 grounded 等记录本来没有声明图像，未补造图片。图片字节来自本四任务已保留的 SQLite 捕获，不包含整库，不会重新触发感知。身份/哈希通过不等于图像语义已被模型正确理解。

完整全时段 ROS JSONL 原件仅保留在本机 `artifacts/acceptance/user-sim-20261104-001/ros-observations-001.jsonl` 与 `-002.jsonl`。本包包括第 2–4 轮的完整冻结执行窗口（额外包含首尾各约 1 秒）、两份停止窗口及观察器最初 4 行。窗口精确字节及来源前缀哈希均保留；可从归档窗口重算第 2–4 轮的路径与采样覆盖。第一轮观察器晚启动，无有效执行期样本；路径为 null，不能声称零运动。完整原件的前缀哈希若需复核，必须访问上述本地原件。

示例（从仓库根目录运行）：

```bash
python3 -m tarfile -e docs/experiments/2026-11-04-user-simulation/evidence/raw/audit-003.tar.xz /tmp/user-sim-audit-003
gzip -dc docs/experiments/2026-11-04-user-simulation/evidence/odometry/odom-window-003.jsonl.gz > /tmp/user-sim-odom-003.jsonl
python3 docs/experiments/2026-11-04-user-simulation/tools/summarize_odom.py \
  --task /tmp/user-sim-audit-003/audit-003/task-before.json \
  --samples /tmp/user-sim-odom-003.jsonl \
  --output /tmp/user-sim-odom-003-recomputed.json
```

复核已覆盖：4 份原始审计 manifest 全部成员及代码 SHA、72 张图片 SHA、3 个任务窗口完整重算、2 个停止窗口、两份 UI 导出 trace 与对应审计 snapshotSHA256。`PACKAGE.json.files` 校验本包除自身以外的全部文件。打包器仅访问显式允许的任务证据；README 和复核报告在最终组装阶段加入，随后重新生成文件清单。

未纳入浏览器录制 ZIP、cookie/request headers、配置或凭据文件、浏览器 session/token 存储、二进制、整库以及大截图。任务自身的 session 绑定与 token usage 是审计数据，保留在任务记录中。旧失败任务后来由 Codex 在授权的仿真测试中通过工作台记录 ABANDONED 对账并取消（不是实机现场人工核查）；后续状态单独留存，没有把被冻结的失败样本改成成功。

所有位置/速度/停止指标都来自仿真传感器或里程计采样，不是独立真值、接触传感器证明、实机安全认证或无碰撞结论。
