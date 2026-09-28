# Gazebo 自然语言能力目标矩阵与故障回溯（2026-09-28）

本报告检验：同一个 Agent 是否能从不同自然语言目标创建**未批准草案**，按注册能力契约执行，并在真实 Gazebo XLeRobot 家庭场景中留下可核对的步骤、地图和导航证据。前 17 例是首轮矩阵；case18 在首轮代码合入后复测 case10 的原句。它承接 [统一能力目标规格](../development/2026-09-27-capability-goal-implementation-spec.md) 和 [2026-09-27 首轮闭环](2026-09-27-capability-goal-closure.md)；历史记录均保留。

## 方法与环境

- macOS 开发主机；Docker Gazebo / ROS 2、`home_furnished` 多房间场景、同源 XLeRobot；Local Agent `127.0.0.1:8897`，Runtime `127.0.0.1:50161`，机器人 `gazebo-home_furnished`。
- 只经 `POST /v1/tasks` 下发自然语言。先审查冻结计划、工具顺序和关键参数，再 `approve`。通过统一任务事件、Provider 回执、独立读回与地图制品判断；没有直接调用建图或导航 RPC 推进目标。
- GOAL / INTENT 路由为当时已配置的 `https://api.deepseek.com` / `deepseek-flash`；标记 `LLM` 的样本发生真实模型调用。精确完整句式走离线语法。凭据不写入报告。
- 原始草案、审批响应、最终 Task、模型路由、逐例报告存于本机 `artifacts/acceptance/nl-matrix-20260928/case*`；地图存于 `artifacts/sim-stack/gazebo-xlerobot-home/gazebo/maps/home_furnished/workflow/`。机器可读摘要见 [验收清单](2026-09-28-natural-language-goal-matrix.json)。这些路径的原始仿真数据为本机忽略制品，不等于已推送到 Git。

## 样本与结果

每一行是一次实际请求；修复前的失败样本没有被改写成成功。`422` 表示未创建任务，`取消` 表示审批前发现错误计划后未执行物理动作。`CAPABILITY_VERIFIED` 仅说明该任务内对应步骤通过其契约核验，不能外推为任意任务能力。

| 案例 | 自然语言目标（摘要） | 计划来源与最终结果 | 关键检查 |
| --- | --- | --- | --- |
| case01 | 检查标定、地图、语义地点 | 离线，`SUCCEEDED` | 3 个只读能力核验；原有地图 `scan-e23523f77362` |
| case02 | 检查标定和地图并列地点；不要移动或重建图 | LLM，`SUCCEEDED` | 4 个只读能力；无运动、建图调用 |
| case03 | 重新标定、巡检 SLAM、卧室到位 | LLM，审批前取消 | 模型给巡检模式附加探索预算；旧目录未拦住，未批准 |
| case04 | 同 case03 | LLM，`SUCCEEDED` | 5 个能力核验，巡检建图后卧室 `verify_arrival=CONFIRMED` |
| case05 | 未登记阳台开窗、浇水 | LLM，`422` | 没有相应硬件工具/地点，不创建任务 |
| case06 | 随便选房间且不要告知 | `422` | 否定输出要求触发保守拒绝；不能把此例算作通用歧义理解能力 |
| case07 | 去那个房间 | LLM，`422` | 指代与成功条件不明确，不创建任务 |
| case08 | 先走廊、再客厅、分别确认 | LLM，审批前取消 | 第二导航子句指向客厅，冻结 Intent 只含走廊；未运动 |
| case09 | 巡检建图最多 12 米 / 1 轮 | 离线，`422` | 巡检模式与探索预算矛盾，创建前拒绝 |
| case10 | 同 case08，修复后重试 | `422` | 模型子请求含不受旧 Intent 支持的条件/否定语法；仍未创建任务 |
| case11 | 依次到走廊和客厅并各自核验 | LLM，`RECOVERABLE_FAILURE` | Nav2 重启后尚未定位；`NAVIGATION_NOT_READY`，物理结果未知；旧取消行为误写 `CANCELLED`，原始快照保留 |
| case12 | 同 case11，修复后重试 | LLM，`SUCCEEDED` | 6 个能力核验；走廊和客厅各有独立 `verify_arrival=CONFIRMED` |
| case13 | 探索建图最多 8 米 / 2 轮 | 离线，`RECOVERABLE_FAILURE` | 77 帧后捕获到旧 RGB-D；部分地图标 `partial=true` 与 `STALE_CAPTURE`，不计成功 |
| case14 | 同 case13，修复旧帧等待后重试 | 离线，Task `SUCCEEDED`，验收失败 | 112 帧、102 配准，地图启用；实测 8.330 米超过请求的 8 米，原脚本报告与事后预算审计同时留存 |
| case15 | 同 case13，修复预算后重试 | 离线，`SUCCEEDED`，验收通过 | 123 帧、114 配准、2 回环，实测 7.150 米，地图 ID/版本读回一致；连续深度不足而提前结束 |
| case16 | 切回保存的 `scan-f7a5c354f3b7` 并检查地图、地点 | LLM，`SUCCEEDED` | `mapping.activate → mapping.status → semantic.locations`，3 步核验，未重新建图或运动 |
| case17 | 核查导航地图、定位状态、可导航地点 | LLM，`SUCCEEDED` | `navigation.map → semantic.locations`，Nav2 `localized`，与 activeMap 版本一致 |
| case18 | case10 原句：先走廊、再客厅、分别确认抵达 | 确定性整句路线，`SUCCEEDED` | 单个 `robot.task` 保留两地点；两个独立 `verify_arrival=CONFIRMED`，历史 case10 拒绝记录未改写 |

