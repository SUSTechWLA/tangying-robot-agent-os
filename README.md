# 躺营 · Tangying Robot AgentOS

[![License: MIT](https://img.shields.io/badge/License-MIT-167d72.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.0-167d72.svg)](CHANGELOG.md)
[![Go 1.26](https://img.shields.io/badge/Go-1.26-167d72.svg)](go.mod)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-167d72.svg)](pyproject.toml)
[![仿真演示免 Key](https://img.shields.io/badge/仿真演示-不需要%20API%20Key-2ad0bb.svg)](#跑起来)
[![仿真免 GPU](https://img.shields.io/badge/仿真-纯%20CPU%20可跑-2ad0bb.svg)](#跑起来)

### 说一句中文，机器人自己把它做完——而且**每一步都能证明自己真做了**。

一套面向家庭场景与具身机器人的**分布式 AgentOS**：把大模型规划、任务编排、世界裁决与机器人实时执行，拆成可替换的控制面与边缘运行时。

```text
从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅
```

这句话进去，系统自己拆成 12 步：走到厨房 → 感知陶瓷杯 → 抓稳 → 放进收纳盘 → 回到客厅。
**每一步都用「命令之后新采集的画面」验证，不是用工具返回的"成功"。**

| | |
| --- | --- |
| 🦾 **真闭环** | 14 个会改变物理世界的工具，返回成功只是**触发观察**，缺新鲜证据就是没做完 |
| 🧠 **模型不碰底层** | 28 个工具直接 function calling；**关节角与位姿级接口不暴露给模型** |
| 🌐 **一开始就是分布式** | Local Agent + SQLite 起步，云端 Fleet 扩展到多机；**不是先写单机再重构成分布式** |
| 🔌 **开箱即用** | 默认 Gazebo + ROS 2 Jazzy，支持四个场景入口；需要 Docker，支持 CPU 软件渲染。旧 MuJoCo 任务基线保留显式入口 |
| 🔬 **工程可见** | 69 个 Go 包 1079 个测试函数 + 1670 个 Python 测试函数、144 篇文档、验收数字可复现 |

---

## 为什么值得一看

市面上讲「大模型控制机器人」的项目，大多数停在**工具调用成功**这一步：调用 `pick` 返回 `SUCCESS`，任务就标记完成了。

物理世界不是这样工作的。杯子可能没夹住、可能滑了、可能夹的是旁边那个。所以这个项目把边界划在了别处：

> **工具返回成功，只是「去看一眼世界」的触发器，永远不是「世界已经按预期改变」的证明。**

这一条假设，往下推出了整套设计——闭环契约、证据校验、八类失败分类、结果未知禁止重试。这是它和普通机器人 Demo 最根本的区别，也是[第 3 期长文](artifacts/marketing/03-工具调用/小红书长文-03-软件MCP与物理世界工具调用.md)专门在讲的事。

## 跑起来

前置：Go 1.26、Python 3.11、Node.js 22、Git、Make，以及已启动的 Docker（含 Compose）。默认引擎为 **Gazebo Harmonic + ROS 2 Jazzy**，首次启动自动构建运行镜像。

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
make setup
make sim-start       # Gazebo home，工作台 http://127.0.0.1:8787/
make home-furnished  # Gazebo 装修家庭，独立端口 http://127.0.0.1:8897/
```

工作台的底盘、头部 RGB-D 均来自 Gazebo 相机。建图通过注册的 mapping 服务运行，导航使用 RTAB-Map / Nav2；地图、日志和 journal 按场景持久化。场景切换、验收命令与当前功能边界见 [Gazebo 默认引擎指南](docs/guides/gazebo-default.md)。**场景加载和关节运动通过，不代表抓取放置已经通过**；以运行时 capability catalogue 和实测报告为准。

开头的杯具转运任务来自旧 MuJoCo 家庭基线，需要显式选择该后端复现：

```bash
SIM_STACK_ENGINE=mujoco make home-furnished
.venv/bin/python scripts/run_home_task_suite.py \
  --base-url http://127.0.0.1:8897 \
  --output artifacts/acceptance/my-household-run-1 \
  --scenario patrol --scenario inspect-kitchen --scenario mug-transfer --timeout 600
```

## 关于 API Key：什么时候需要，什么时候不需要

这里容易产生歧义，所以说清楚：

| 场景 | 要不要 LLM API Key | 说明 |
| --- | --- | --- |
| **仿真演示、跑测试、复现验收** | **不需要** | 默认 `AGENT_PROVIDER=deterministic`，走确定性解析器。开头那句演示指令就是它的固定用例（见 [`agent/intent/home_route_test.go`](agent/intent/home_route_test.go)），完整闭环不依赖任何模型服务 |
| **开发 / 自用：想让系统听懂更多自然表达** | **需要** | 已知意图仍优先走确定性解析，只有其余表达才交给 LLM；**解析失败返回原解析错误，不静默降级**，而规划层另有确定性后备（[编排](docs/architecture/orchestration.md)） |
| **实机部署或对外提供自然语言入口** | **需要** | 否则只能处理解析器已覆盖的句式；不是不能跑，是能听懂的说话方式有限 |

配置方式（本地 Console 的"开发模式 → 开发诊断"，或私有配置文件）：

```text
AGENT_PROVIDER=openai          # OpenAI 兼容接口；默认 deterministic
AGENT_BASE_URL=https://your-provider.example/v1
AGENT_API_KEY=...              # 你的 key，只放在本机
AGENT_MODEL=your-model
```

**关于密钥安全，几个关键约束**（这些是代码里的边界，不是承诺）：

- `AGENT_API_KEY` **只在 Agent 进程内使用**，不会发往机器人，也不会出现在浏览器端状态里（[配置与安全](docs/production/configuration-and-security.md)）。
- 云端机群控制与 Orin NX 单机器人 Agent 使用同一有界 Agent 内核、按角色限制模型与工具；同一多架构 Docker 镜像可在云端运行系统任务 Agent，在 Orin 运行单机 Agent 或 Fleet Worker。各模型调用阶段可独立配置，边缘端可按阶段调用云端只读推理。见 [Harness / Docker 升级规范](docs/superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md)、[Fleet 架构](docs/architecture/fleet-cloud.md) 与 [Orin NX 部署](docs/install/edge-orin.md)。
- 模型只负责**理解与规划**，不接触适配器 SDK、gRPC 消息或任何安全字段，也不授予硬件动作权限——它不碰关节角、轮速和坐标。
- 请把 key 放进**私有配置文件**：`*.env` 已在 [`.gitignore`](.gitignore) 中全局忽略（包括 `artifacts/` 下生成的环境文件），只有 `*.env.example` 占位模板会被跟踪。
- **本仓库不含任何密钥**，历史提交里也没有。提交前建议自查一次：`git diff --cached | grep -iE 'sk-|api[_-]?key'`。

## 一、闭环契约：返回成功不算完成

`mutates_world=True` 的工具（导航、抓取、放置、恢复位姿）返回成功后，系统会把它推进一个独立状态——**待取证**。物理结果此刻仍然未知，必须附上一条**命令之后采集、带观测标识、来源没过期**的新鲜证据，才允许记为完成。

判定与失败分类在 [`core/closedloop`](core/closedloop/closedloop.go)：证据缺 ID、缺采集时间、早于命令下发、或来源判定为 `STALE`，一律拒绝。失败不是笼统重试，而是分成八类（Transient / Perception / Planning / Permission / Resource / Validation / UnknownOutcome / Fatal），每一类唯一安全的下一步都不同；其中**「结果未知」禁止自动重试**，必须先对账。

### 可选的物理接地验证（GVF）

新增 GCL 合约与独立边缘验证器，默认关闭。开启后，抓取、放置、导航的工具回执还需通过传感器证据校验；缺证据返回 `UNKNOWN` 并持久阻断后续物理动作。报告、中文模板和原始证据引用可以回放。

```bash
make gvf-demo       # Gazebo 接触夹具：工具 SUCCESS，实际漏夹
make gvf-experiment # 30 个任务模板 × 5 个种子，生成对照报告
```

需要已安装项目依赖、Docker 和 `tangying-navigation:dev` 镜像。详细开关、合约扩展和复现方式见[单机器人闭环指南](docs/development/single-robot-loop.md#物理接地验证-gvf可选)。[实验报告](docs/experiments/2026-09-21-grounded-verification.md)区分实测验证结果与离线恢复估计；没有模型响应的 LLM 对照不计作已完成。

## 二、分布式：不是把程序拆到多台机器上

「分布式」在这套系统里不是部署姿势，是**故障假设**。它意味着四件具体的事：

| 设计 | 解决的问题 |
| --- | --- |
| **单写协调 + 资源归属** | 多个写者不会同时改同一个物理世界（地图、位姿、谁抓着什么、充电位） |
| **租约 + 单调递增的 fencing token** | 旧持有者拿着过期授权写入，会被直接拒绝——脑裂挡在数据层，不靠人盯 |
| **事件与 Outbox + 幂等键** | 先落盘事实再投递；进程重启/队列宕机后能接着做，而不是从头再来 |
| **云端慢环 + 边缘快环** | 实时控制环与安全监督留在机器人上，规划与对账在控制面；**断网安全停机，恢复后对账** |

两种形态共享同一套 Runtime、工具目录、观测契约与世界快照格式：

```text
云端 Fleet（扩展路线）                     本地 Local Brain（当前主线）
浏览器 ── Fleet API ── 任务/协调器          浏览器 ── Local Agent + SQLite
              ── EventLog + Outbox                        │
              ── WorldHub + Harness                       │
                    │ mTLS                                │
              Edge ── Robot Runtime              同一 Robot Runtime
                    │                                    │
              实机 / 仿真工具                       实机 / Gazebo / MuJoCo 仿真
```

**换机器人不用重写任务与工具**：新本体只需实现 `RobotProfile → PluginBackend → RobotRuntimeService`，审批、租约、日志、取消、急停全部复用。

> 说清楚边界：当前**主线是一台机器人**在受限环境把活干完；上面右侧的云端 Fleet 是仓库里**已经实现**的扩展路线，不等于"单机开发栈已经具备跨地域生产级高可用"。恢复能力限于单主进程重启范围内的一部分状态恢复，跨主机共识与跨存储事务仍在待验证清单上。

## 三、模型能做什么、不能做什么

| 工具层 | 内容 |
| --- | --- |
| 暴露给大模型 | 28 个工具，覆盖感知、导航、抓取、放置与三个验证工具；有 5 级安全标注，会改变世界的必须声明为副作用 |
| **不暴露给大模型** | `move_arm_to_joints`（关节角）、`navigate_to_pose`（位姿）——原始接口只留给工程调试 |
| 执行通道 | 所有工具走同一条 `ExecuteSkill` 通道与安全监督；模型不接触关节角、轮速或坐标 |
| 外部接入 | [MCP 桥接](robot/mcp/README.md) 只给 8 个粗粒度工具，不提供批准、不提供自动批准、不暴露关节控制；外部 Agent 建的任务一样要人工审批 |

## 四、已经验证了什么

| 能力 | 状态 | 怎么验证 |
| --- | --- | --- |
| 家居自然语言拆解（客厅/厨房、拿取、放置） | ✅ 已验证 | 实测：`observe → pre_position → navigate → verify_arrival → observe_scene → resolve_targets → plan_grasp → pick → verify_grasp → place → verify_placement` 全部闭合，12 条观测证据；**返程那一步例外，见下** |
| 返程导航（回到出发房间） | ⚠️ 受地图覆盖限制 | 参考地图上返程点未认证可通行，`navigation.navigate` 以 `GOAL_NOT_CLEAR` 结束。这是"不知道的地方不进去"，不是缺陷，但**在这一版上返程确实没走通** |
| 底盘导航 | ✅ 已验证 | `navigation.navigate` + `verify_arrival` |
| 机械臂抓取与放置 | ✅ 已验证 | `manipulation.pick` / `manipulation.place` + 两个 `verify_*` |
| 相机感知（头部 + 底盘双 RGB-D、深度、点云） | ✅ 已验证 | 工作台"机器人视野"，以及每步绑定的采集 |
| 完成后确认（闭环契约） | ✅ 已验证 | 写工具缺新鲜证据即失败关闭 |
| 整机标定与自行录入 | ✅ 已接通注册服务 | 工作台"去处理"→标定；支持算法结果录入、版本校验和应用 |
| RGB-D 稠密 SLAM、保存和 WebGL 展示 | ✅ 已接通注册服务 | [标定与建图操作指南](docs/guides/robot-service-workflow.md) |
| 保存关键帧的图像、位姿与配准诊断 | ✅ 已验证 | [关键帧检查指南](docs/guides/slam-keyframe-inspection.md) |
| 安全工具（急停、恢复安全位姿、限速） | ✅ 已注册 | `emergency_stop`、`recover_to_safe_pose`、`set_speed_limit` |
| RTAB-Map + Nav2 | ⚠️ 可运行，需先探索建图 | [真实 SLAM 建图与 Nav2](#五真实-slam-建图与-nav2) |
| 实机（XLeRobot） | ⚠️ 需现场验收 | [真机路径](#六真机sim2real) |

一条指令的完整链路（每一步的回执都绑定该步的 RGB-D 采集）：

| # | 步骤 | 工具 | 这一步在证明什么 |
| --- | --- | --- | --- |
| 1 | 看一眼现场 | `observe_scene` | 当前能看到哪些物体与房间 |
| 2 | 走到厨房 | `navigation.navigate` | 底盘真的移动了 |
| 3 | 确认到达 | `verify_arrival` | 用**新的**底盘相机与位姿误差确认，不是相信回执 |
| 4 | 重新观察 | `observe_scene` | 到达后重新感知，不复用出发前的画面 |
| 5 | 解析目标 | `resolve_targets` | 陶瓷杯与收纳盘在场景中的身份 |
| 6 | 规划抓取 | `plan_grasp` | 抓取位姿可行性 |
| 7 | 拿起 | `manipulation.pick` | 物理动作 |
| 8 | 确认拿起 | `verify_grasp` | 物体确实离桌并在夹爪中 |
| 9 | 放下 | `manipulation.place` | 物理动作 |
| 10 | 确认放好 | `verify_placement` | 连续三帧稳定处于 `inside:kitchen-tray` |
| 11 | 回到客厅 | `navigation.navigate` | 底盘再次移动 |
| 12 | 确认回到客厅 | `verify_arrival` | 同第 3 步的判据 |

## 五、真实 SLAM 建图与 Nav2

默认工作台通过机器人注册的 `calibration.*`、`mapping.*` 和 `navigation.map` 服务运行。OS 不按仿真或实机选择实现。当前参考驱动支持实际移动采集、平面 RGB-D ICP/位姿图 SLAM、不可变地图保存、栅格路径规划和在线碰撞检查。完整步骤见[标定与建图操作指南](docs/guides/robot-service-workflow.md)。

最新[装修家庭验收记录](docs/development/2026-09-13-furnished-home-acceptance.md)记录新场景、实际移动扫描、关键帧浏览、任务证据及失败修复。此前的[注册服务验收记录](docs/development/2026-09-13-robot-workflow-acceptance.md)保留为历史基线。

需要 RTAB-Map 与 Nav2 实现时，可启动独立导航服务（需要 Docker）：

```bash
make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'
make navigation-status
```

`navigation-status` 就绪时会打印 `ready: true`、`mode: mapping`、`mapRevision`（地图身份）与 `poseSource: rtabmap_tf`。地图持久化在容器卷 `/data/maps/home/rtabmap.db`。

**建图阶段的行为是刻意设计的安全门禁**：全局代价地图的 `allow_unknown=false`，Nav2 拒绝规划穿过尚未建图的区域。所以冷启动后只能导航到已覆盖范围（例如客厅），去厨房会以 `NAV_FAILED NAV2_ACTION_ENDED` 结束——这不是缺陷，而是"不知道的地方不进去"。因此正确顺序是：

1. **建图**：`--mode mapping` 下低速探索覆盖五个房间，确认视觉词典、占据图与定位都在更新；
2. **执行**：`--mode localization --scene home` 复用同一张地图，再运行自然语言路线；
3. **抓取**：需要厨房物体的任务用 `--scene home_task`，它在此基础上增加可见的红色杯子与蓝色收纳盒。

工作台支持手动有界移动扫描，以及驱动注册的巡航路线扫描。后者使用已配置路线，尚未实现未知住宅的自主 frontier 探索。RTAB-Map 操作详见[家庭场景操作](docs/guides/home-scene-operations.md)。

## 六、真机（Sim2Real）

同一套任务、工具与证据合同在实机上不变，替换的是适配器、感知、标定与现场安全配置：

```bash
./install.sh robot-pi --dry-run --yes     # 先预览安装计划
./install.sh robot-pi                     # 树莓派 / 机器人上位机
robot-agent doctor robot-pi               # 上电前离线检查
```

真机放行前必须完成：双 RGB-D 与里程计标定、地图覆盖、刹车与实体急停、机械臂碰撞边界、以及至少 30 次受监护的路线/抓取验收。逐步清单见[家庭 Sim2Real](docs/guides/home-sim2real.md)与[发布检查清单](docs/operations/release-checklist.md)。

**软件发布与实机放行是两项独立结论**——仿真通过不等于实机可用，这是刻意的。

## 七、日常使用

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

停止与查看仿真栈：

```bash
bash scripts/sim-stack.sh status --artifacts-dir artifacts/sim-stack/furnished-home
bash scripts/sim-stack.sh logs   --artifacts-dir artifacts/sim-stack/furnished-home
bash scripts/sim-stack.sh stop   --artifacts-dir artifacts/sim-stack/furnished-home
```

验收输出目录必须是新目录；脚本不会自动重试物理操作或重置场景。杯子收纳需要初始未放置状态，重复测试前应完成或停止当前任务，再显式重启场景并确认地图有效。旧 `make home-start` / `make home-accept` 仍保留为彩色杯兼容基线，使用独立的默认运行目录和 8787 端口。

## 八、仓库里有什么

代码按运行位置分类，权威说明见[部署目标与代码归属](docs/operations/deployment.md)：

| 目标 | 内容 | 入口 |
| --- | --- | --- |
| 本地单机 | Local Agent、工作台、任务账本（**当前主线**） | `./install.sh local` |
| 机器人端 | ROS 2 网关与安全监督、xlerobot 驱动 | `./install.sh robot-pi` |
| 云端 | Fleet 控制面（**已实现，扩展路线**） | `./scripts/fleet-up.sh up` |
| 仿真 | 默认 Gazebo / ROS 2，MuJoCo 保留回归 | `./install.sh sim`，然后 `make sim-start` |

共享运行时代码：`agent/`、`core/`、`orchestration/`、`tasks/`、`skills/`、`middleware/`；分布式控制面与边缘运行时在 `fleet/`、`edge/`（11.7k 行生产代码）；家居场景在 `sim/mujoco/`，工具层在 `robot/gateway/`，前端在 `web/`。

## 九、开发与验证

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

第一次来？走一遍[新人第一小时](docs/README.md#新人第一小时)，里面有"每类东西放在哪个目录"的[仓库地图](docs/README.md#仓库地图每类东西放在哪)。

文档入口：[完整文档索引](docs/README.md) · [装修家庭演示](docs/guides/furnished-home-demo.md) · [RGB-D 闭环原理](docs/development/single-robot-loop.md) · [机器人工具层](docs/development/robot-tool-layer.md) · [分支与发布规范](docs/development/branching.md)。本版变更见 [Changelog](CHANGELOG.md)，发布身份见 [v0.7.0 发布记录](docs/releases/v0.7.0.md)。架构评估、工具安全修复、地图/工作区规划与真实验收边界见[系统审查与升级记录](docs/development/2026-09-13-system-audit.md)。

**想系统读懂这套系统？** [`book/`](book/README.md) 是一本基于本仓库写成的技术专著《深入理解分布式机器人 Agent 系统》：16 章 + 3 附录，从物理约束推导架构、逐层拆解闭环契约与证据门禁、给出从零搭建的施工图，并有[与通用 coding agent 的二十条对照](book/chapters/ch16-coding-agent-contrast.md)。全书技术断言可回溯到 `文件:行号`，研究笔记留在 [`book/research/`](book/research/) 供核对。

## 十、这个仓库故意不做什么

一个项目的取舍比功能列表更能说明它的判断。以下都是**刻意的非目标**，写在[完整架构文档](docs/production/architecture.md)里：

- **不用软件急停替代实体急停。** 软件状态永远低于硬件限制。
- **不把仿真模型当作未经标定的实机真值。**
- **不在观测陈旧、资源冲突或证据缺失时猜测成功**——宁可失败关闭。
- **不承诺单机开发栈已经具备跨地域生产级高可用**（那属于 Fleet 的待验证清单）。
- **不让大模型输出关节角。** 物理动作的粒度是任务，不是电机。

## 其他路线（保留但暂不聚焦）

以下能力保留且仍有测试，只是当前不投入：

- **多机器人 / Fleet**：`cmd/fleet-control-plane`、`fleet/`、`edge/`。单机器人把自己的任务做完即可；多机协调属于调度层，不影响上面的闭环。
- **RoboCasa 双机交接**：`./scripts/fleet-sim.sh handoff`；Compose 云端流程为 `./scripts/fleet-up.sh up`。旧 `./install.sh cloud` 只返回迁移提示。
- **固定工位桌面任务**：`make rgbd-start`（`--scene tabletop`）。

架构决策见 [World/Harness 设计](docs/superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md)与[早期 Local-first 设计](docs/superpowers/specs/2026-08-18-local-first-runtime-design.md)。

## 关于这个项目与作者

我把它当成一套**写给物理世界的操作系统**在做，而不是一个 Demo：所以有闭环契约、有证据链、有 fencing token、有失败分类，也有[公开的"仍须独立验证"清单](docs/architecture/distributed-agentos.md)。

系列文章（按主题逐期讲清一个设计决策）都在 [`artifacts/marketing/`](artifacts/marketing/README.md)：

| 期 | 主题 |
| --- | --- |
| 01 | [总体架构](artifacts/marketing/01-总体架构/小红书长文-01-总体架构.md) |
| 02 | [为什么要分布式](artifacts/marketing/02-为什么要分布式/小红书长文-02-为什么要分布式.md) |
| 03 | [软件 MCP 与物理世界工具调用](artifacts/marketing/03-工具调用/小红书长文-03-软件MCP与物理世界工具调用.md) |

**如果这套设计对你正在做的事有用，欢迎 star、提 issue，或者直接来聊。** 我在找具身智能 / 机器人软件方向的工作（机器人 Agent 与系统、SLAM 与导航），缺人的话求内推 🙏

MIT License · 文档 144 篇 · 测试 1079 个 Go 测试函数 + 1670 个 Python 测试函数

### Agent 上下文评测

任务、编排、工具结果和恢复轨迹可投影为带来源与有效期的自然语言事实包，支持 JSON 和两种自然语言渲染。默认关闭，`TANGYING_AGENT_CONTEXT=json` 启用本轮开发集选中的表达。`make test-agent-context` 验证接入，`make eval-agent-context-replay` 离线重放真实模型响应。参见 [使用与训练接口](docs/development/natural-language-evaluation.md#agent-全程上下文与能力评测2026-09-21) 和 [量化实验报告](docs/experiments/2026-09-21-agent-context-evaluation.md)。

第二轮已支持 **按关键环节选择表达**：`TANGYING_AGENT_CONTEXT=stage` 使用经开发选择和留出门禁的内置策略；工具结果/反思、恢复、诊断/交接等环节可以使用不同格式。运行 `make test-agent-stages`、`make eval-agent-stages-replay`；实验对比与退化案例记录在同一报告第 12 节。

第三轮补充 **两模型因子实验、反事实字段必要性证明、形式化检查、结构与决策契约消融**。`make test-agent-factorial` 与 `make eval-agent-factorial-replay` 可离线验收；逐环节结果和未通过项见报告第 13 节，完整字段设计见 [生产者字段字典](docs/development/decision-context-fields.md)。研究模式默认关闭；最终模型绑定配置会拒绝已观测到危险建议或零完整成功的环节，不把相对基线持平当作可上线。
