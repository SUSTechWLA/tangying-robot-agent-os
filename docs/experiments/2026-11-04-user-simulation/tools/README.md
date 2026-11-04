# 只读任务采集与巡检验收

`collect_task_audit.py` 仅发送本机 HTTP **GET**，只读取显式传入的任务和 SQLite 数据库。它不创建、批准、恢复、重试、取消或停止机器人任务，不读取配置、环境变量、凭据文件，也不跟随重定向。输出目录必须尚不存在，避免覆盖既有实验。

开始任务前，通过 GET 保存当时 `/v1/telemetry` 的 JSON，用来预注册机器人、地图、标定版本和房间目标。在工作台输入任务并执行完成后，运行以下命令；将端口、任务 ID、数据库路径和预执行遥测文件替换为本轮实际值：

```bash
python3 docs/experiments/2026-11-04-user-simulation/tools/collect_task_audit.py \
  --base-url http://127.0.0.1:8897 \
  --task-id TASK_ID \
  --journal /absolute/path/to/journal.sqlite \
  --preflight-telemetry /absolute/path/to/preflight-telemetry.json \
  --required-capability calibration.get \
  --expected-room kitchen \
  --expected-room bedroom \
  --expected-room bathroom \
  --expected-room living_room \
  --output /absolute/path/to/new-audit-directory
```

可用 `--wait-seconds 300` 等待明确收尾事件，轮询周期不超过 2 秒；没有这个参数时立即采集。`--images` 才导出数据库中本任务既有 RGB/深度原始字节，默认仅校验其哈希并保存快照 JSON。脚本不重新采集传感器；本轮数据只来自已有任务。`--journal` 未提供时可以采集 API，但数据库一致性和严格验收不会判为通过。

输出内容：

- `task-before.json`、`task-after.json`：采集前后任务快照；须完全一致。
- `trace.json`：原始自然语言、修订、原子步骤、RPC、关联捕获与一致性发现。
- `observations-page-*.json`、`observations.json`：分页读取的完整捕获索引。
- `journal-task.json`、`*-snapshot.json`：一次 SQLite 只读事务中的本任务字段、事件、步骤记录、捕获元数据及快照精确字节。不会导出整库或其他任务。
- `recovery.json`、`experience.json`、`telemetry.json`、`runtime.json`、`metrics.json`：任务恢复、经验和采集时系统状态原始响应。全局指标与遥测不是本任务的独占测量。
- `summary.json`：收尾、逐步骤/RPC 状态、捕获一致性、路线和到位证据、模型实际 usage。
- `collector-used.py`：本次采集器的精确代码副本。
- `http-get-log.json`、`polls.json`、`manifest.json`：GET 时间/状态/响应摘要、等待历史、文件摘要和采集器代码 SHA-256。

`recordAuditPassed` 要求：明确的当前修订终态和后置 `LOCAL_RUN_FINISHED`、成功标记（若终态成功）、API 两次稳定、trace 事件与任务一致且完整、分页索引完整、SQLite 字段与事件一致、API 与 SQLite 捕获身份一致。

可重复指定 `--required-capability` 预注册必须执行的能力，例如 `calibration.get`；该工具必须存在于冻结计划并由当前机器人、当前修订确认完成，结果不能未知。

`acceptancePassed` 进一步要求任务成功、所要求的前置能力通过、冻结路线等于事先指定路线、每站导航和独立 `verify_arrival` 各有一次确认、命令/捕获不复用、机器人/修订/地图身份一致、捕获在自身命令下发与确认之间、快照/RGB/深度哈希有效、快照位置误差 ≤ 0.08 m 且偏航误差 ≤ 0.15 rad。命令目标和 `map_route` 目标还必须对应预执行语义地图目标。退出码 0 表示全部满足，2 表示未满足，具体原因保留在 summary。

该验收是**记录的里程计和传感器证据一致性**，不是独立真值测量，不证明无碰撞、图像语义已被正确识别或实机安全。预执行遥测应真正来自任务开始之前；脚本检查版本与身份，不能替代实验人员对采集时间来源的证明。存在恢复/重规划/重复到访时，应保留失败结果并明确修订实验判据，不应为通过验收而把历史失败抹掉。

模型计量按结构化 `agent.model-call.v1.call_id` 去重；同一 ID 内容冲突时该 ID 不计入总量。只累计供应商报告且数值有效的字段，允许合法的部分 usage。未报告的 token 字段为 `null`，按字段给出覆盖调用数和完整性；未知不是 0，`output_tokens` 是输出上限而非消耗，缓存 token 已包含在输入 token 中。这些不是账单或独立 GPU 测量。

离线回归测试：

```bash
python3 -m pytest -q docs/experiments/2026-11-04-user-simulation/tools/test_collect_task_audit.py
```

测试使用完全合成任务和本机测试 HTTP 服务，不代表 Gazebo 实验通过；覆盖四站及修订执行、缺失收尾、错误机器人/版本/地图/目标/时间/哈希、token 去重与部分 usage、只读数据库/任务隔离、路径穿越和重定向拒绝。

`observe_ros.py` 应在已运行的 ROS 2 仿真容器中执行，仅订阅 `/odom`、关节、吸盘状态、双相机点云元信息，以约 2 Hz 输出 JSONL。支持 `--duration-seconds` 和 `--stop-file`；不会发布命令。必须记录它相对于任务批准/收尾的采样覆盖，不能将晚启动或缺失数据解释为零路径。

`summarize_odom.py` 对已停止、固定的采样文件做离线分析，避免分析输入仍在追加而影响摘要复核：

```bash
python3 docs/experiments/2026-11-04-user-simulation/tools/summarize_odom.py \
  --task /absolute/path/to/audit/task-before.json \
  --samples /absolute/path/to/ros-observations-001.jsonl \
  --output /absolute/path/to/new-odometry-summary.json
```

只累计批准至明确收尾窗口内、新鲜且不同的里程计消息。报告最大采样缺口、起止缺失、时间戳单调性、采样折线距离和报告的线速度，不将 2 Hz 采样视为连续真值。`samplingCoverageComplete` 是明确的实验判据：日志首尾包围执行区间，有效消息距区间边界不超过 1 秒，最大消息缺口不超过 1 秒，传感器时间单调。它不是安全认证；部分覆盖的距离仅代表实际捕获到的区间，零有效样本时结果为 `null`。
