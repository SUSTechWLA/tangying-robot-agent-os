# 闭环验收证据（v0.3.0）

本目录保存 v0.3.0 闭环契约的**原始运行证据**，来自相机工作台（`--perception rgbd --scene tabletop`）上的真实任务，不是手工整理的结果。它记录的是某一次运行，重新克隆后按下面的命令可以重新采集。

| 文件 | 内容 |
| --- | --- |
| `live-closed-loop.json` | 任务 `task-b1df5e4cea456cd203aa85a3`：14 个已确认工具步骤、每个写工具的证据 ID／回执观测 ID／证据来源、采集数量与 RGB SHA-256 校验结果、最终世界状态与运行时场景。 |
| `task.json` | 同一次任务的完整任务记录（事件、意图、状态）。 |
| `experience.json` | 该任务的用户视图：步骤判定与证据文案。 |
| `natural-language-acceptance.json` | 同一条相机工作台上的三条自然语言任务：标准双物品任务、口语说法、以及目标不存在时失败关闭并恢复重试的记录，含每个写工具的证据 ID 与逐条采集哈希校验。 |

## 复现

```bash
make build
bash scripts/sim-stack.sh restart --perception rgbd --scene tabletop
# 在工作台 http://127.0.0.1:8787/ 创建并批准：
#   把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来
```

或用命令行驱动同一条任务并保存证据：

```bash
.venv/bin/python scripts/run_rgbd_acceptance.py --scenario pause-restart
```

## 判据

一次成功的任务必须同时满足：

- 任务终态 `SUCCEEDED`，且每个写工具（`manipulation.pick`、`manipulation.place`、`navigation.navigate`、`recover_to_safe_pose`）在 `TOOL_ACTIVITY` 事件里带非空 `evidenceIds` 与 `receiptObservationId`；
- 每条证据的采集时刻晚于该命令的派发时刻（毫秒精度）；
- 每条采集的 RGB 字节 SHA-256 与观测记录一致；
- 最终世界关系为 `red-cup → right-bin`、`blue-bottle → front-tray`。

缺任一条件时步骤保持未完成、任务进入可恢复失败，而不是记为成功——这正是本版要建立的语义。判定实现见 [`core/closedloop`](../../core/closedloop/closedloop.go)。

这些文件是软件仿真的本机结果，不包含实机、现场制动距离、标定精度或长期可靠性结论。