### 成功的运动与地图证据

case04 的任务 `task-a9e079913fe8defc58281f35`，计划是 `calibration.get → calibration.run → mapping.build(survey) → semantic.resolve(卧室) → robot.task`。冻结后的机器人身份是 `gazebo-home_furnished`。地图 `scan-f7a5c354f3b7`，revision `79baa5abd1d188928efc4e464aa564e8084f4718174924ee22e455de4a243770`；巡检实测 23.462 米、230 帧、219 次配准、2 次回环、71,885 点。`mapping.status` 独立读回 activeMap 与结果同一 ID/版本；卧室经注册工作区语义解析、测量栅格可通行检查及动作后到位核验。

case12 的任务 `task-b25232d7e9434fed6222b56a`，两段计划依次是 `semantic.resolve(走廊) → robot.task → navigation.map` 和 `semantic.resolve(客厅) → robot.task → navigation.map`。两次 `navigation.map` 读回同一 `scan-f7a5c354f3b7` 版本，定位状态 `localized`；两段各有独立到位确认。这是多地点顺序执行的单个任务样本，不是成功率估计。

case15 的任务 `task-3787f0497218bb660fcffcc9`，`mapping.build(mode=explore,maxTravelM=8,maxLegs=2)` 获批后实际只运行一段；操作以 `stopReason=depth_starved` 提前结束，123 帧、114 配准、2 回环、50,271 点、7.150459761 米。地图 `scan-1ffd140ec2c9`，revision `a31e9e560863f2ceb0d6ccc54fa6c6e80054262251f6385cd97c0c541f878df2`；完成读回与 activeMap 同一版本。地图矩形范围未知比例 0.386661，不是全屋覆盖率；`slam-session.partial=false` 只表明本次没有故障中断，**不表示所有未知区域已覆盖**。本次运行镜像比最终源码早一个仅影响多段报告字段/最终保存提示的增量；后续重新部署验证单独记录。

最终源码镜像重启后，case16 `task-844b4ca67e86e63b317229c0` 用真实 GOAL 计划恢复已验证的 `scan-f7a5c354f3b7`，`mapping.status` 和 `semantic.locations` 分别独立读回 revision `79baa5abd1d188928efc4e464aa564e8084f4718174924ee22e455de4a243770`。case17 `task-f58ead6a836d7a5db96a921d` 再经独立只读任务读到 Nav2 地图同一 ID/版本、`localizationState=localized`，五个房间与厨房工作区均 `navigationReady=true`。这确认本轮结束时不是把限距探索地图误留作完整巡检地图。

case18 在首轮合入后的 `fd565bc4036c6d2097b9b016d7ddd2cbf82858a0` 上追加代码并重启同一 Gazebo 家庭栈，直接下发 case10 原句。`task-5ff22902ef98b99b7bca72a9` 的冻结计划是单个 `robot.task`，路线按原句为 `home_corridor → living_room`；两个不同步骤 `rev-1-cap-01-verify_arrival_00` 和 `rev-1-cap-01-verify_arrival_01` 均有 `CONFIRMED` 事件，验收 `verifiedArrivals=2`、`passed=true`。原始文件及 SHA-256 追加在机器清单，旧 case10 拒绝仍可回溯。这是同一语句修复后的独立运行，不能算作两次独立成功率样本。

