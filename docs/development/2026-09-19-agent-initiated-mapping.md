# 让 Agent 真正能"探索环境"：一个工具，而不是一串服务调用

**日期**：2026-09-19
**问题**：用户说"请探索环境，构建全局地图"，系统应该自己判断——没有地图就开始自动探索，已有地图就提示/复用——而不是要用户背出一串网关调用。

---

## 一、先取证：Agent 当时**根本看不到建图能力**

导出给模型的工具面是 29 个工具（改前 28 个）。改前那 28 个里**没有任何建图/探索工具**：

```
capture_image, check_collision, detect_object, emergency_stop, explore_for, fetch_object,
get_arm_state, get_current_pose, get_gripper_state, get_object_pose, get_robot_status,
grasp, home_arm, move_arm_relative, move_arm_to_pose, navigate_to, navigate_to_work_area,
pick_object, place_object, plan_work_area, recall_object, release, reset_safety_stop,
resolve_location, scan_environment, set_gripper_width, set_speed_limit, stop_navigation
```

最接近的 `plan_work_area` 是**工作区可达位姿规划**（桌上作业用的），`explore_for` 是**在有界时间内找物体**——两者都不是建图。

而网关**早就有**建图能力：`mapping.start`（`mode` 支持 `manual`/`survey`/**`explore`**，`baseMapId` 支持续建）、`mapping.status`、`mapping.finish`、`mapping.activate`。它们只出现在控制台按钮和故障恢复目录里，**没有一条通往模型**。

所以这不是"模型没选对工具"，而是**模型没有工具可选**。用户那句话在当时不可能被满足：底层能力齐全，接口层缺了一个入口。

---

## 二、修法：把判断放在有证据的一侧

### 2.1 为什么不加四个工具、让模型自己编排

一条看似直接的路线是暴露 `create_map` / `load_map` / `start_survey` / `survey_status`，让模型自己排序。这条路被否掉，理由是**它会稳定地做错**：

- 模型必须先知道"这台机器上有没有这个环境的地图"，而它拿不到地图目录；让它先 `list` 再判断，就是把一个**可验证的事实**变成一次**推理**；
- 判断错了的代价不对称：把"已有地图"误判成"没有"，就是一次二十多分钟的自动巡检；把"没有"误判成"有"，就是在地图缺失的情况下继续下发导航任务；
- 多步编排的每一步都是一次模型决策，每一步都可以在压力下退化。

**判断被放进网关**，模型只表达意图。这与仓库既有原则一致：*新增能力走既有机制，不新建平行的权威*。

### 2.2 决策规则：`map_selection.select_map()`

一个**纯函数**，输入是已核验的地图清单 + 在用地图 + 调用者的话，输出 `reuse` / `explore` / `ambiguous`：

| 顺序 | 条件 | 结果 | 为什么是这条规则 |
| --- | --- | --- | --- |
| 1 | 明确给了 `mapId` 且可用 | **reuse** | 调用者指名了那一个包，用启发式覆盖它等于忽略指令 |
| 1' | 明确给了 `mapId` 但不在清单里 | **explore** + 说明 | "你要的地图没了"和"这里没有地图"是两个答案，只有一个是真的 |
| 2 | 说了地点，**恰好一张**匹配 | **reuse** | 用户说"探索客厅"，包名叫"客厅地图"——按相等匹配会去扫一个已经有图的房间 |
| 2' | 说了地点，**多张**匹配 | **ambiguous** | 选错地图比问一句更糟：后面所有规划都建在错房间的图上 |
| 2'' | 说了地点，**没有**匹配 | **explore** | 关键区分：只有厨房图时问车库，是**车库没图**，不是"有图可复用" |
| 3 | 没说地点，有可用地图 | **reuse**（在用优先，其次最新） | 并用消息说明用了哪一张 |
| 4 | 没说地点，没有地图 | **explore** | 唯一诚实的答案 |

第 2'' 条是写测试时才发现的**真实逻辑缺口**：最初的实现让"点名了但没匹配"落进第 3 条，于是"探索车库"会去复用厨房的地图——回答了一个没人问的问题。现在它是一个显式分支。

### 2.3 "可用"的定义比"在磁盘上"严格得多

`available_maps()` 只列出 `MapCatalog.open()` **对当前机器人、当前标定修订校验通过**的包。理由写在代码里：用另一套标定建的地图描述的是另一个世界，把它列出来就是 Agent 被说服去启用它的路径。

一个损坏的包**跳过而不抛错**：一个坏目录是关于那个目录的事实，因为它拒绝列出任何东西，会在地图最需要被看到的时候把它们藏起来。

### 2.4 接口

| 层 | 新增 | 说明 |
| --- | --- | --- |
| 网关服务 | `mapping.ensure` | 意图入口。`{environment?, mapId?, name?, maxTravelM?, maxLegs?}` → `{decision, message, mapId, availableMaps, matches, started, session}` |
| 网关服务 | `mapping.inventory` | 只读：当前可用地图与在用地图。**便宜到可以在每次建图请求前调用**——这正是重点：需要先启动一次巡检才知道要不要巡检，就已经浪费了那次巡检 |
| 模型工具 | `build_map` | `{environment?, mapId?, max_travel_m?, max_legs?}`，`SafetyLevel.NORMAL_MOTION`，`mutates_world=True` |
| 决策规则 | `map_selection.py` | 纯函数，可单测 |

`decision` 是**返回**而不是**抛出**：调用者是个要开口说话的 Agent，"我复用了这张图"和"我开始建图了"是两句不同的话，不是两个不同的异常。

---

## 三、行为对照

| 用户说 | 本机状态 | 结果 |
| --- | --- | --- |
| 请探索环境，构建全局地图 | 无地图 | `decision=explore`，`mapping.start {mode: explore}`，附会话状态 |
| 请探索环境，构建全局地图 | 已有家庭地图 | `decision=reuse`，启用并返回该 `mapId`，消息说明用了哪张 |
| 建一张客厅的地图 | 有"客厅地图" | `decision=reuse`，复用客厅那张 |
| 建一张客厅的地图 | 有"客厅-东"和"客厅-西" | `decision=ambiguous`，返回两张候选，**先问用户** |
| 建一张客厅的地图 | 只有"厨房地图" | `decision=explore`，去建客厅的图（不是复用厨房） |
| 用 scan-abc 那张图 | scan-abc 可用 | `decision=reuse` |
| 用 scan-abc 那张图 | scan-abc 不在清单 | `decision=explore` + 说明该包不可用 |

---

## 四、验证

- `robot/gateway/tests/test_map_selection.py`（新增 19 项）：决策规则七条分支 + 真实地图包上的清单过滤 + `ensure_map` 三条路径 + 工具层四条（含"不假装可重试的失败"）。测试用的是**真的建出来的地图包**，不是桩：决策读的是 manifest 和 session 文档。
- 工具面钉住测试更新：`test_tool_selection.py` 新增三条自然语言指令（"请探索环境，构建全局地图" / "扫描一下这里" / "建一张客厅的地图"）都必须能路由到 `build_map`；`test_tools.py` 的标准工具面集合加入 `build_map`。
- `tools.json` 重新生成（29 个工具）。
- 全量：`robot/gateway/tests` + `tests/tool_layer` **929 passed / 4 skipped**。

复现：

```bash
.venv/bin/python -m pytest robot/gateway/tests/test_map_selection.py -q     # 19 passed
.venv/bin/python -m pytest robot/gateway/tests tests/tool_layer -q          # 929 passed
.venv/bin/python -m tangying_robot_gateway.llm_tools --check                # 工具面与定义一致
```

---

## 五、没做的部分（明确记录）

1. **`environment` 与地图身份的对应没有真正的索引。** 现在靠 `slam-session.json` 里的 `name` 做**子串匹配**。中文地名共享字符，"车库"是"厨房地图"的子串，所以它会过度匹配。这是**已知限制**而不是正确性：假匹配最多把答案收窄成一次复用，而回复里会点名用了哪张图，操作员能看出错；假不匹配则会启动一次二十分钟的巡检。真正的消歧需要地点索引，那是比这条规则大得多的改动。
2. **`ensure_map` 是同步启动、异步执行。** 返回 `started=true` 只表示会话已开，建图还在跑；`decision` 与 `session` 都是"已开始"的证据，不是"已建完"。Agent 必须读 `mapping.status`（工具描述里写明了）。**没有**为建图完成加回调或事件——那需要任务级编排，不在本轮。
3. **复用分支会在机器人正在任务中时启用地图。** `_load_map` 走 `reserve`/`release`，与既有 `mapping.activate` 同路径，因此比它更危险的行为没有被引入；"要不要在任务中途换图"是产品决策，本轮没有单方面决定。
4. **Go 侧恢复目录没有改。** `map.re-survey` 仍然是"需要批准的有界写入"——**这个区别是刻意的**：用户主动要求建图是操作员意图，不该需要批准；而恢复流程自己发起重新巡检是补救动作，仍然要人同意。`build_map` 走的是 `SafetyLevel.NORMAL_MOTION` + `mutates_world`，与其它运动命令同一条安全准入，没有新建旁路。
5. **没有真机/仿真端到端跑通一句话建图。** 验证停在服务与工具层：决策、清单、编排、工具面都有测试，但"人对机器人说一句话 → 真的建出图来"这条链路没有在仿真或真机上走一遍。
