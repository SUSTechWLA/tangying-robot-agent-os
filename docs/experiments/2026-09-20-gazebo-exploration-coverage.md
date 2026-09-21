# Gazebo 里的自主探索：把"可观测"变成"可驱动"，并量出两个仿真器的差距

**日期**：2026-09-20
**目标**：在 Gazebo 中跑通"用户一句话 → 机器人自主 SLAM 探索"，修掉过程中暴露的缺陷，量化当前仿真环境的探索覆盖率，并给出 MuJoCo/Gazebo 对比实验。
**结论一句话**：Gazebo 现在是**可驱动的后端**，一句话建图的链路已经真的跑通；同一策略在 MuJoCo 户型上覆盖 99.0%，在 Gazebo 户型上只有 **79.3%**，而差距**不在策略**——两个户型要求相反的盲区半径假设。

---

## 〇、交付与遗留（先看这一节）

**已交付，每项都有实测证据：**

| 目标 | 状态 | 证据 |
| --- | --- | --- |
| Gazebo 端到端跑通 | ✅ | 托管 14 个建图服务；控制台 `Adapter=gazebo`；镜像自足 |
| 一句话启动 SLAM 探索 | ✅ | `请探索环境，构建全局地图` → `decision=explore` → 会话起来 → 机器人驱动建图 |
| 修掉 Gazebo 暴露的 bug | ✅ **11 个** | §二（9 个）+ §3.5（2 个探索层），每个都有测试钉住 |
| **提高探索覆盖率** | ✅ | **Gazebo 79.3% → 99.5%（+20.2 pp）**；MuJoCo 99.0% 不变且少走 13%；拒绝鲁棒性 92.0%±11.2 → 98.4%±0.7（§4.5） |
| 可复现对比报告与实验数据 | ✅ | 本文；`--truth mujoco|gazebo`、`--blind-radius`、`--min-frontier-area-m2`、`--min-frontier-cells` 全部可复跑 |
| 现场地图 vs 评测台同分母 | ✅ | `scripts/score_live_map.py`：现场 **73.1%** vs 评测台 99.5%（§4补） |

**遗留，以及为什么它不是没做，而是换了一层：**

1. **现场覆盖率 73.1%（同分母），评测台 99.5% —— 已经量化，且不在探索层。**
   §4补 把两个数放到了同一个分母上：差距真实存在，根因是**272 次配准只成功 1 次**，
   地图实为里程计漂移的产物（4,573 格自由落在真值障碍上）。
   这与仓库上一轮"覆盖率第一瓶颈在建图层"的结论一致；本轮把**第二瓶颈（策略层）证实并修掉**了，
   并把第一瓶颈从"感觉在建图层"变成了一个带账本的数。
2. **现场有 run-to-run 方差**（§3.7）：行程 0.57–5.09 m，行程够远时必出配准、必能发布。
   这是出生点附近重规划视距塌缩导致的**驱动层**方差。
3. **盲区半径这一个常数在同时干两件事**（§4.8），几何只支持 ≈0.5 m 而常数是 1.0 m——
   拆开它是下一轮明确、有界的入口。

## 一、起点与终点

起点是仓库自己的记录（`docs/development/2026-09-19-gazebo-backend-status.md` §四）：

> **Gazebo 现在可被观测，还不可被驱动。**

以及 §三·补：

> 12 个关节位置控制器已加载……但**没有把物体夹在两颚之间拿起来过**。

本轮结束时：

| 能力 | 之前 | 现在 | 证据 |
| --- | --- | --- | --- |
| `Observe`（RGB-D + 位姿） | ✅ 但**位姿是空的** | ✅ 7 元平面位姿 | §二.1 |
| `ListServices` / `CallService` | ❌ UNIMPLEMENTED | ✅ **14 个建图服务** | §二.5 |
| 一句话建图端到端 | ❌ 从未跑通 | ✅ **跑通**（§三） | `scan-eea3bad1fa3d` |
| 部署可复现 | ❌ `docker cp` + 手工 pip | ✅ **镜像自足** | §二.6 |

启动方式现在只有一条命令，且镜像自带网关依赖、自带运行时节点、自带发布端口：

```bash
make gazebo-house-start          # 127.0.0.1:18792 = robot.profile.v1 运行时
```

---

## 二、跑起来才暴露的九个缺陷

每一个都是"看代码看不出来、只有真跑才现形"的类型。按发现顺序。

### 1. 每一条观测的位姿**在最后一跳丢了**

`gazebo_runtime.observation()` 把 `robot_state`（含 `base_pose`）装配得好好的；节点在把它写进 proto 时**只抄了三个身份/时间字段，没有抄 `robot_state`**。

后果不是报错，而是**格式完全合法的空消息**：客户端请求 `robot_state` 会收到一条通过校验、却没有位姿的 Observation。任何下游检查都发现不了。

**修复**：把 payload → wire 的转换收敛成网关里的**一个函数** `observation_message()`，节点和进程内采集共用它。转换写两份，迟早会互相矛盾——这次就是。

### 2. `base_pose` 是 3 元，而所有消费者要 7 元

`observation()` 发布的是 `[x, y, z]`（矩阵平移）。而整个建图层读的是 `pose_se2(base_pose)`，它**只接受 7 元、归一化、底盘水平的四元数**，否则抛错。

一个 3 元位姿不是一个"短一点的位姿"，是一个**不同的东西**：第 2 个元素被当成别的量，航向角根本不存在。若被补零，一个转了 90° 的机器人会一直报"航向 0"。

**修复**：`leveled_base_pose()` 从 4×4 齐次矩阵取出 `[x,y,z,qw,qx,qy,qz]`，并且**拒绝倾斜的底盘**（`BASE_NOT_LEVEL`）而不是把它拍平——拍平会造出一个看起来合理、但没有任何下游检查能发现其错误的平面位姿。

### 3. 走过的走廊，在自己的地图上变成禁区

