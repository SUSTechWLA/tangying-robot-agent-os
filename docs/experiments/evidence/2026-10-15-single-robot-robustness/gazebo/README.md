# 2026-10-15：单机器人真实 Gazebo 实验索引

本目录保留小体积、脱敏的实验记录；不是 mock、recorded controller 或真机数据。运行链为实际 Gazebo Harmonic → ROS 2 / RTAB-Map / Nav2 → Runtime → Local Agent。唯一成功启动的物理 episode 为 `1016`，世界和 Runtime 在三项成功任务之间没有重启。Agent 在无活动任务的边界升级，任务数据库、地图及失败检查点保留。

| 样本 | 结果 | 核验范围 |
| --- | --- | --- |
| preparation01 | SUCCEEDED | 标定、完整巡检建图、地图/定位读取；4能力，493.295秒验收总耗时。23.47155米和234帧来自 Provider 建图回执，Agent observation index 为0，不宣称逐内部动作独立图像验收 |
| inspection01 | 创建规划120秒超时，没有任务、没有运动 | 真实模型失败原文和9轮 trace 保留；任务列表及 planning attempt 核对后没有盲目重试 |
| inspection02 | SUCCEEDED，原句修复后独立复验通过 | 7能力、18子步骤、7到位确认；含两段起点确认，跨房移动5次。一次执行期只读故障通过 Task/Ops/Recovery 自动恢复；528.093秒验收总耗时 |
| host-inspection01 | RECOVERABLE_FAILURE | 新 `/v1/objectives` 内部 legacy 回调误触发父任务 Pending 恢复守卫；在实际物理派发前停止，0图像观测。失败任务、原检查点保留 |
| host-inspection02 | SUCCEEDED，修复后独立复验通过 | 同一精确目标的新任务；云中立目标委托 → 边缘规划 → 4能力、10子步骤、4到位确认；250.158秒验收总耗时，父/子账本和完成 proof 独立核对 |

旧地图与当前场景资源身份不一致的首次环境启动也单独保留在 `environment-attempt001-failed.json`，属于任务创建前的环境准备失败。不能把以上不同软件版本、任务类型和准备条件混算成一个成功率样本。没有本轮抓放、无人机、足式或真机测试。

## 数据字段与证据边界

- `tasks.csv`：每次尝试的原句、任务编号、UTC时间、软件二进制SHA、模型路由、结果及验收耗时。创建失败没有任务时间；其 `harness_recorded_utc` 明确来自原始 run-config 文件时间，不冒充服务端任务创建时间。
- `steps.csv`：原父任务 `TOOL_ACTIVITY` 的步骤、命令、起止时间和终态。`delegated-capabilities.csv` 补充持久 child journal 中的能力耗时及失败分类。
- `arrivals.csv`：实际命令绑定的捕获ID、到位残差、原始RGB-D摘要与保管位置。残差相对任务目标，不能视为SLAM绝对定位精度；巡检任务没有实走里程测量，`actual_travelled_m` 保留未测。
- `*-terminal-task.json`、`*-durable-state-*.json`：原始父/子任务和目标检查点；`fault-recovery-chain.json` 保留故障、调查、重读及成功的因果链。
- `raw-evidence-index.json` 与各 `*-manifest.json`：原始响应、图像的SHA及本地位置。完整地图、图像、数据库、二进制和911文件源码快照保留在 `artifacts/experiments/single-robot-robustness-20261015-gazebo-002/`，**没有随 Git 上传**。
- `manifest.json` 覆盖首批导出文件，`supplemental-manifest.json` 对后续只读交接、冻结依赖和复验记录另行索引；原始任务与原 manifest 均未改写。

`host-inspection02` 的地图身份/定位完成谓词只说明地图一致和定位状态。独立验收还核对授权参数、不可变 child 计划、完整房间顺序、实际命令、命令后的新鲜RGB-D、到位残差与重复物理确认。派生投影仅给 child 能力步骤加真实 child ID 前缀，使其对应原始物理子步骤命名空间；原父task、revision、command及观测字节保持不变。child 计划由父目标授权，不是另一份人工批准计划。

## 使用冻结依赖做只读复验

`frozen-revalidate_host_inspection.py` 的共享检查函数依赖已冻结在 `frozen-verification-dependencies/`，摘要见其中的 `frozen-dependencies.json`。`verify_frozen_host.py` 在运行前核对每项SHA，并拒绝 `-O`、`-OO` 或 `PYTHONOPTIMIZE`。不要直接用优化模式执行含断言的冻结验收器。

以下命令从仓库根目录运行。它复制已完成的原始包到新的 scratch，仅在 scratch 写新报告，不联网、不执行机器人动作、不覆盖已有验收结果：

```bash
TASK_REPO_ROOT="$(pwd)"
TASK_VERIFY_DIR="$(mktemp -d /tmp/tangying-gazebo-review.XXXXXX)"
.venv/bin/python - "$TASK_VERIFY_DIR" <<'PY'
from pathlib import Path
import shutil, sys
repo = Path.cwd()
scratch = Path(sys.argv[1])
assert not any(scratch.iterdir()), "scratch 必须为空"
evidence = repo / "docs/experiments/evidence/2026-10-15-single-robot-robustness/gazebo"
raw = repo / "artifacts/experiments/single-robot-robustness-20261015-gazebo-002"
shutil.copytree(raw / "host-inspection02", scratch / "host-inspection02")
shutil.copytree(evidence / "frozen-verification-dependencies", scratch / "scripts")
for name in ("frozen-revalidate_host_inspection.py", "verify_frozen_host.py"):
    shutil.copy2(evidence / name, scratch / name)
PY
cd "$TASK_VERIFY_DIR"
"$TASK_REPO_ROOT/.venv/bin/python" verify_frozen_host.py host-inspection02
```

已实际在全新 scratch 完成该复验：原/复制 manifest SHA相同，结果再次通过；额外的优化模式调用被拒绝。记录见 `frozen-revalidation-execution.json`。只从 Git 获取本目录而没有本地原始包时，不能声称重新核验过未分发的图像或地图。

交接时8次读取均为 Runtime/导航 ready，故障arm在没有活动任务后关闭，原代理累计只注入一次。服务保留供查看：`http://127.0.0.1:8927/`；失败Host01仍保留为 RECOVERABLE_FAILURE。具体PID、所有者session和时间见 `handoff.json`。
