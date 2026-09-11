# 躺营 · Tangying Robot Agent OS

**当前聚焦：一台机器人在四房间家庭场景里，把一句中文指令真正做完。** 你说“从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅”，系统自己拆步骤、走到厨房、看清杯子在哪、抓起来、放进收纳盒并回头确认，每一步都用**命令之后新采集的画面**证明做完了——返回码不算完成。

多机器人（Fleet）、RoboCasa 交接、固定工位等能力仍在仓库里，但当前不是主线，见文末[其他路线](#其他路线暂不聚焦)。

## 一条指令，完整闭环

任务：

```text
从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅
```

系统会走完这条链路（每一步都绑定同一次 RGB-D 采集）：

| # | 步骤 | 工具 | 这一步在证明什么 |
| --- | --- | --- | --- |
| 1 | 看一眼现场 | `observe_scene` | 当前能看到哪些物体与房间 |
| 2 | 走到厨房 | `navigation.navigate` | 底盘真的移动了 |
| 3 | 确认到达 | `verify_arrival` | 用**新的**底盘相机与位姿误差确认，不是相信回执 |
| 4 | 重新观察 | `observe_scene` | 到达后重新感知，不复用出发前的画面 |
| 5 | 解析目标 | `resolve_targets` | 红色杯子与蓝色收纳盒在场景中的身份 |
| 6 | 规划抓取 | `plan_grasp` | 抓取位姿可行性 |
| 7 | 拿起 | `manipulation.pick` | 物理动作 |
| 8 | 确认拿起 | `verify_grasp` | 物体确实离桌并在夹爪中 |
| 9 | 放下 | `manipulation.place` | 物理动作 |
| 10 | 确认放好 | `verify_placement` | 连续三帧稳定处于 `inside:kitchen-bin` |
| 11 | 回到客厅 | `navigation.navigate` | 底盘再次移动 |
| 12 | 确认回到客厅 | `verify_arrival` | 同第 3 步的判据 |

工具都是 LLM 可直接 function calling 调用的（`tools.json`），执行仍然走同一条 `ExecuteSkill` 通道与安全监督；模型不接触关节角、轮速或坐标。

## 跑起来

前置：Go 1.26、Python 3.11、Node.js 22、Git、Make。**不需要 LLM API Key**，也不需要 Docker。

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
make setup          # 安装 Python / Go / 前端依赖，首次较久
make home-start     # 启动四房间家庭场景（含厨房红色杯子）
```

打开 <http://127.0.0.1:8787/>，把上面那句指令粘进输入框，先核对系统拆出的步骤，再批准执行。预期结果：任务 `SUCCEEDED`，红色杯子在 `kitchen-bin` 内，机器人回到客厅。

想直接在命令行复现同一条任务（会保留每一步的原始观测与 RGB/depth 字节）：

```bash
make home-accept     # 输出到 artifacts/acceptance/home-mobile-run-1/
```

停止与查看：

```bash
make sim-status
make sim-logs
make sim-stop
```

`make home-start` 与 `make home-accept` 都显式使用 `--scene home_task`。省略 `--scene` 会沿用上一次记录的场景，可能打开没有厨房物体的路线场景，那样抓取任务必然找不到目标。

## 这条闭环覆盖了什么

| 能力 | 状态 | 怎么验证 |
| --- | --- | --- |
| 家居自然语言拆解（客厅/厨房/卧室/卫生间、拿取、放置、返回） | ✅ 已验证 | 上面的任务，12 步全部有证据 |
| 底盘导航 | ✅ 已验证 | `navigation.navigate` + `verify_arrival` |
| 机械臂抓取与放置 | ✅ 已验证 | `manipulation.pick` / `manipulation.place` + 两个 `verify_*` |
| 相机感知（头部 + 底盘双 RGB-D、深度、点云） | ✅ 已验证 | 工作台“机器人视野”，以及每步绑定的采集 |
| 完成后确认（闭环契约） | ✅ 已验证 | 写工具缺新鲜证据即失败关闭，见下 |
| SLAM 建图（RTAB-Map）| ⚠️ 可运行，需先探索建图 | [下一节](#真实-slam-建图与-nav2) |
| 安全工具（急停、恢复安全位姿、限速） | ✅ 已注册 | `emergency_stop`、`recover_to_safe_pose`、`set_speed_limit` |
| 实机（XLeRobot） | ⚠️ 需现场验收 | [真机路径](#真机sim2real) |

**闭环契约**：`mutates_world=True` 的工具（导航、抓取、放置、恢复位姿）返回成功只是**触发观察**。系统要求一条命令派发之后采集、带观测标识的新鲜证据；缺证据时该步骤保持未完成、任务进入可恢复失败。判定与失败分类在 [`core/closedloop`](core/closedloop/closedloop.go)。

## 真实 SLAM 建图与 Nav2

上面的闭环使用仿真内的导航控制器，用来验证任务链路与证据合同。要跑**真正的 RTAB-Map 建图 + Nav2 规划**（需要 Docker）：

```bash
make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'
make navigation-status
```

`navigation-status` 就绪时会打印 `ready: true`、`mode: mapping`、`mapRevision`（地图身份）与 `poseSource: rtabmap_tf`。地图持久化在容器卷 `/data/maps/home/rtabmap.db`。

**建图阶段的行为是刻意设计的安全门禁**：全局代价地图的 `allow_unknown=false`，Nav2 拒绝规划穿过尚未建图的区域。所以冷启动后只能导航到已覆盖范围（例如客厅），去厨房会以 `NAV_FAILED NAV2_ACTION_ENDED` 结束——这不是缺陷，而是“不知道的地方不进去”。因此正确顺序是：

1. **建图**：`--mode mapping` 下低速探索覆盖五个房间，确认视觉词典、占据图与定位都在更新；
2. **执行**：`--mode localization --scene home` 复用同一张地图，再运行自然语言路线；
3. **抓取**：需要厨房物体的任务用 `--scene home_task`，它在此基础上增加可见的红色杯子与蓝色收纳盒。

当前仓库**还没有自动探索（frontier）工具**：建图阶段的覆盖需要受控低速驱动。这是通往实机的下一个缺口，详见[家庭场景操作](docs/guides/home-scene-operations.md)。

## 真机（Sim2Real）

同一套任务、工具与证据合同在实机上不变，替换的是适配器、感知、标定与现场安全配置：

```bash
./install.sh robot-pi --dry-run --yes     # 先预览安装计划
./install.sh robot-pi                     # 树莓派 / 机器人上位机
robot-agent doctor robot-pi               # 上电前离线检查
```

真机放行前必须完成：双 RGB-D 与里程计标定、地图覆盖、刹车与实体急停、机械臂碰撞边界、以及至少 30 次受监护的路线/抓取验收。逐步清单见[家庭 Sim2Real](docs/guides/home-sim2real.md)与[发布检查清单](docs/operations/release-checklist.md)。软件发布与实机放行是**两项独立结论**。

## 日常使用

| 入口 | 用途 |
| --- | --- |
| 工作台 | 输入任务、核对拆解、批准执行、看机器人相机画面 |
| 任务记录 | 按任务编号查找历史任务，筛选成功/失败 |
| 任务全过程回放 | 逐步对齐工具调用、观测证据与恢复状态，列出不一致项 |
| 我的机器人 | 连接状态、运行环境、相机画面 |
| 开发诊断 | 需要"开发模式"；事件、命令编号与底层状态 |

一次启动全部组件（默认仿真 + 本地控制台）：

```bash
./scripts/start-all.sh up          # 也可以用 make up / make down / make stack-status
./scripts/start-all.sh status
./scripts/start-all.sh down
```

## 仓库里有什么

代码按运行位置分类，权威说明见[部署目标与代码归属](docs/deployment.md)：

| 目标 | 内容 | 入口 |
| --- | --- | --- |
| 本地单机 | Local Agent、工作台、任务账本（**当前主线**） | `./install.sh local` |
| 机器人端 | ROS 2 网关与安全监督、xlerobot 驱动 | `./install.sh robot-pi` |
| 云端 | Fleet 控制面（暂不聚焦） | `./scripts/fleet-up.sh up` |
| 仿真 | MuJoCo 四房间家庭场景 | `./install.sh sim` |

共享运行时代码：`agent/`、`core/`、`orchestration/`、`tasks/`、`skills/`、`middleware/`；家居场景在 `sim/mujoco/`，工具层在 `robot/gateway/`，前端在 `web/`。

## 其他路线（暂不聚焦）

以下能力保留且仍有测试，但当前不投入：

- **多机器人 / Fleet**：`cmd/fleet-control-plane`、`fleet/`。单机器人把自己的任务做完即可；多机协调属于调度层，不影响上面的闭环。
- **RoboCasa 双机交接**：`./scripts/fleet-sim.sh handoff`；Compose 云端流程为 `./scripts/fleet-up.sh up`。旧 `./install.sh cloud` 只返回迁移提示。
- **固定工位桌面任务**：`make rgbd-start`（`--scene tabletop`）。

它们的文档仍在文档索引里，架构决策见 [World/Harness 设计](docs/superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md)与[早期 Local-first 设计](docs/superpowers/specs/2026-08-18-local-first-runtime-design.md)。

## 开发与验证

```bash
make build           # 二进制
make test            # Go + Python + Web 全部单元与契约测试
make lint            # gofmt + ruff + tools.json 一致性
make generate-check  # proto 生成物是否与仓库一致
```

常见 CLI（按所在机器选角色）：

```text
robot-agent doctor ROLE
robot-agent configure ROLE KEY=VALUE
robot-agent pair ROBOT_HOST --ssh-user USER
robot-agent start ROLE
robot-agent status ROLE
robot-agent logs ROLE --follow
robot-agent demo
```

文档入口：[完整文档索引](docs/README.md) · [家居场景操作](docs/guides/home-scene-operations.md) · [RGB-D 闭环原理](docs/development/single-robot-loop.md) · [机器人工具层](docs/development/robot-tool-layer.md) · [分支与发布规范](docs/development/branching.md)。本版变更见 [Changelog](CHANGELOG.md)，发布身份见 [v0.5.0 发布记录](docs/releases/v0.5.0.md)。