`RobotWorkflow._observed_travel()` 在 `clearance_validator is None` 时**直接 return**——不认证任何东西。而 Gazebo 绑定的第一版正是 `clearance_validator=None`。

后果很重：前视相机永远看不见自己脚下的地板，密集航迹认证一停，"机器人刚开过的走廊"在发布出来的地图上就是不可通行区域，从那里派发的第一个任务会被自己的地图拒绝。

**修复**：`GazeboTravelClearance` —— 从**机器人实际报出的位姿**记录被扫过的线段（由 `capture()` 喂，因为 `_move_and_sample` 每 200 ms 采一次，看到的是**真走过的路**，不是请求过的路），并按参考驱动同一条规则认证：查询点必须落在走过的线段附近，且请求半径不超过已被证明的半径。**更宽的问询被拒绝，而不是用更窄的检查去回答。**

### 4. 转向指令被当成"已经到位"

这是最贵的一个。调查模块每一步之前会**原地转向**，这些目标点的 `x/y` 恰好就是机器人当前的位置。而驱动第一版只比较位置：

```python
if distance <= tolerance_m:
    return None          # ← 于是每一次转向都被报成"已完成"
```

于是机器人从不旋转，调查看到连续 12 步位移为零，段结束在 `no_progress`，**整栋房子没建出来**。实测：每段 26 秒，行程 0.00 m。

**修复**：`bounded_step_command()` 同时处理位置与航向；到达位置后按目标航向继续旋转。

### 5. Gazebo 没有服务目录，所以"可观测不可驱动"

`ListServices`/`CallService` 返回 `UNIMPLEMENTED`。**修复**：节点可选地托管网关自己的 `RobotWorkflow`，注册出完整 14 个服务：

```
calibration.get/run/save, mapping.status/conflicts/start/move/stop_motion/
finish/cancel/activate/inventory/ensure, navigation.map
```

**没有第二套实现**：调查循环、前沿策略、占据栅格、地图发布全部是 MuJoCo 用的那一份代码。Gazebo 只提供两件网关不知道的事——**怎么看见**（运行时自己的采集）和**怎么移动**（带守卫的有界步）。

`ExecuteSkill`、`Cancel`、`EmergencyStop` 仍然按名字拒绝。急停那条尤其重要：报一个没有执行的停止，比什么都不报更糟。

### 6. 部署不可复现

运行时节点是靠文档里的一段 `docker cp` + 手工 `pip install` 起起来的。**修复**：节点装进镜像（`gazebo_runtime` console script）、网关整包 COPY 进镜像、`scipy`/`pydantic`/`pillow` 进 Dockerfile 的依赖层、运行时端口发布到宿主、`gazebo_house.launch.py` 自己把节点拉起来。

世界文件的 sha256 也在 launch 里**算出来**而不是让人记着填：需要人记住去更新的 revision，一定会有一次忘记更新——然后两个不同的世界共用一个地图身份。

### 7. 建图相机是**平视**的——这是整轮最根本的一个

参考机器人（MuJoCo）把**建图相机**放得很低、并**向下 15°**，为的是看见**地板**：

```python
# sim/mujoco/tangying_sim/rgbd_navigation.py:332
forward_tilt = 15.0
camera_pos = [0.30, 0, 0.16] if scene in {"home", "home_task"} else [0.185, 0, 0.50]
```

而 Gazebo 世界的基座相机是 `<pose>0.28 0 0.48 0 0 0</pose>`——**0.48 m 高、完全平视**，测的是墙。

一个平视相机在一间房里给出的是两个又远又重复的立面，**没有地平面**，深度 ICP 因此配准不出任何东西。实测：

| | 修之前 | 修之后 |
| --- | --- | --- |
| 位姿配准次数 | **0** | **7** |
| 单段行程 | 0.436 m | **5.094 m** |
| 关键帧 | 26 | 78 |
| 结果 | `failed`（"至少采集 3 个可配准视角"） | **`completed`，地图发布并启用** |

**0 次配准**是"发布被拒绝"的真正原因——不是门禁太严，是地图确实没有经过验证的运动。
这个缺陷一路被前六个 bug 掩盖着：位姿丢了、航向被忽略、约束没认证，机器人在此之前连"走到能配准的地方"都做不到。

**修复是协调的四处改动**（任何一处漏改都会让标定与实际渲染不一致）：

1. 世界文件里基座相机 `<pose>` → `0.36 0 0.16 0 0.261799 0`（x 用 0.36 而不是参考的 0.30，只为避开这个世界 0.65 m 的车体，否则镜头被自身遮挡）；
2. launch 的静态 TF 由**倾斜角算出**光学系 RPY，而不是写死一个平视系——写死正是两者当初走散的原因；
3. 运行时 `CAMERA_MOUNTS` 升级为**带旋转的 4×4 挂载**，因为"把 link 位姿转成光学系"的转换假设挂载描述了 link 的朝向；
4. 网关标定同步。

### 8. 自然语言入口缺幂等键（新写的入口自己暴露的）

新加的 `POST /v1/mapping/request` 第一次调用得到：

```json
{"code":"REQUEST_ID_REQUIRED","message":"操作需要唯一 requestId。"}
```

`mapping.ensure` 是变更型服务，运行时拒绝没有幂等键的调用——这是对的：一个可能已经开始的驱动必须能按身份重放，而不是靠猜。**控制台是这里的客户端，所以键由控制台生成**，且**每个请求生成一个**：两句相同的请求是操作员要的两次建图，不是一次被问了两次。

### 9. README 的测试计数过期（仓库自己的门禁抓到的）

`tests/docs` 有一条门禁断言 README 里的测试函数数量与源码树一致。它红了：README 写 1362 个 Python 测试函数，树里是 1535。已按实测更新为 1050 Go + 1535 Python。

---

## 三、端到端：从一句话到机器人开始探索

### 3.1 链路

