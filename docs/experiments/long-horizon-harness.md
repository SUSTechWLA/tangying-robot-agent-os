# 长程 Gazebo 自然语言任务验收脚本

`scripts/evaluate_long_horizon.py` 对运行中的 Gazebo Console 提交一条完整自然语言任务，保存草案，核对约束后自动批准执行，再检查终态、逐步骤观测与多 Agent 恢复证据。运行脚本就是授权本次仿真任务运动；脚本不会在终端再次询问批准。

本文是可复用操作说明。本次实际任务编号、尝试次数、失败原因和最终结果由[实测报告](2026-10-05-long-horizon-recovery.md)记录；实现与修复原因见[开发记录](../development/2026-10-05-long-horizon-recovery.md)。不要用本文的命令示例代替已执行的证据。

## 运行条件

- 主机具备 Docker、Go、Python 3.11，仓库依赖已安装。首次准备可运行 `make setup`；本地 Agent 用 `make build` 构建。`gazebo_process.py` 会检查当前源代码对应的 Gazebo 镜像，必要时构建，并准备家庭场景资源。
- 使用 `home_furnished` 场景与真实 Gazebo Runtime。场景包含家庭语义地点、可操作的 `ceramic-mug` 和 `kitchen-tray`；装饰模型不是额外可抓取物品。
- 复杂目标需要已配置的 GOAL 模型。模型设置沿用 Local Agent 的私有 `local.env` 或环境变量，详见[统一能力目标指南](../guides/unified-capability-goals.md#模型调用点)。用 `GET /v1/config/status` 核对路由，不打印或提交 API key。
- 默认运行 `task`、`ops`、`recovery` 三个 Agent；示例显式设置 `TANGYING_AGENTS=task,ops,recovery`。`TANGYING_AGENTS=task` 会关闭所需监督链路，不能通过严格恢复验收。Ops 分类与 Recovery 的目录内只读调查可以是确定性的，多 Agent 参与不表示三个独立 LLM 调用。
- 下文的巡检重建图流程使用同一场景已有的有效标定、已保存地图、栅格与语义地点，并以 `localization` 模式启动。当前地图和标定必须与场景、机器人及地图版本一致。任务中的 `mapping.build` 会采集、保存并启用新地图；不能用重命名旧地图代替此步骤。
- 固定家庭 `survey` 的起点是客厅。若上一个任务停在别的房间，先经正式任务 API 返回客厅并确认抵达，再执行本文原句。该测绘路线不会自动选择任意房间的入口；从卫生间直接向首个走廊测绘点行驶曾被碰撞保护拒绝，失败记录见实测报告。不要靠复位世界或关闭碰撞检查补齐这个条件。

**没有前置地图时，先完成独立的标定/建图验收。** 按[统一能力目标指南](../guides/unified-capability-goals.md)建立地图，确认 `/v1/navigation/map` 就绪、遥测 `active_map` 与 `semantic_navigation` 的地图/标定版本一致，且所需房间目标存在，再运行本文的长程巡检与抓放任务。本文不把无地图冷启动与复用测绘成果的实验视为同一条件。

## 部署链路与目录

```text
evaluate_long_horizon.py
  → Console HTTP 127.0.0.1:8897
  → Local Agent（task / ops / recovery）
  → 只读故障代理 gRPC 127.0.0.1:50162
  → Gazebo Runtime gRPC 127.0.0.1:50161
  → ROS 2 / Nav2 / RGB-D / Gazebo 物理世界
```

如果这条链路已经运行并承载任务，先查看 Console 及原任务状态，直接使用既有服务。不要重复执行启动段，不要按端口杀进程，不要删除数据库、地图、journal 或未知任务来获得“干净通过”。重新启动 Gazebo 会创建新的物理场景 episode；必须单独记录，不能声称跨重启连续执行。

下面用新目录 `long-horizon-manual-001` 示范独立实验，端口与实际验收链路一致。端口被既有服务占用时应复用或明确选择另一组端口。各终端均从仓库根目录运行，并设置同样的变量；每次新实验使用不同的目录名。

```bash
TASK_PROJECT_ROOT="$(pwd)"
GAZEBO_ROOT="$TASK_PROJECT_ROOT/artifacts/sim-stack/long-horizon-manual-001/gazebo"
EVIDENCE_ROOT="$TASK_PROJECT_ROOT/artifacts/acceptance/long-horizon-manual-001"
mkdir -p "$EVIDENCE_ROOT"
```

需要为新部署复用地图时，只复制已经停止写入的、同一场景的完整地图目录。不要对仍运行的 SQLite/地图目录做普通文件复制。以下来源是已有部署的路径示例，应换成已经核对版本的地图归档；`copytree` 会拒绝覆盖目标目录。

```bash
SOURCE_SCENE_MAPS="$TASK_PROJECT_ROOT/artifacts/sim-stack/gazebo-xlerobot-home/gazebo/maps/home_furnished"
.venv/bin/python -c 'import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2])' \
  "$SOURCE_SCENE_MAPS" "$GAZEBO_ROOT/maps/home_furnished"
```

保留完整 `home_furnished` 目录中的地图数据库、workflow、标定绑定及相关元数据。实验记录应写明来源路径、复制时间、原世界版本及是否新建 episode。只复制 `active-map.json` 不足以恢复所引用的地图。

## 启动 Gazebo、故障代理和 Agent

终端 A 启动 Gazebo。`--seed` 在此处只标记 episode，不是物理引擎确定性随机种子的保证。程序前台拥有其 Compose 进程；不要在验收途中关闭此终端。

```bash
TANGYING_NAVIGATION_MODE=localization \
  .venv/bin/python scripts/gazebo_process.py \
  --listen 127.0.0.1:50161 \
  --scene home_furnished \
  --seed 8 \
  --artifacts-dir "$GAZEBO_ROOT" \
  2>&1 | tee "$EVIDENCE_ROOT/gazebo.log"
```

Runtime 可访问后，在终端 B 启动故障代理。代理在监听前调用 `GetRuntimeInfo` 和 `ListServices`，要求 `adapter=gazebo`、目标服务唯一且不改变世界；连接真实 Runtime 失败时退出，不会启动替代 Runtime。

```bash
.venv/bin/python scripts/gazebo_read_fault_proxy.py \
  --runtime 127.0.0.1:50161 \
  --listen 127.0.0.1:50162 \
  --service navigation.status \
  --fail-count 1 \
  --arm-file "$EVIDENCE_ROOT/arm-read-fault" \
  --output "$EVIDENCE_ROOT/faults.jsonl" \
  2>&1 | tee "$EVIDENCE_ROOT/proxy.log"
```

看到 `proxy_ready` 后，在终端 C 启动 Agent，并使 `--robot` 指向代理端口。Agent 使用同一场景自己的持久数据库，绝对 `--data-dir` 便于回溯实际数据位置。不要并行启动另一个 Agent 操作同一 Runtime。

```bash
TANGYING_AGENTS=task,ops,recovery \
TANGYING_MAP_ROOT="$GAZEBO_ROOT/maps/home_furnished/workflow" \
  bin/local-agent \
  --dev-insecure \
  --robot-safety-profile desktop_standard \
  --listen 127.0.0.1:8897 \
  --robot 127.0.0.1:50162 \
  --data-dir "$GAZEBO_ROOT/agents/home_furnished" \
  2>&1 | tee "$EVIDENCE_ROOT/agent.log"
```

`--dev-insecure` 用于这组 loopback 仿真 gRPC 连接。它不取消 Console 会话要求，也不把该部署说明扩展为真机部署流程。私有模型配置可用 Agent 的 `--config /absolute/path/local.env` 指定；默认 macOS 路径是 `~/Library/Application Support/TangyingRobotAgent/local.env`，也可通过 `ROBOT_AGENT_CONFIG_DIR` 设置配置目录。不要把密钥放进命令行或运行日志。

手动启动时必须将 Agent 的 `TANGYING_MAP_ROOT` 指向同一 Gazebo workflow 地图目录。否则机器人服务仍能使用正确地图执行任务，但网页的本地“已保存地图”列表会读取默认 `artifacts/maps`，出现其他场景的地图；这两个入口并不共享进程环境变量。标准 `sim-stack.sh` 启动器已负责传递该配置。

在终端 D 保留只读就绪记录，并打开 `http://127.0.0.1:8897/` 查看任务列表、相机和地图。HTTP 健康响应只说明 Console 可达；还需检查 Runtime、定位与地图证据。

```bash
curl --noproxy 127.0.0.1 --fail --silent --show-error \
  http://127.0.0.1:8897/v1/runtime > "$EVIDENCE_ROOT/runtime-before.json"
curl --noproxy 127.0.0.1 --fail --silent --show-error \
  http://127.0.0.1:8897/v1/config/status > "$EVIDENCE_ROOT/model-routes-before.json"
curl --noproxy 127.0.0.1 --fail --silent --show-error \
  http://127.0.0.1:8897/v1/navigation/map > "$EVIDENCE_ROOT/navigation-before.json"
curl --noproxy 127.0.0.1 --fail --silent --show-error \
  'http://127.0.0.1:8897/v1/telemetry?adapter=gazebo&limit=1' > "$EVIDENCE_ROOT/telemetry-before.json"
```

## 会话与一次性故障

验收脚本使用 `scripts/console_session.py` 获取 Console 会话：显式环境配置优先，否则向对应 loopback Console 的 `/healthz` 读取实时会话 cookie，失败时按已记录地址/文件回退。写请求使用 `X-Tangying-Session` 和本地 `Origin`。脚本不打印会话值，不需要从浏览器复制 cookie；不要把 `console-session` 文件放入报告。

脚本会安装不使用系统 HTTP 代理的 opener；手动 `curl` 使用上面的 `--noproxy 127.0.0.1`。若显式设置过期的 `TANGYING_CONSOLE_SESSION`，它会优先于实时获取，必须核对该设置是否属于当前 Console。

确认没有其他任务会消费故障后，在终端 D 创建本轮专用 arm 文件：

```bash
.venv/bin/python -c 'from pathlib import Path; import sys; Path(sys.argv[1]).touch(exist_ok=False)' \
  "$EVIDENCE_ROOT/arm-read-fault"
```

代理只拦截所选服务中 `request_id` 以 `task-` 开头的执行期请求，按进程内计数最多注入一次 `UNAVAILABLE`，且在转发前返回失败。无任务身份的规划查询、技能执行和其他服务照常转发。故障日志应出现 `fault_injected`、具体 request ID、`forwarded=false` 和计数；它证明注入位置，任务账本证明之后的诊断和恢复。

arm 文件存在本身不证明发生故障。代理也不绑定某个待创建 task ID，因此整个实验必须保证同一机器人没有其他并发任务。代理重启会重置注入计数；不能在同一任务途中重启它并仍宣称只注入一次。

## 执行完整自然语言验收

以下原句覆盖读取标定、巡检重建图、状态读取、多房间巡检、厨房抓放、返回与最终状态确认。输出子目录 `run-001` 必须不存在；脚本以排他创建保留历史结果。

```bash
.venv/bin/python scripts/evaluate_long_horizon.py \
  --base-url http://127.0.0.1:8897 \
  --request '依次执行以下完整家庭任务：读取整机标定状态；重新按家庭巡检路线完成SLAM建图并启用新地图；读取导航定位状态；从客厅出发，巡检卧室和卫生间，最后回到客厅；再从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅；最后读取并报告当前地图和导航定位状态。每个地点都确认抵达，抓取和放置分别验证。' \
  --output "$EVIDENCE_ROOT/run-001" \
  --timeout 2400 \
  --required-source llm \
  --expected-arrivals 5 \
  --require-manipulation \
  --pause-after-tool mapping.build \
  --pause-seconds 65 \
  --require-collaboration \
  --require-auto-investigation \
  --require-auto-recovery
```

脚本只使用生产任务 API 创建、批准、暂停、继续、读取与必要的取消；观测和服务目录也从生产只读 API 获取。它不会直接调用建图 RPC 推进过程，不移动或复位物体，不重置场景，也不替换失败任务。

`--pause-after-tool mapping.build` 在看到该 capability 的 `CAPABILITY_VERIFIED` 后请求暂停。下一项工具可能已启动，暂停会等当前工具完成并保存到安全边界；这不是中途急停。脚本看到任务实际 `PAUSED` 后开始计时，至少等待 65 秒，再对同一 task ID 请求继续。完成记录与原冻结计划必须保留。报告将它归为 `acceptance_operator`，不会把测试者主动继续误报为自动故障恢复。

`--timeout` 从批准后的轮询开始计算，包含暂停时间。创建和批准 HTTP 调用各自最多等待 120 秒，普通调用最多 45 秒；HTTP 超时不保证服务端操作未发生。任务终态后，默认再用 `--settle-seconds 10` 收集异步调查事件；必要时可增大这个有界窗口。`--poll-interval` 默认 1 秒。

## 验收判据

| 要求 | 检查的实际证据 |
| --- | --- |
| 模型计划 | 草案 `plan.source=llm`；草案未批准，包含 capability calls；请求暂停的工具确实存在 |
| 冻结审批 | 批准与轮询、最终任务中的 plan 均等于原草案，存在 `TASK_APPROVED` |
| 全部能力完成 | 按冻结工具顺序、唯一 step ID 核对 `CAPABILITY_VERIFIED`；provider 回执对应相同步骤；契约 required/match 及长操作终态成立 |
| 子步骤观测 | `command_observation`，capture ID 被事件引用，历史观测绑定同一任务/步骤与 Gazebo；RGB/深度文件 SHA-256 与索引及详情一致 |
| 到达 | 至少 5 个不同到达步骤，用保存的观测位姿相对该步骤 `goalPose` 重新计算平面误差 ≤ 0.08 m、航向误差 ≤ 0.15 rad |
| 导航 | 地图回执绑定原 command ID，具备 mapId/mapRevision/calibrationRevision，观测位姿落在目标容差内 |
| 抓放 | 实际 pick/place 完成，独立 Gazebo physics 的 grasp/placement 检查通过，对象/目的地匹配，至少 3 个样本、稳定时间 ≥ 0.1 s，放置关系正确 |
| 防止物理重放 | 同一物理步骤不能出现重复的完成确认；已开始的子步骤必须有完成证据 |
| 多 Agent | 任务账本实际含 `ops` 与 `recovery` 事件及恢复计划；配置列表不能替代参与证据 |
| 自动调查 | 与 plan ID 关联的 `ops.recovery_executed` 必须 executed/verified，且不是操作者批准的动作 |
| 自动任务恢复 | 下列完整有序链成立，且原任务最终 `SUCCEEDED` |

`--expected-arrivals` 是独立到达次数的下限。对冻结的 `home_route` / `home_manipulation`，验收还按各能力的 `legacyIntent.routeRooms` 顺序逐段绑定导航与到达核验，用当前地图版本的 `semantic_navigation` 目标和别名核对地点，并在 `verifiedCompositeOutcomes` 输出房间序列。重复到达同一点不能替代计划中的不同地点。抓放核验必须贯穿同一物体和同一目的地，并符合冻结的语义类别及属性。

子步骤必须归属当前任务、修订号和能力，并具有对应 command ID 的派发及完成记录；后续失败或未完成调用不能沿用较早的成功。已完成步骤在暂停后仍是有效历史证据，新派发的物理动作和验证步骤则必须使用派发后的观测。普通场景读取可以使用绑定传感器声明的有效期内、最多早于派发 1 秒的帧，不可借用暂停前的旧帧。额外上下文快照可以没有图像，实际子步骤观测仍必须有 RGB-D。

严格恢复链包括：

```text
原 CAPABILITY_CALL（唯一 step / command ID）
→ CAPABILITY_READ_RECOVERY_REQUESTED（maxRetries=1）
→ CAPABILITY_FAILED（dispatch，只读，结果非未知，允许的通信错误）
→ ops.anomaly_detected（ops；同 tool 与 step）
→ ops.recovery_plan（recovery；新 planId；recovery.start 轨迹绑定原 step）
→ ops.recovery_executed（execution.read-history；自动；executed=true；verified=true）
→ CAPABILITY_READ_RETRY（原 command ID；attempt=2；只有一次）
→ CAPABILITY_VERIFIED（原 step 与 tool）
→ 原任务 SUCCEEDED
```

目录必须声明该能力 `mutatesWorld=false` 且 `effects=[READ]`。仅调查读取成功、计划建议、Agent 已注册、人工 `/resume`，都不能满足 `--require-auto-recovery`。持续通信失败、物理动作结果未知、急停、物理复验失败及需要新运动的修复不在这一有界自动重试范围内。

## 制品、失败与回溯

每次 API 响应以独立 `response-NNNNN.json/bin` 保存原始字节。`manifest.json` 将请求路径/方法映射到响应文件，并为已保存文件记录 SHA-256；不保存认证请求头。关键文件为：

| 文件 | 含义 |
| --- | --- |
| `draft-plan.json` / `approved-plan.json` | 原始草案与批准后的任务响应 |
| `paused-task.json` / `pause-response.json` / `resume-response.json` | 受控暂停、恢复证据；只有实际走到该阶段才存在 |
| `terminal-task.json` | 首次看到任务终态时的原始响应 |
| `final-task.json` | 异步调查收集或错误清理后的最终已知任务响应 |
| `agent-events.json` | 诊断、计划、恢复、失败与重试事件及参与判定 |
| `model-routes.json` | Console 脱敏模型路由状态 |
| `report.json` | passed、taskId、状态、逐步核验、暂停及恢复结果、错误与清理结果 |
| `manifest.json` | 逐文件摘要和 API 响应索引；manifest 本身不包含自己的哈希 |

文件采用排他写入，重复运行不能覆盖原结果。这里的“不可覆盖原证据”是脚本行为与摘要校验约定，不是不可篡改存储认证。`artifacts/acceptance/` 默认被 Git 忽略；推送代码或文档不等于已把大型原始 RGB-D 制品上传。报告应保留本地路径、task ID、摘要与制品保管位置。

任何验收不满足都保留失败并退出非零。超时或执行期间发生错误时，只查询并尝试取消脚本自己创建的已知任务；不重试创建/批准/恢复写请求，不自动接管其他任务。已处于失败、待用户处理或安全停止的任务保持相应状态。取消响应也不证明未知物理动作已经停止，`cleanupError` 和任务恢复视图必须一并审阅。

模型创建请求若在服务端生成任务后丢失响应，脚本可能没有 task ID；先在 Console/任务列表中核对原句和时间，不能直接重跑命令制造第二个任务。若验收器自身有缺陷，保留原 `report.json`，用修复后的只读校验对同 task ID 的历史观测另存复验结果；不要修改原失败报告或再次执行运动来掩盖验收器问题。

不要在验收进行中停止服务。需要结束已确认无执行任务的本轮部署时，在其所属终端对本轮进程正常发送中断；Gazebo owner 会关闭自己的 Compose 项目。关闭仿真会结束本 episode，但应保留地图、数据库、日志与证据目录。

## 离线回归与证据边界

```bash
.venv/bin/python -m pytest -q \
  tests/install/test_long_horizon_eval.py \
  tests/install/test_gazebo_read_fault_proxy.py
.venv/bin/ruff check \
  scripts/evaluate_long_horizon.py \
  scripts/gazebo_read_fault_proxy.py \
  tests/install/test_long_horizon_eval.py \
  tests/install/test_gazebo_read_fault_proxy.py
```

这些测试使用受控假 API、假时钟或本地 gRPC 假 Runtime，验证缺失证据、错误绑定、重试顺序、超时处理和代理行为；它们没有运行 Gazebo，也不能证明机器人到达或抓放成功。真实仿真验收必须另有 Gazebo 进程、版本/场景记录、任务原始事件和传感器/物理证据。

Gazebo 的抓放证据来自此场景的物理及 `sim_suction` 机制，不能推广成真实夹爪性能认证。单条长程任务与一次只读瞬时故障通过，也不能推广成任意自然语言任务、任意故障或跨主机自动修复的保证。