### 失败如何改变代码

1. `mapping.build` 的 Provider 目录改为互斥 `oneOf`：`survey` 不接收 `maxTravelM/maxLegs`，`explore` 可接收合法预算；Go/Python 契约检查一致。模型提交目录非法计划时最多再规划一次；仍不合法返回 `422`。case03 与 case09 在审批前/创建前拦截。
2. 在当前家庭导航词表内，Agent 比对自然语言子句的明确移动目标和审批前冻结的 `routeRooms`，防止 case08 那种“文本去客厅，实际只去走廊”。其他异构机器人仍以其注册目录和执行后核验为准。
3. `NAVIGATION_NOT_READY` 只在 Nav2 明确表示**未接纳**目标时以同一命令 ID 有界等待，最长 180 秒且不超过命令截止时间；取消、繁忙和传输不明不进入此重试。case11 的旧任务保持失败记录，case12 是后续独立新任务。
4. 有未对账物理步骤的可恢复失败不能靠取消请求改写为 `CANCELLED`；写 `LOCAL_CANCEL_BLOCKED`，留待物理核查和步骤对账。case11 的旧取消快照仅展示发现此 bug，不能用新代码追改其历史事件。
5. case13 的旧帧绝不交给 SLAM；网关在 8 秒上限内等新捕获，持续过期仍以 `STALE_CAPTURE` 失败。部分地图可保存并启用，但任务与制品均标记失败/部分，不能称“建图完成”。
6. case14 发现“距离上限”只在完整移动后检查，可超出最后一步。探索策略现在把剩余里程传给驱动，最后一步留控制余量；如果实测仍越界，Provider 明确失败并把地图记为部分。验收脚本独立核对 `travelledM <= maxTravelM`，不再因任务自报 `SUCCEEDED` 就给通过。
7. 回归还发现可选 RoboCasa 依赖探测把本地同名命名空间当作已安装包；现检查真正需要的 `robocasa.models.scenes`，安装完整依赖才运行对应集成测试。
8. case10 的原句在旧版由模型拆成带条件的子请求，虽未误执行，却产生了无必要的 `422`。精确家庭路线现在先通过完整的保守语法，并将整句交给一个 `robot.task`；“分别确认抵达”只在整条路线末尾且没有其他动作时接受，路线执行器为每个地点生成独立导航与到位核验。验收脚本按冻结路线地点数核对不同的 `verify_arrival=CONFIRMED` 步骤。

## 复现与门禁

先按 [Gazebo 家庭场景操作](../guides/gazebo-house-operations.md) 启动本机栈并配置 GOAL/INTENT 模型。每次使用**新**输出目录；脚本会读取草案并核对所需工具和参数，随后批准，机器人可能运动：

~~~bash
.venv/bin/python scripts/evaluate_capability_goal.py \
  --request '探索建图最多行驶8米最多2轮' \
  --output artifacts/acceptance/my-bounded-exploration --timeout 1800 \
  --required-tools mapping.build --forbidden-tools mapping.finish \
  --required-source deterministic \
  --required-arguments-json '{"mapping.build":{"mode":"explore","maxTravelM":8,"maxLegs":2}}'
~~~

脚本检查计划在审批前后完全相同、每个调用对应核验事件、写操作 Provider 回执和非空证据、导航后的到位事件。`robot.task` 的子步骤由既有执行器记录，不伪造一个 Provider 回执。运行期失败保留任务终态与事件，不自动重新下发可能已产生副作用的动作。

## 结论与边界

只读查询、完整巡检 SLAM 与卧室到位、顺序多地点导航、限距探索及地图恢复在这台 Gazebo XLeRobot 家庭场景中实际通过；不可用工具和不明确请求被阻断。首轮 case10 的原句曾被拒绝，case18 用保守整句解析和每地点独立到位核验跑通；模型对其他复杂措辞仍可能给出无效计划，本矩阵没有证明“任意自然语言都能执行”。部分地图、失败任务、仿真到位也不构成实机认证。真实 Orin/GPU、实体安全停稳、异构机器人与大规模机群仍需各自实测。