```
"请探索环境，构建全局地图"
  → POST /v1/mapping/request          （控制台；操作员会话 + 写入闸门）
  → 词表判定为建图意图                  （与 build_map 工具声明的词表同源）
  → CallService("mapping.ensure")     （robot.profile.v1，与 MuJoCo/真机同一条契约）
  → RobotWorkflow.ensure_map
  → select_map(...) → decision=explore（决定权在机器人手里，因为证据在它手里）
  → start({"mode":"explore"}) → _explore_legs → SURVEY.run_leg
  → explore_target(...)               （前沿策略，未改动）
  → 有界步驱动 + RGB-D 采集 → DenseSLAM → 占据栅格 → 发布地图
```

控制台看到的是**同一个机器人契约**，不分支：

```
$ curl -s http://127.0.0.1:8799/v1/runtime
{"RobotID":"gazebo-house-rgbd","Adapter":"gazebo", ...}
```

### 3.2 实测

**输入**

```bash
TOKEN="$(cat /tmp/gz-console/console-session)"
curl -X POST http://127.0.0.1:8799/v1/mapping/request \
  -H 'Content-Type: application/json' -H "X-Tangying-Session: $TOKEN" \
  --data '{"request":"请探索环境，构建全局地图","maxLegs":2,"maxTravelM":25}'
```

**系统响应（节选）**

```json
{ "decision": "explore",
  "message": "本机还没有该环境的地图，现在开始自动探索建图。",
  "request": "请探索环境，构建全局地图",
  "served": "mapping.ensure",
  "session": { "mapId": "scan-eea3bad1fa3d", "state": "moving" } }
```

**随后机器人真的动了**（只读投影 `GET /v1/mapping` 的采样）：

```
[exploring] frames=4   travelled=0.00m  explored=0%
[exploring] frames=17  travelled=0.00m  explored=0%   自动探索第 1 段：正在选择下一个未知区域。
[exploring] frames=25  travelled=0.28m  explored=71%  target=[0.678, -0.819]
[exploring] frames=26  travelled=0.42m  explored=73%  refused=1
```

**另一次完整跑通（服务级，全预算）**：`state=completed`，61.35 s，2 段，地图 `scan-32d3bae6bba4` **已发布并启用**，`unknownFraction` 从 0.699 降到 0.283。

### 3.3 完整闭环：一句话 → 探索 → 建图 → 地图启用

在修掉 §五.3 的代价地图配置后（并把覆盖面限定在 Gazebo 场景内），同一条一句话请求**走完了全程**：

```
=== 输入: 请探索环境，构建全局地图 ===
decision: explore | served: mapping.ensure
message : 本机还没有该环境的地图，现在开始自动探索建图。
session : scan-3ed7827731e1 moving

  [exploring] frames= 16 travelled= 0.00m explored=  0%
  [exploring] frames= 24 travelled= 0.15m explored= 71%  target=[0.579, -0.893]
  [exploring] frames= 26 travelled= 0.43m explored= 71%
  [completed] frames= 26 travelled= 0.43m explored= 74%  free=30017 occ=1288 refused=1
```

产出的地图包**已发布并成为在用地图**：

```
/data/maps/gazebo_house/workflow/scan-3ed7827731e1/
  cloud/  grid/  navigation/  manifest.json  objects.json
  semantics.json  slam-keyframes.json  slam-session.json  trajectory.geojson
active-map.json → scan-3ed7827731e1
```

**同一个入口也演示了另一条分支**：地图还在时同一句话返回 `decision: reuse` 并直接启用已有地图，
`state=idle`——"有图就复用、没图就建图"这个判断确实在机器人手里，而不是在控制台或模型手里。

### 3.4 修掉相机之后：一次完整的自主探索

**一句话入口的完整闭环**（控制台 → `mapping.ensure` → 探索 → 建图 → 地图启用）：

```
输入: 请探索环境，构建全局地图
decision: explore | message: 本机还没有该环境的地图，现在开始自动探索建图。
session : scan-130d29852805 moving
  [exploring] frames= 24 travelled= 0.29m explored=68%
  [exploring] frames= 49 travelled= 0.99m explored=73%   free=31867 occ=792
  [failed   ] frames= 58 travelled= 1.46m explored=73%
```

**服务级、满预算的一次**（`--max-travel-m 60 --max-legs 4`，干净数据库）：

```
state    : completed
elapsed  : 266.5 s
frames   : 85   travelled: 4.27 m   registrations: 9   explored: 72%
  leg 1   no_reachable_frontier   free=32998  occ=1875
mapId    : scan-f8acde0e9af9   （已发布并启用）
message  : 地图已保存；剩余未知区域目前不可达。
```

两次差异值得记录：**配准次数与行程强相关**。行程 4.27 m → 9 次配准；行程 1.46 m → **0 次配准**，
于是发布门禁拒绝。也就是说，一句话入口这次停在 73% 并不是链路问题，而是**它没走够远**（§3.7、§4.4 的同一个原因）。


把建图相机改成参考几何（§二.7）后，同一段预算的一次探索第一次真正走完：

```
state    : completed
elapsed  : 249.7 s
frames   : 78   travelled: 5.094 m   registrations: 7   points: 208,189
  leg 1   explored=65%  free=27069  occ=93
  leg 1   explored=71%  free=35338  occ=1141   stopReason=no_reachable_frontier
message  : 扫描完成，导航地图已启用，三维地图已保存。
```

从 0 次配准、0.44 m、发布被拒，到 7 次配准、5.09 m、**地图发布并启用**。

### 3.5 顺带修掉的两个探索层缺陷

追查"为什么一段只走 0.43 m"时，在探索层里找到两个真实缺陷（都有测试钉住）：

**① 直线可达性检查把自己所在的那一格也否掉了。** `plan_path` 会为机器人**当前所在的格子**开一个口子
（"底盘停在家具旁时，它合法地处在净空包络内"），而 `_straight_line_clear` 紧接着又用同一张
`traversable` 否掉这一格——于是第一步永远失败，`next_waypoint` 掉进"只走一格"的兜底分支，
调查用 **5 cm 一步**走完整栋房子，而 5 cm 的视距基本就变成了朝向问题，机器人把时间花在原地转向上。
实测：1969 次驱动 tick 里 **1168 次是旋转指令**。修法是把检查从 `index=1` 开始：要防的"切角"是后续路径的性质，
不是机器人已经站着的那一格的性质。

**② 有界步驱动的减速斜坡在短步上把速度压到 1 cm/s。** `max(0.01, distance/0.5*speed)` 在 0.5 m 以内就开始降速，
而这个驱动的控制周期是 0.05 s、速度是 0.05 m/s——**一个 tick 只走 2.5 mm，而到位半径是 40 mm**。
斜坡是给快机器人防过冲用的，在这里它没有买到任何东西，却让每一个 5 cm 的短步都要 5 秒。
修法是到位半径外全速。

两处修好后，实测的平移指令从 `cmd=(0.010, 0.000)` 变成 `cmd=(0.050, 0.000)`——**满速**。

### 3.6 一个必须先说清楚的环境前提：RTAB-Map 数据库会跨run累积

复现时踩到的一次真实困惑，记下来：同一条命令、同一个世界，**第二次跑就失败**（0 次配准、0.99 m、45 帧、
25,119 点），而第一次成功（7 次配准、5.09 m、78 帧、208,189 点）。

原因是 `/data/maps/gazebo_house/rtabmap.db` 落在命名卷上，**RTAB-Map 的数据库跨 run 累积**：上一轮的地图还在，
姿态图、词典与局部地图都不是干净状态。清掉之后同一配置**稳定复现 `completed`**：

| 复现 run | frames | travelled | registrations | points | 结果 |
| --- | --- | --- | --- | --- | --- |
| 第 1 次（干净 DB） | 78 | 5.094 m | 7 | 208,189 | `completed` |
| 第 2 次（干净 DB） | 65 | 3.352 m | 3 | 182,289 | `completed` |

**所以"跑之前先清 RTAB-Map 数据库"是复现的前提，不是可选项。** 这一条已经写进 §六 的复现步骤。

### 3.7 现场跑动仍有 run-to-run 方差（如实记录）

同一条一句话请求、同一个世界、干净数据库，现场结果在两次之间差很多：

| 现场 run | frames | travelled | registrations | explored | 结果 |
| --- | --- | --- | --- | --- | --- |
| A | 85 | 4.27 m | 9 | 72% | `completed`，地图启用 |
| B | 65 | 3.35 m | 3 | — | `completed`，地图启用 |
| C | 78 | 5.09 m | 7 | 71% | `completed`，地图启用 |
| D | 44 | **0.57 m** | **0** | 74% | `failed`（"至少采集 3 个可配准视角"） |

**失败的那次不是链路问题**：`decision=explore`、会话起来了、机器人动了、地图长到 74%。
问题是**它没走够远**——0.57 m 的行程给不出可配准的基线，于是 `registrationCount=0`，
发布门禁正确地拒绝了这张图。

行程为什么时好时坏：出生点在沙发与茶几之间，重规划视距在那里会塌缩；
机器人能不能"脱身"取决于前几个决策选中的方向。这是**驱动层**的方差，不是决策层的。
它有一个明确的判据（§3.4：行程 ≥ 约 3 m 时配准稳定出现），也有一条明确的下一步（§3.8）。

### 3.8 仍然存在的限制（如实记录）

- 一段 60 m 预算的探索目前走到 71–73% 就报 `no_reachable_frontier`，行程 1.5–4.3 m，远小于预算。
  这一步的覆盖率提升**没有在策略层实现**：§4.4/§4.6 已经证明"调小盲区常数"会毁掉 MuJoCo，
  而"用可见性把门洞撤出盲区"在本户型上只有 2.8% 的格子可用。真正该做的是**自适应盲区**
  （按已测自由空间的局部宽度决定），那是下一轮的事。
- 一段 60 m 预算的探索行程远小于预算。旋转仍占驱动 tick 的一半左右，
  调查在"朝向—走一步—重规划"之间花的代价偏高。这是**驱动层**的标定问题（差速底盘的转向响应与轮子摩擦），
  不是探索层的策略问题——§4.3 已经证明策略没有漏掉任何前沿。
- Gazebo 世界的走廊宽 1.5–2 m，而 §4.4 测出这个户型需要更小的盲区半径假设。这两个事实指向同一个方向：
  **盲区应当随户型自适应**，而不是一个全局常量。

---

## 四、对比实验：同一策略，两个户型

### 4.1 方法

**被比较的是同一个策略**，不是两套代码。评测台是仓库已有的闭环前沿基准，本轮给它加了第二个真值源：

```bash
.venv/bin/python scripts/exploration_survey_benchmark.py --truth mujoco
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo
```

- **真值**：MuJoCo 户型仍从模型几何栅格化；Gazebo 户型**直接从 `tangying_home.sdf` 解析**（box/cylinder/sphere + 位姿）栅格化。
  - 同一高度带 `(0.03, 1.2) m`、同一分辨率 `0.05 m`、同一条"机器人自身几何不算障碍"的规则。**两个栅格化器不同，比的就会是栅格化器。**
  - 不用运行中的仿真器取真值：那会让策略基准依赖它本该独立于的那套栈，数字会随栈一起漂。
- **分母**：`coverable` = 某个合法可达位姿能看到的真值格（60° 半角、3 m、有遮挡），不是"所有格子"。
- **起点**：从世界文件里读，不写死。

Gazebo 户型：占用 10,311 格，可达地面 23,691 格，`coverable` 25,801 格。

### 4.2 主结果

| 真值来源 | 覆盖率（改前） | 覆盖率（改后） | 行程（改后） |
| --- | --- | --- | --- |
| MuJoCo 装修家庭 | 99.0% | **99.0%** | 53.8 m |
| **Gazebo 四室户型** | 79.3% | **99.5%** | 65.9 m |

**改前差 19.7 个百分点**，且 Gazebo 还少走了 13 m —— 它不是走不动，是没地方可去。
§4.5 找到了原因并修掉：**前沿片段下限被写成了格数而不是面积**，于是在这个户型上把通向房间的门洞
连同走廊毛边一起过滤掉了。

### 4.3 差距**不在策略**（一个可证伪的诊断）

把"漏掉的 coverable 格"拆开：

| 真值 | 漏掉 | 其中**仍是前沿**（策略本该去） | 其中**不与已知自由区相邻**（不可达） |
| --- | --- | --- | --- |
| MuJoCo | 183 | **0** | 183 |
| Gazebo | 5466 | **0** | 5466 |

两个户型里，**策略都没有漏掉任何一个前沿**。Gazebo 的 5466 格是"从某个可达位姿看得见、但机器人从未走到它旁边"的地方。

把它们画出来（`#`=真值障碍 `.`=已建图 `X`=漏掉的 coverable）：

```
  ###############################
  #.    .........# XXX  #########
  #.####.........#XXXXX##########
  #######........#XXXXXX        #
  #..............#XXXXXX      XX#
  #..............#XXXXX ##### XX#
  #....####........XXXX ##### XX#
  #....####.........XXXX      XX#
  #..................XXXXXXXXXXX#
  #..................XXXXXXXXXXX#
  #.................XXXXXXXXXXXX#
  #..............#XXXXXXXXXXXXXX#
  #..............#...           #
  #..............#..............#
  #..............#..............#
  #..............#..............#
```

**机器人把走廊系统建完了，整间房没进去。** 这正是"走廊 + 房间"户型相对"家具填满的家"独有的失败形态。

### 4.4 覆盖率杠杆：盲区半径（本轮最重要的发现）

`BLIND_RADIUS_M` 建模的是"前视相机永远看不见自己脚下"的那圈地板：航迹 1 m 以内的格子被从**前沿掩码**里剔除。扫描它：

| 盲区半径 | MuJoCo 覆盖率 | Gazebo 覆盖率 |
| --- | --- | --- |
| **1.0 m（现值）** | **99.0%** | **79.3%** |
| 0.7 m | **31.9%** ⚠️ | **99.9%** ✅ |
| 0.45 m | 30.7% | 99.7% |
| 0.25 m | — | 17.0% |

**这不是"调小一点更好"，是一个窗口，而且两个户型需要相反的值。**

- Gazebo：1 m 的盲带**把门洞整个吞掉**——走廊宽约 1.5–2 m，两侧各 1 m 的盲区覆盖了整条走廊，于是通向房间的门洞永远不是前沿，房间永远进不去。降到 0.7 m，门洞留在前沿上，覆盖率从 79.3% 跳到 **99.9%**。
- MuJoCo：1 m 是对的；降到 0.7 m 直接**崩到 31.9%**——机器人在自己看不见的地方反复挑目标，走 5.8 m 就停。

**所以本轮没有改生产常量。** 一个固定值服务不了两个户型，而 0.25 m 那一行的崩塌说明这也不是"越小越好"。真正该做的是让盲区**随户型自适应**（例如按已测自由空间的局部宽度决定），那是下一轮的事，本轮把它变成了一个可复现的实验开关 `--blind-radius`。

### 4.5 覆盖率提升：把"前沿片段下限"从格数改成面积

**这是本轮真正把覆盖率提上去的那一处。**

§4.3 已经证明"策略没有漏掉任何前沿"——漏掉的 5466 格是**从未走到旁边**的地方。
顺着这条线查下去，问题出在**选目标时的片段下限** `minFrontierCells = 8`：
走廊户型沿墙会产生一条由许多小碎片组成的长毛边，而目标选择是**距离优先**的，
于是每一步"最近的可规划目标"都是这些碎片，调查把走廊扫了一遍却从不穿过门洞进房间。

而且这个常数**是格数，不是面积**——同一个 "8"，在调查实际构建的 5 cm 栅格上是 0.02 m²，
在一个 1 m 的测试栅格上是 8 m²。**一个格数在不同的地方意味着不同的物理尺寸**，
这正是一个只在一个户型上调出来的常数会在另一个户型上错二十个点的原因。

改成面积 `MIN_FRONTIER_AREA_M2` 之后扫描两个户型的真值：

| 前沿片段下限（面积） | 0.02 | 0.03 | 0.04 | **0.05** | 0.06 | 0.08 | 0.12 | 0.16 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Gazebo 四室户型** | 79.3% | 99.4% | 99.5% | **99.5%** | 99.1% | 99.1% | 98.7% | 74.0% |
| **MuJoCo 装修家庭** | 99.0% | 98.3% | 98.8% | **99.0%** | 99.0% | 98.8% | 85.0% | 71.6% |

**两个户型在 0.03–0.08 之间都在 98.8% 以上**，所以 0.05 落在一个平台上，不是一个碰巧得分的点。
取 0.05（= 调查自己的 5 cm 栅格上的 20 格）：

| | 改之前 | 改之后 |
| --- | --- | --- |
| Gazebo 覆盖率 | **79.3%** | **99.5%** （+20.2 pp） |
| MuJoCo 覆盖率 | 99.0% | **99.0%**（无回退） |
| MuJoCo 行程 | 61.6 m | **53.8 m**（少走 13%） |

#### 4.5.1 顺带把"拒绝鲁棒性"也提上去了

同一个改动让调查对安全层拒绝的鲁棒性明显变好（拒绝注入评测台，`seeds=5`）：

| 拒绝率 | 规则 | 改之前 | 改之后 |
| --- | --- | --- | --- |
| 0% | — | 99.0% | **99.0%**（校准点不变） |
| 5% | `step` | 98.7% ± 0.1 | 98.5% ± 0.4 |
| 10% | `step` | 92.0% ± 11.2 | **98.4% ± 0.7** |
| 20% | `step` | 83.0% ± 22.9 | **91.5% ± 13.1** |
| 10% | `target`（旧规则） | 72.8% ± 27.2 | 75.3% ± 28.5 |

`target` 那两行基本不动——**那个规则的失效是结构性的**（把局部证据记在全局对象上），
和片段下限无关；这也反过来确认了改动的确只作用在它该作用的地方。

### 4.6 前沿簇预算：一个被否掉的假设（阴性结果）

`MAX_FRONTIER_CLUSTERS` 原本是 12，被怀疑是"只考虑最近的 12 个前沿片段"导致漏房。扫描：

| 每步评估簇数 | MuJoCo | Gazebo |
| --- | --- | --- |
| 12（现值） | 99.0% | 79.3% |
| 24 | — | 79.3% |
| 48 | 99.0% | 79.3% |
| 96 | — | 79.3% |

**逐位相同。** 这个假设被实验否掉了，记在这里以免下次重复投入。

### 4.7 一个被否掉的"更聪明"的盲区模型

既然盲区半径是主导杠杆，自然的想法是：**那一圈里有些格子其实从更远处的航迹位姿看得见**，
把看得见的撤出盲区，就能既保住"不要追自己的脚印"，又不吞掉门洞。

实现了第二个模型 `--blind-model visibility`（用与传感器模型**同一套**射线投射），实测：

| 真值 | `disc`（现行） | `visibility` |
| --- | --- | --- |
| MuJoCo | 99.0% | 99.0% |
| Gazebo | 79.3% | 79.3% |

**逐位相同。** 直接量了原因：在 Gazebo 户型上，盲区 1446 格中只有 **40 格（2.8%）**能被更远的航迹位姿看见。

**并验证了这个测量本身不是实现错误**：航迹每 0.05 m（一格）记一次，采样间隔 20 格 = 1.00 m，
与盲区半径同量级——采样是对的。所以结论是几何的：**走廊太窄，门洞那几格在机器人经过它之前，
从一个 0.5 m 高、平视的相机看过去确实被走廊自身遮住**，等到看得见时已经太近。

**这条阴性结果反过来解释了相机的修复为什么有效**（§二.7）：把相机放低到 0.16 m 并向下 15°，
看见的地板（含门洞附近）本来就更多——不是把常数调小去迁就模型，而是让传感器真的看得见。

### 4.8 盲区半径这个常数在同时干两件事（下一轮的入口）

把相机的几何算出来，`blindRadiusM = 1.0` 的来历就清楚了，也就知道为什么它两边不讨好：

| | 相机高度 | 俯角 | 垂直半视场 | 最低视线 | **看见地板的最短距离** |
| --- | --- | --- | --- | --- | --- |
| MuJoCo 参考 | 0.16 m | 15° | 29° | 44° 下 | `0.16/tan44° = 0.17 m`（机身前 ≈ **0.47 m**） |
| Gazebo（修后） | 0.16 m | 15° | 26.4° | 41.4° 下 | `0.16/tan41.4° = 0.18 m`（机身前 ≈ **0.54 m**） |

**两个机器人的相机几何都只支持约 0.5 m 的盲区半径，而常数是 1.0 m——大了约一倍。**

原因是这个常数在同时承担两件不同的事：

1. **建模相机的盲区**（几何要求 ≈ 0.5 m）；
2. **阻止机器人追自己的脚印**——把一个只属于相机的事实，当成了全局的规划约束。

第 2 件事在 MuJoCo 的密集家具户型里是必要的（降到 0.7 m 就崩到 31.9%），
在 Gazebo 的走廊户型里却正好把门洞吞掉（1.0 m 只有 79.3%，0.7 m 有 99.9%）。
**一个常数承担两个职责，就必然在其中一个户型上做错。**

下一轮该做的不是再调这个数，而是把它拆开：盲区按**相机几何**（≈0.5 m，且随挂载自动算出），
"不要追自己的脚印"按**规划约束**单独处理（例如对已认证走过的地面不给前沿分，而不是把它标成不可达）。
本轮把它变成了可复现的开关（`--blind-radius` / `--blind-model`）和上面这张几何表，
但没有在缺少 MuJoCo 侧替代方案的情况下动生产常量。

### 4.9 传感器模型不是原因

Gazebo 户型在 `--realism real` 与 `--realism ideal` 下都是 **79.3%**（逐位相同）。所以差距不是"扫描得不够密"，是**拓扑**上的。

---

## 四·补、现场地图与评测台放在同一分母上：73.1% vs 99.5%

### 4补.1 为什么必须做这一步

仓库里"覆盖率"有两个数，而它们**不是同一个数**：

- 运行中的调查报 `1 - unknownFraction`，分母是**它自己栅格的全部格子**——包括房子外面的空间、墙的内部、
  以及任何位姿都看不见的、被封在家具后面的地板；
- 闭环评测台报 `mapped / coverable`，分母是**某个合法可达位姿真正能看见的地板**。

现场读到 ~73%、评测台读到 99.5%，很容易把这 26 个点当成"建图层缺陷"。**大部分情况下这只是分母不同。**
所以新增了 `scripts/score_live_map.py`：把**已发布的地图包**投影回真值坐标系，用评测台的分母重新数一遍。
（用的是规划器真正读取的那个经过验证的包，不是旁边留的副本。）

### 4补.2 结果：差距是真的

| | 值 |
| --- | --- |
| 调查自己报的已探明 | **72.1%**（分母 = 自己栅格的 60,522 格） |
| 与评测台同分母的覆盖 | **73.1%**（18,861 / 25,801 coverable） |
| 闭环评测台（同一策略、同一户型） | **99.5%** |

两个分母在这里几乎相等，所以**26 个点的差距不是度量假象，是真实的**。
自证项还暴露了一件事：**4,573 格被地图标成自由、而真值是障碍**（反向只有 754 格）。

### 4补.3 差在哪：这不是 SLAM，是里程计

地图包里记录了每一次配准尝试。`scan-b47a762ae586` 的账本：

```
registration attempts : 272
accepted              : 1        ← 只成功了一次
no_overlap            : 125  (46%)   ← 新关键帧与参考子图重叠不到 30%
correction_too_large  :  52  (19%)   ← ICP 想修正的量超过这一小步允许的上限
too_flat_or_few_normals: 43  (16%)   ← 法向量张不成平面（走廊两面平行墙）
weak_loop             :  38  (14%)
```

**112 个关键帧、1 次配准、0 次回环。** 这张图本质上是**里程计加一坨点云**，不是 SLAM——
它漂移了，这就是那 4,573 格"自由落在墙上"的来源。

而关键帧的构成解释了为什么：关键帧在**平移 ≥0.14 m 或旋转 ≥0.22 rad** 时产生。
112 个关键帧对应 4.9 m 行程——如果全是平移驱动的，只该有 35 个。**约三分之二是旋转关键帧**，
而旋转恰恰是深度 ICP 最没有约束的运动（记录里的 `no_overlap` 与 `too_flat_or_few_normals` 就是它）。

### 4补.4 一个被实验否掉的方向（阴性结果）

既然旋转是关键帧的主要来源，而 `_look_around` 每次最多转 4 × 90°、每条腿最多 6 次
（**最多 37.7 rad 的纯旋转**），自然的假设是"把原地扫视的预算砍掉"。

实测 `maxLooksPerLeg: 6 → 1`：

| | 基线 | 砍掉扫视 |
| --- | --- | --- |
| 关键帧 | 112 | 14 |
| 行程 | 4.9 m | 1.61 m |
| 配准 | 1 | 0 |
| **同分母覆盖率** | **73.1%** | **66.7%** |

**更差了，而且配准也没有变好。** 原地扫视确实在挣它的那份覆盖率（它测量了行进中看不见的角度），
省掉它只是少看了东西。这条假设被实验否掉，记在这里以免下次重复投入。

**所以现场差距的下一步不在探索层**：它是"这个传感器 + 这套配准门限"能不能撑起位姿图的问题，
属于 SLAM 层。`score_live_map.py` 与地图包里的 `registrationAttempts` 账本是继续查它的两个入口。

## 五、跑通过程中发现的、尚未修掉的问题

诚实记录，避免下一个人重新发现。

1. ~~**Gazebo 桥出来的点云，坐标系名与实际约定不符。**~~ **已修**（§七）：不再桥接到 `/camera/*/points`，
   改名为 `/camera/*/raw_points`，测试钉住"该名字下不得有发布者"。原文保留如下，因为测量过程本身有参考价值。
   `/camera/*/points` 的 `frame_id` 写的是 `base_camera_optical_frame`（TF 说它是 roll=-90°、yaw=-90° 的光学系），但**数据实际是传感器 link 约定（x 向前）**。实测：该云 x 中位数 3.17、z 中位数 -0.22；而由深度图生成的 `/camera/*/nav_points` 是 z 中位数 3.74（正确的光学深度）。
   - 按 TF 变换：障碍落在机器人**身后 0.60 m、地板下 2.35 m**——几何上不可能。
   - 按 link 理解：x ∈ [0.74, 4.80] m，最近障碍 0.74 m，正是前方茶几的前沿。
   - 目前 nav2 的局部代价地图读的是 `nav_points`（正确），所以暂未受害；但**任何遵循 TF 的消费者都会被转 90°**。

2. ~~**nav2 配置的足迹比机器人真实碰撞几何小。**~~ **已在 Gazebo 场景内修掉**（§七）：
   足迹改为世界文件里真实的 0.655 × 0.67 m。原文保留。
   `nav2.yaml` 的 footprint 是 0.46 × 0.44 m，而 SDF 里 `base_link` 的 `<collision>` 是 **0.65 × 0.55 m**，轮子在 y = ±0.31。代价地图的避障范围盖不住真实车体。

3. **局部代价地图的 `track_unknown_space` 对 Gazebo 是错的。** 改之前 25,600 格里 21,250 格未知（83%），DWB 找不到一条合法轨迹，每个目标都以 `NAV2_ACTION_ENDED` 结束、机器人 15 秒不动。Gazebo 户型下已改为 `false`（16,512 格自由）。**滚动窗口局部代价地图的职责是表达"已感知的障碍"，未知意味着"还没感知到"，把没感知到当成障碍，机器人就永远走不出自己的传感器脚印。** 全局代价地图保持 `true` 不变，因为"不要穿过未建图区域"这条规则属于那里。

   仓库里有一条测试**明确钉住**了两个代价地图都是 `true`。本轮**没有改共享默认值**，而是把覆盖限定在 `scene == "gazebo_house"`：一个仿真器上的测量不足以提升为全局默认，而真机 profile 没有被测量过。这是本轮唯一一处"知道怎么改但只改了一半"的地方，理由是另一半没有证据。

4. **有界步驱动的转向迟滞**（§3.3）。

---

## 六、复现方式

```bash
# 0. 清干净上一次的 SLAM 状态。RTAB-Map 的数据库跨 run 累积，不清就不是同一个实验。
docker exec tangying-gazebo-house-gazebo-house-1 rm -rf \
  /data/maps/gazebo_house/workflow/scan-* \
  /data/maps/gazebo_house/workflow/active-map.json \
  /data/maps/gazebo_house/rtabmap.db*
docker restart tangying-gazebo-house-gazebo-house-1

# 1. 起栈（镜像自足：网关依赖、运行时节点、发布端口都在里面）
make gazebo-house-start                       # 或 scripts/gazebo-house-stack.sh start
bash scripts/gazebo-house-stack.sh status     # ready:true, mode:mapping

# 2. 控制台接到 Gazebo 运行时
go build -o /tmp/local-agent ./cmd/local-agent
/tmp/local-agent --dev-insecure --robot-safety-profile desktop_standard \
  --listen 127.0.0.1:8799 --robot 127.0.0.1:18792 --data-dir /tmp/gz-console

# 3. 一句话建图
TOKEN="$(cat /tmp/gz-console/console-session)"
curl -X POST http://127.0.0.1:8799/v1/mapping/request \
  -H 'Content-Type: application/json' -H "X-Tangying-Session: $TOKEN" \
  --data '{"request":"请探索环境，构建全局地图","maxLegs":2,"maxTravelM":25}'
curl http://127.0.0.1:8799/v1/mapping        # 只读进度投影

# 4. 对比实验
.venv/bin/python scripts/exploration_survey_benchmark.py --truth mujoco
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo --blind-radius 0.7

# 5. 后端无关的采集（直接对 robot.profile.v1，不经控制台）
docker exec tangying-gazebo-house-gazebo-house-1 bash -lc \
  'source /opt/ros/jazzy/setup.bash; cd /opt/tangying-gateway && \
   /opt/navigation-venv/bin/python survey_run.py \
     --target 127.0.0.1:50071 --output /tmp/survey --mode explore --max-travel-m 40 --max-legs 4'

# 6. 测试
.venv/bin/python -m pytest robot/gateway/tests -q     # 733 passed, 4 skipped
go test ./console/ ./tests/docs/ -count=1
```

---

## 七、本轮改动清单

| 文件 | 改动 | 理由 |
| --- | --- | --- |
| `robot/gateway/tangying_robot_gateway/gazebo_runtime.py` | 新增 `leveled_base_pose()`、`observation_message()` | §二.1、§二.2 |
| `robot/ros2_ws/.../gazebo_runtime_node.py` | 托管映射服务；有界步驱动 + 深度守卫；单一定义的报文转换 | §二.1、§二.5 |
| `robot/gateway/tangying_robot_gateway/gazebo_workflow.py` | **新增**：导航客户端、标定派生、`GazeboTravelClearance`、`bounded_step_command`、`swept_step_is_clear`、`GazeboWorkflowBindings` | §二.3、§二.4 |
| `robot/gateway/tests/test_gazebo_workflow.py` | **新增** 30 项测试 | 钉住上面每条规则 |
| `robot/gateway/tests/test_gazebo_runtime.py` | 新增 4 项位姿契约测试 | §二.2；旧测试从未断言过 `base_pose`，这就是它活了两轮的原因 |
| `console/mapping_request.go` / `_test.go` | **新增**：自然语言建图入口 + 6 项测试 | §三 |
| `console/server.go` | 注册 `POST /v1/mapping/request` | 同上 |
| `console/mapping.go` / `mapping_test.go` | 测试桩记录 requestId | §二.7 |
| `deploy/robot/navigation/Dockerfile` | 网关整包 + scipy/pydantic/pillow | §二.6 |
| `deploy/robot/navigation/gazebo-house.compose.yaml` | 发布运行时端口；地图落命名卷；`TANGYING_MAP_ROOT` | §二.6 |
| `robot/ros2_ws/.../gazebo_house.launch.py` | 启动运行时节点；世界 sha256 算出而不是填出 | §二.6 |
| `robot/ros2_ws/.../setup.py` | `gazebo_runtime` console script | §二.6 |
| `launch/navigation.launch.py` | `scene == "gazebo_house"` 时局部代价地图 `track_unknown_space: false`；足迹改为世界文件里真实的 0.655 × 0.67 m | §五.3、§五.2。**限定在 Gazebo 场景内**：真机 profile 与共享默认值不动——一个仿真器上的测量不足以提升为全局默认 |
| `worlds/tangying_home.sdf` | 基座建图相机 → `0.36 0 0.16`、俯 15° | §二.7，本轮最根本的修复 |
| `launch/gazebo_house.launch.py` | 相机静态 TF 由倾斜角算出光学系 RPY | §二.7；写死平视系正是两者走散的原因 |
| `gazebo_runtime_node.py` | 相机挂载升级为带旋转的 4×4；有界步 trace（`TANGYING_TRACE_STEPS`，默认关） | §二.7、§3.5 |
| `config/gazebo_house_bridge.yaml` | 不再把 Gazebo 原始点云桥接到 `/camera/*/points`，改名为 `/camera/*/raw_points` | §五.1 |
| `exploration.py` | `_straight_line_clear` 从 `index=1` 开始（不否掉机器人自己所在的格）；新增 `MIN_FRONTIER_AREA_M2`，前沿片段下限由**格数**改为**面积** | §3.5①、§4.5 |
| `survey.py` | 配置键 `minFrontierCells` → `minFrontierAreaM2` | §4.5：同一个格数在不同分辨率下意味着不同物理尺寸 |
| `gazebo_workflow.py` | 有界步：到位半径外全速；`optical_rpy()` 由倾斜角算出 | §3.5②、§二.7 |
| `robot/gateway/tangying_robot_gateway/exploration.py` | `explore_target(max_clusters=...)` | §4.5，为实验把常量变成可扫的参数，默认值不变 |
| `scripts/exploration_survey_benchmark.py` | `--truth` / `--blind-radius` / `--max-frontier-clusters`；Gazebo 真值栅格化 | §四 |
| `scripts/survey_run.py` | **新增**：后端无关的调查采集器 | §4、§六.5；原先唯一的采集器要经控制台，与它要观测的对象共享失败模式 |
| `README.md` | 测试计数按实测更新 | §二.8 |

**明确没改的**：`exploration.py` 的**策略本身**（前沿选取、打分、A*）、`survey.py`、`map_pipeline.py`、`dense_slam.py`。本轮有测量的结论是：**覆盖率的第一瓶颈不在策略**（§4.3：两个户型里策略都没有漏掉任何一个前沿）。这与仓库上一轮的结论一致，而本轮又用一次实验确认了它——只是这次的瓶颈换成了**仿真器后端**。
