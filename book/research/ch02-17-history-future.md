# 《深入理解分布式机器人 Agent 系统》第 2 章、第 17 章核心素材

**考古对象**：`SUSTechWLA/tangying-robot-agent-os`（躺营 Tangying Robot AgentOS）
**考古时间**：2026-09-21
**取证快照**：工作树 `HEAD = 8b9683be8`（`feat: prepare v0.7.0 grounded runtime and system evaluation`，2026-09-21）。

> **两条必须先说清的取证前提，否则后文的行号无法复现：**
>
> 1. **本仓库在本次考古期间发布并提交了 v0.7.0。** 任务书写的是「v0.6.0」，但工作树的 `VERSION` 已是 `0.7.0`，且 HEAD 已提交 `docs/releases/v0.7.0.md`。本文件如实按 **v0.7.0 是当前版本**处理，并把 v0.7.0 纳入时代划分（阶段 G，全表 A–G 共 7 个阶段）。
> 2. **`CHANGELOG.md` 在提交 `8b9683be8` 中被收敛过。** 提交 `774bd2a2f`（v0.6.0 之后、v0.7.0 之前）的 `CHANGELOG.md` 有 **3,282 行**，是逐项技术叙事；HEAD 的 `CHANGELOG.md` 只有 **200 行**，是版本摘要。因此本文件的 CHANGELOG 引用一律写成 **`CHANGELOG.md@774bd2a2f:<行号>`**（该提交长期可复现），只有引用当前 200 行摘要时才写 `CHANGELOG.md:<行号>`。核对命令：
>
> ```bash
> git show 774bd2a2f:CHANGELOG.md | wc -l     # 3282
> wc -l CHANGELOG.md                           # 200
> git ls-tree --name-only HEAD docs/releases/  # 含 v0.7.0.md
> cat VERSION                                  # 0.7.0
> ```

---

# 第一部分 · 演进史（第 2 章）

## 1. 时间轴

全仓库 **327 个提交**、跨 **2026-08-17 → 2026-09-19**（34 个自然日），共 9 个发布标签（`v0.1.0-rc.1`、`v0.1.0-rc.2`、`v0.2.0`、`v0.3.0`、`v0.4.0`、`v0.5.0`、`v0.6.0`，另有两个 `archive/*` 归档标签）。

> **一个取证细节**：截至本文件写作时，**`v0.7.0` 标签尚未创建**（`git tag -l 'v0.7*'` 为空），但 `VERSION` 已是 `0.7.0`，`docs/releases/v0.7.0.md` 已随提交 `8b9683be8` 入库。即 **v0.7.0 处于「已准备、未打标」状态**。引用它时请注明这一点，不要写成已发布。

取数命令：

```bash
git log --oneline | wc -l                     # 327
git log --format='%ad' --date=short | sort | uniq -c   # 逐日提交量
git log --tags --simplify-by-decoration --format='%h|%ad|%d' --date=iso
```

逐日提交量本身就是一个强烈的信号，值得写进书里：

| 日期 | 提交数 | 当日性质 |
| --- | --- | --- |
| 08-17 | 24 | 从 0 到 v0.1 rc |
| 08-18 | 17 | local-first + 分层中间件两次重构 |
| 08-19 | 33 | XLeRobot 可观测仿真训练，当日 05:09 收工 |
| **08-20** | **2** | 只有两笔：世界模型 harness 的设计 + 计划 |
| 08-21 | 13 | RoboCasa 双机 + WebGL 数字孪生 |
| 08-22 | 44 | 全天最密（含 12 笔 RoboCasa 验收信任边界修复） |
| 08-23 | 19 | 版本化任务体验 + 生产手册 + CI 收口 |
| 08-24 | 25 | Policy sidecar, v0.2.0-rc.2 |
| **08-25 → 09-05** | **0** | 空档 12 天 |
| 09-06 → 09-13 | 1/1/3/10/4/7/33/23 | 单机器人主线重启期 |
| 09-14 → 09-19 | 11/16/10/19/11/1 | 运维/评测/恢复期 |

那个 **08-20 只有 2 笔提交**的日子和 **08-25→09-05 的 12 天空档**，是理解这个项目节奏的钥匙：前者是全仓库唯一一次「先写完整设计文档再动手」（`2026-08-20-distributed-agentos-world-harness-design.md`，21.5 KB），后者对应一次方向切换（云端 Fleet 叙事退场、单机器人主线接管，09-06 `c1cf62ccd` 重新开工）。

### 阶段 A · 2026-08-17：一天之内从空仓库到 v0.1（24 提交）

**核心问题**：验证「语言模型能不能驱动一台真实/仿真机器人干活」这个想法本身。

**关键决策（全部写进了提交里）**：

- `ed20bd310 chore: initialize robot agent repository` → `e0c801c66 feat: define robot and control plane protocols` → `0bae97a8d feat: add cloud task control plane`：**第一天就是分布式**。云控制面、Local Agent、机器人网关三个角色在同一天内一起出现，没有任何「先写单机版再拆」的中间态。
- `225463914 feat: add raspberry pi robot gateway and safety supervisor`：安全监督从一开始就在**独立进程**里，不在 Agent 进程里。
- `b872c686f feat: add fail-closed xlerobot adapter`：命名里第一个出现的形容词是 **fail-closed**。
- `f885b141e release: complete robot agent v0.1 simulation baseline`（17:28），两小时后打 `v0.1.0-rc.1`。

**文档**：`docs/superpowers/specs/2026-08-17-role-based-installation-design.md`（12.4 KB）。
这份文档的价值不在安装脚本，而在它**先把非目标写死了**：

> 「There is no STM32 installer.」（§8）
> 「It cannot safely automate mechanical assembly, servo ID assignment, 12 V wiring, calibration, physical emergency-stop installation, or the first motion test.」

以及 §12 的发布边界：「it is not a claim of unattended hardware commissioning」——**这个项目从第一天起就把「软件发布」和「实机放行」当作两项独立结论**，之后每一版发布记录都重复这一条。

**产出模块**：`proto/robot/v1`、`core/taskgraph`、`core/skills`、`robot/gateway`（Python）、`sim/mujoco`、`deploy/`、`install.sh`、`cmd/robot-agent`。

### 阶段 B · 2026-08-18：两次架构重构（17 提交）

这是全仓库**唯一一天做了两次架构级重写**。两次都由一份设计文档驱动：

**B1 `2026-08-18-local-first-runtime-design.md`（19.4 KB）→ 提交 `4cc5cf56a` 设计 / `8cdebd64e` 计划 / `61c79a72c` 完成。**
决策：**删掉云控制面**。`0cbfdc562 refactor: simplify deployment to local first` 一次性移除 `cmd/cloud-control-plane`、PostgreSQL 存储、Compose 栈与 `cloud` 安装角色。文档 §3 的 Non-Goals 明确列出被删范围，§13 甚至规定了**哪些代码要被复用而不是丢弃**（「The current uncommitted v1 work is treated as source material, not discarded wholesale」）——这是一次有考古自觉的重构。

**B2 `2026-08-18-layered-runtime-middleware-design.md`（13.9 KB）→ 提交 `1429295ab` 设计 / `db5e58518` 中间件契约 / `547d8570c` 定稿。**
决策：引入 **ports and adapters**，把 SQLite 从 `edge/localstore` 移到 `middleware/sqlite`，把 protobuf 从 Python 后端里赶出去（`32033057f refactor: decouple robot runtime from protobuf`）。
这份文档 §2 罕见地**逐条列出了现有代码的层违规**（7 条），§9 规定用 `go list -json` **机械检查**依赖方向，§12 还写了一条元规则：

> 「Future middleware adapters or Robot Runtime protocol changes must update the current architecture and link to a new decision record rather than erasing prior design history.」

**留下的不变量**：`tests/architecture/dependencies_test.go`，以及今天仍然生效的 `middleware/` 三个包（`memory`、`sqlite`，加根契约包）。B2 §11 的验收条件还额外规定了一条**高速数据不出机器人**的边界（同批改动的 CHANGELOG 条目原文：「keeping **high-rate camera/LiDAR/IMU/joint data robot-local**」，`CHANGELOG.md@774bd2a2f:156`）。

### 阶段 C · 2026-08-19 → 08-22：仿真、世界模型与数字孪生（117 提交）

**核心问题**：从「能跑通」升级到「能被观测、能被验证、能被证明」。

- **08-19（33 提交）可观测 XLeRobot 仿真与语义工具训练**，设计见 `2026-08-19-xlerobot-observable-sim-training-design.md`。当日 01:07 → 05:09 的提交序列几乎全是 `fix:`，且**每一条 fix 都对应一个仿真特有的失效**：`01fa6e955 fix: stabilize xlerobot task scene`、`720d0a192 fix: keep xlerobot task goals within single-arm reach`、`9e163d8f0 fix: approach grasp targets before attachment`、`5f8c649b2 fix: serialize rendering and placement commits`、`d505f5a2e fix: fail closed on stale scene frames`。这段时间确立了一条后来反复复用的判据：**仿真动作不能冒充已训练物理策略**（文档 §3 Excluded）。
- **08-20（2 提交）分布式世界模型与 Harness 设计**，`2026-08-20-distributed-agentos-world-harness-design.md`（21.5 KB，全仓库最长的一份设计文档，中文）。这是**整本书第 2 章最该细读的一份文档**：它给出了 `world.observation.v1` / `world.snapshot.v1` 的完整字段、写工具与观测适配器分离（Tool Registry vs Observation Registry）、单写者协调 + leader lease + fencing epoch、九类故障注入矩阵、以及 §2.3 一段极罕见的**自我否定**：

  > 「『云端管理多机器人』本身不是足够独特的创新声明。」
  > 「是否属于学术或专利意义上的『巨大原创创新』，必须在实现完成后结合最新 cloud robotics、multi-robot task allocation、robot world model 和 agent harness 工作做正式检索与对比，**不能仅凭工程完成度宣称**。」

- **08-21 → 08-22（57 提交）RoboCasa 双机 + WebGL 数字孪生**。两份设计文档：`2026-08-21-robocasa-dual-xlerobot-harness-design.md`、`2026-08-21-robocasa-webgl-digital-twin-design.md`。08-22 的 12 笔连续 `fix` 提交（`3bcb4da90` → `2f88ad74d`）全部集中在**验收证据的信任边界**上：fd 所有权、路径完整性、原子读、竞态、不安全条目拒绝。

**留下的文档**：`docs/architecture/` 全树（`agent-events.md`、`orchestration.md`、`protocols.md`、`multi-robot.md`、`middleware.md`）+ `docs/production/`（11 篇手册，`fa1ef13b9 docs: deliver Robot AgentOS production manuals`）。

### 阶段 D · 2026-08-23 → 09-05：版本化任务体验、生产化，与 12 天沉默

- `2026-08-22-versioned-task-experience-design.md` → 提交 `e13a4aeda`（不可变 TaskRevision）→ `785ca5a0e`（原子持久化）→ `e09065292`（安全点对账）→ `70ce8feaa`（运行中更新）。这是**唯一一份把 UI 体验、领域模型、分布式一致性放在同一份文档里**的设计：它规定「确认更新永不改写旧 revision」，以及 `PROPOSED / WAITING_APPROVAL / WAITING_SAFE_POINT / ACTIVE / SUPERSEDED / REJECTED` 六个状态。
- `2026-08-24-policy-tools-recovery-sim2real-design.md` → `efbf6a543`（契约）→ `1208d7441`（Provider）→ `c00c6c316`（Edge 侧准备）→ `78813871b`（框架无关 sidecar）。确立了 **policy sidecar 没有 Fleet 凭据、不能完成 intent** 的三段信任边界。
- `d1127eaea chore: prepare v0.2.0-rc.1`（08-23 08:36）→ 之后整整一天全是 **CI 可移植性修复**（`c75efc332`、`7e62a7499`、`84fe0862e`、`696791d12 ci: use egl for headless mujoco rendering`、`ec147ad8c ci: use osmesa on cpu runners`）。这段历史的教育价值很高：**让测试在 CI 上真的跑起来，花的时间比写测试本身还多**。
- **08-25 → 09-05 空档 12 天**（0 提交）。产出是 `docs/production/v1-assessment-2026-09-05.md`——一份**从外部视角自我审查**的文档，包含 6 条实机上线阻塞与「未执行项」清单，以及一条方法论：

  > 「专项结果是同一批代码的补充证据，不应与整套测试数量相加来夸大覆盖率。」（`docs/production/v1-assessment-2026-09-05.md:89`）

### 阶段 E · 2026-09-06 → 09-13：单机器人主线、工具层、五连发（92 提交）

这段时间的密度和方向都很特殊：**8 天内打了 5 个版本标签**（v0.2.0 09-09、v0.3.0/v0.4.0/v0.5.0 均在 09-11、v0.6.0 09-13）。

- `c1cf62ccd feat: harden V1 robot agent and refresh console and documentation`（09-06 22:32）：**方向切换的那一笔**。云端叙事退场，单机器人 V1 主线接管。
- `7332976a8 feat: add heterogeneous robot adapters and unified MCP tools`（09-07）← `2026-09-06-heterogeneous-robots-design.md`
- `2e841a9c3 feat: add RGB-D single-robot loop and durable task recovery`（09-08）← `2026-09-08-single-robot-rgbd-design.md`
- `f4dbd307a feat: integrate dual RGB-D RTAB-Map navigation and stable console views`（09-08）
- `6a0349d33 feat: release v0.2 verified single-robot navigation loop`（09-09 03:13）
- `ab6936d83 feat: add household mobile manipulation harness`（09-10）← `2026-09-10-home-mobile-manipulation.md`
- `ccfdbeedd refactor: classify the repository by deployment target`（09-11）：**把「代码在哪一层」和「代码装到哪台机器」正式拆成两件事**，产出今天的 `docs/operations/deployment.md` + `tests/deploy/test_deployment_layout.py`。
- `cddb5bb46 chore: ship decision records, not agent scratch and plans`（09-12）：删除 `.superpowers/` 下 8 份 agent 会话报告与两份无人引用的产物；`docs/superpowers/` **只保留 `specs/`**。提交信息里那句「plans 是过程产物，不是产品的文档」是一条可迁移的仓库治理经验。
- `895bfc3fe feat: complete registered robot calibration, SLAM and manipulation workflows`（09-13 12:41）← `2026-09-13-general-robot-system-design.md`
- `c7e7ab530 feat: furnish household tasks and expose SLAM keyframe diagnostics`（09-13 17:19）
- `e901b6f35 chore: release v0.6.0`（09-13 20:05）

**这段时间产出的两份 ADR 是全书最有价值的两份**：`2026-09-10-closed-loop-semantic-upgrade-adr.md`（26.5 KB）与 `2026-09-11-standard-robot-tool-layer-adr.md`（9.1 KB）。详见下文「关键转折点」。

### 阶段 F · 2026-09-14 → 09-19：从「能跑」到「能证明自己跑对了」（67 提交）

主题词从「功能」变成了「运维、评测、恢复、诚实」。

- `e28f1205a SLAM 探索：门不是瓶颈，让机器人真的能扫完一整个家（38.9% → 81%）`（09-15）
- `ee7c0a2f0 机器人异常处理审计：逐项验证行为/恢复/入表，并修掉"故障丢弃整张地图"`（09-15）
- `5b0d857b6 AI 回溯诊断：incident.v1 记录 + 确定性故障族分类`（09-16）
- `88cf9f7ce Agent 层升级为可扩展多 Agent 运行时（当前启用 task + ops）`（09-17）
- `6b9c9550e 恢复 Agent：只提议不执行的第三类 Agent + 修掉四个只有真机才暴露的缺陷`（09-17）
- `1f6b72e27 接入模型：自然语言任务分解走通；修掉两个永远不成立的检查`（09-18）
- `505af6577 编排层评测体系：先有刻度，再谈自训`（09-18）
- `0774468f1 问题列表从 279 条报告变成 7 个问题；修掉三处身份缺陷`（09-18）
- `f9f50febd 新增 docs/architecture/why-distributed.md`（09-18）
- `774bd2a2f 找到控制台卡死的真凶：轮询比端点响应还快`（09-19）

**文档产出**：`docs/architecture/why-distributed.md`（10.1 KB）是**全书最好的「设计辩护」样本**：它 §0 直接说「『分布式』不是这个系统的价值所在」，§1.3 说「一台机器人 + 一个人看着 + 非安全关键 → 三层是过度设计」，§8 列出「这份文档没证明的事」（没有性能基准、没有多机实测、没有对照实现）。

### 阶段 G · 2026-09-21：v0.7.0 与评测底座（单提交 `8b9683be8`）

HEAD 提交一次性纳入：Grounded Contract Language（可选物理接地验证 + 三值逻辑）、Agent 决策上下文分阶段实验、`orchestration/eval/system_eval` 离线评测底座（20 项能力 / 7 层 / 12 类 29 工具 / 34 指标）、Gazebo 运行时与建图/标定工作流。版本说明见 `docs/releases/v0.7.0.md`，设计见 `docs/architecture/agent-evaluation-system.md`（42.2 KB，全仓库最长的架构文档）。

这份文档 §12.3 给出了一张**「已实现 / 已设计未实现」分界表**（`docs/architecture/agent-evaluation-system.md:417-430`），是第 17 章最直接的素材来源。

---

## 2. 关键架构转折点

以下四处都是**真实的岔路口**：当时的文档里留下了被否决方案与理由，且今天仍能验证后果。

### 转折点一：不做「先单机再重构」，第一天就写分布式

**如果是另一条路**：会有一次「单机 → 分布式」的大爆炸式重写，`edge/runtime` 的语义命令边界、`world.snapshot.v1`、fencing token 都会是补丁。

**原始论证**：`2026-08-17-role-based-installation-design.md` §2 定义了 4 个安装角色（`sim`/`cloud`/`local`/`robot-pi`），§3 规定「root `install.sh` is the only bootstrap entry point」；`2026-08-18-layered-runtime-middleware-design.md` §2 逐条列出既有代码的层违规 —— 也就是说，**它是在已经写成分布式之后立刻做的边界收紧，而不是先单机再拆**。

**取舍代价**：这个选择直接导致了 08-18 的两次重构（一天 17 提交）。作者接受了这个代价，理由是 §11 的验收条件：「Agent/task/runtime core packages compile without importing any concrete middleware adapter or vendor SDK.」

**验证**：31 天后 `docs/architecture/why-distributed.md` §1 给出了事后总结：「分层是物理强加的，不是架构偏好」——控制回路 100 Hz–1 kHz、急停毫秒级、感知带宽数十 MB/s 三条硬约束。**但同一文档 §1.3 也承认反面成立**：「一台机器人 + 一个人看着 + 非安全关键 → 三层是过度设计。」

### 转折点二：local-first 成为主线，cloud fleet 降为扩展路线

**如果是另一条路**：云端多租户是主形态，`cloud` 安装角色保留，Local Brain 是「离线降级模式」。

**原始论证**：`2026-08-18-local-first-runtime-design.md` §3 Non-Goals 明确写「Multi-tenant cloud accounts, hosted task history, remote fleet management, and public API exposure are not part of the first local-first release」，§13 给出删除清单（`cmd/cloud-control-plane`、PostgreSQL schema、Compose、`edge/cloudclient`、claim/lease 循环）。§1 的理由是一句产品判断：「a user can install on one laptop, configure with an OpenAI-compatible LLM API, pair with one Raspberry Pi robot, and operate without a hosted control plane or deployment middleware.」

**注意这里有一个后来被推翻的细节**：`2026-08-20-distributed-agentos-world-harness-design.md` §2.2 明确批评现状「文档同时保留『本地优先、云端退出默认产品』和『Fleet 云端』两套产品叙述」，并把**云端重新定为重点部署**。而 09-06 之后单机器人主线又回来了。**这是一次完整的方向往返**：local-first（08-18）→ cloud-first（08-20）→ local 主线 + fleet 扩展路线（09-06 起，直到今天的 README）。

**今天的状态**：README §二 用一张表把两者并列，并明确标注「云端 Fleet（扩展路线）/ 本地 Local Brain（当前主线）」，同时给出边界声明：「不等于『单机开发栈已经具备跨地域生产级高可用』」。

**取舍代价**：`docs/architecture/distributed-agentos.md` 开头就写着「这是部署替换边界，**不是已经实现的云端/本地自动故障切换**」。

### 转折点三：工具层要标准化，而且**不新增执行通道**

**如果是另一条路**：为 LLM 直接暴露关节角与底盘坐标（最省事），或者新建一条「LLM 专用」执行总线（最"干净"）。

**原始论证**：`2026-09-11-standard-robot-tool-layer-adr.md` 是一份**先审计、后决策**的 ADR。它的背景章节先给出「审计到的既有事实」表（8 行），结论一句话：

> 「**不存在需要『另起炉灶』的缺口。**」（`2026-09-11-standard-robot-tool-layer-adr.md`，背景章节末）

然后 ADR-1 规定领域工具**编译成既有的 `ExecuteSkill`**，理由是：「新开一条总线会立刻产生两份租约、两份幂等和两份安全判定」。

ADR-2 是全书最值得引用的一个设计决定：**能力集（LLM 可见）与动作集（适配器可见）分离**，翻译发生在 gateway 层而不是适配器层，理由是「若把翻译放进适配器，每个型号适配器都要重做一遍语义解析并各自犯错；放在 gateway 侧可以单点测试、单点限制」。

ADR-5 是另一种诚实：**明确不宣称已对接 MoveIt 2 / Gazebo**，理由是可验证性（「仓库的 ROS 2 镜像只安装 `navigation2` 与 `ros-gz`，**没有 MoveIt 2**」），并写下一句判据：

> 「声称 MoveIt 2 可用却无法运行，会让整套工具层的可信度归零。」

**取舍代价**：`move_arm_to_joints` 与 `navigate_to_pose` 被标为 `llm_visibility: "fallback"`，默认不提供给模型——今天 README §三 仍然如此。

### 转折点四：闭环语义升级 —— 把「会改变世界」变成契约字段

**如果是另一条路**：继续用「适配器自己声明验证步骤序列」的约定（参考场景能工作，异构型号不能）。

**原始论证**：`2026-09-10-closed-loop-semantic-upgrade-adr.md` 的「背景：现有系统审计结论」先列 7 项已有能力（保留，不替换），再列 **5 个真实缺口**。缺口 1 就是：

> 「**没有『读工具 / 写工具』的机器可判定标记。** `core/skills/manifest.go` 只有 `SafetyLevel`，它表达危险级别而不是『这个工具会改变物理世界』。」

ADR-1 因此引入 `MutatesWorld bool`，并**显式否决了两个替代方案**：用 `SafetyLevel` 新档位代替（会让两个正交概念绑在一起，反例是 `emergency_stop` 是物理动作但不需要场景证据）、以及每个 adapter 自己声明步骤序列（异构型号无法保证）。

ADR-2 规定了失败关闭的方向：证据缺失 → 步骤保持 `STARTED` + 可恢复失败 + **不使用成功文案**。理由那句话是全书的核心命题之一：

> 「证据缺失意味着『我们不知道世界是什么样』，验证失败意味着『世界看起来不对』——这是两类不同错误。」

ADR-7 是一份**范围裁剪声明**：语义地图（TagMap）与主动搜索**本轮刻意不实现**，并给出三条理由（关键帧 ID 未暴露、旋转搜索需要新安全包络、接口已预留）。ADR-8 补充了时间精度判据：派发时刻截断到毫秒，同一毫秒算命令后，前一毫秒拒绝，由 `DispatchPrecision` 单点定义。

**注意 ADR 的自我修正**：同一份文档的 ADR-10 段（2026-09-21 追加）明确写「上述 ADR-4 的 `Track` 重试状态机**后来已移除**；当前 `core/closedloop` 只负责完成门禁、失败分类与恢复建议」。**设计文档保留历史、不追改**——这是这个仓库的一条硬规则（见 `2026-08-18-local-first-runtime-design.md` §16 的 notice rule）。

### 转折点五（补充）：判卷的人不能是考生

`docs/architecture/post-training-pipeline.md` 与 `docs/development/2026-09-18-system-review-and-improvement-plan.md:807` 反复出现同一条原则：

> 「**不让 EvalAgent 或任何 Agent 拥有移动判据的能力。** 判卷的人不能改判据。」

落地为 `train/` 目录的定位（`docs/operations/deployment.md` 源码目录归属表）：

> 「`train/`｜离线｜训练**门禁**：`Compare`/`Gate`…它不在任何部署目标里运行，也刻意不提供训练循环——判卷的人不能同时是考生。」

---

## 3. 五版发布各自解决什么

| 版本 | 一句话定位 | 关键能力增量 | 发布记录 |
| --- | --- | --- | --- |
| **v0.2.0**（09-09，`b44c1d010`） | **把单机器人自然语言任务做成一条能复现的闭环** | 四房间家庭 MuJoCo 场景；双 RGB-D + 彩色点云；可选 RTAB-Map/Nav2；`home_task` 12 步移动抓放；机载证据保存与回看；导航预算 60 s / 抓放 15 s；三帧稳定放置确认 | `docs/releases/v0.2.0.md` |
| **v0.3.0**（09-11，`42e31df10`） | **把「物理写工具的完成判定」从约定变成契约** | `core/closedloop`；`mutates_world` 贯穿 `core/skills → skills/manipulation → robot.proto → MuJoCo 运行时 → Go 客户端`；8 类失败分类；未知终态永不自动重试；`DispatchPrecision` 单点定义时间判据 | `docs/releases/v0.3.0.md` |
| **v0.4.0**（09-11，`3d2dff62f`） | **把机器人能力变成 LLM 能直接 function calling 的工具** | 27 个工具 / 25 个默认给 LLM / 6 个命名空间；`tools.json` 由代码生成并被 `make lint` 校验；语义位置表 `assets/home_locations.json`；任务全过程回放（7 类一致性检查） | `docs/releases/v0.4.0.md` |
| **v0.5.0**（09-11，`1c7496edf`） | **把回放补成「任何时候都能完整复盘」** | 按任务编号直接回放（突破列表 50 条窗口）；四种筛选；空缺处说明原因而不是留白；修掉浏览器验收脚本的三处误判 | `docs/releases/v0.5.0.md` |
| **v0.6.0**（09-13，`e901b6f35`） | **从「能完成任务」到「能被非专家操作」** | 装修家庭场景（暖木地板/瓷砖/MIT-0 家具）；标定页支持算法录入；移动建图 + 不可变地图 + 导航栅格 + 显示点云；关键帧诊断；地图与标定/场景版本校验绑定；地图续建 | `docs/releases/v0.6.0.md` |
| **v0.7.0**（`8b9683be8`，2026-09-21，**已准备未打标**） | **给系统装上刻度与物理接地：先能证明，再谈变强** | GCL 物理接地验证（VERIFIED/FALSIFIED/UNKNOWN + 证据引用 + 9 个细分失败码）；Agent 决策上下文分阶段实验；`orchestration/eval/system_eval` 离线统一评测底座（20 能力 / 7 层 / 34 指标 / 配对比较 / 三态研究门禁）；Gazebo 运行时与建图/标定/监视线 | `docs/releases/v0.7.0.md` |

**一条贯穿五版（现在六版）的元规律**：每一版的定位句都在**收紧「什么叫完成」**，而不是扩大功能面。

- v0.2.0：任务能做完 → 但「做完」取决于计划里有没有恰好排进验证步骤
- v0.3.0：把判据下沉到工具契约 → 但 LLM 看不懂工具（底盘只有坐标、机械臂只有关节序列）
- v0.4.0：造工具层 → 但历史任务在界面上不可达
- v0.5.0：修可达性 → 但只对仿真里的「能跑的任务」有效
- v0.6.0：让非专家能操作 → 但「工具说自己成功了」仍然不等于「世界变了」
- v0.7.0：物理接地验证 + 评测底座 → **当前状态**

---

## 4. 演进中的失败与修正

以下 8 项都来自真实的提交与文档，每一行都可回溯。**这是本文件对教学最有价值的一节。**

### 修复 1：两个「永远不可能成立」的检查

**现象**：在一台已经测绘过房间的机器人上，`grounding` 永远看到 0 个物体；每个任务都在 grounding 处失败，每次失败产生一条 anomaly —— **这是后来那 139 项「发现」的总根源**。

**根因（两条互不相同的"永假条件"）**：

1. 仿真侧 `semantic_services._recall` 要求对象层文档里的 `mapId` / `mapRevision` / `calibrationRevision` 三项都与当前地图一致。但**对象层就是地图自己的产物**：它写在 `objects.json`，其字节参与哈希，哈希就是 `mapRevision`。「一个文档不可能包含自己的哈希。」
2. 语义解析器里，「语法说『要澄清』」被当成终局答案直接返回，模型**永远轮不到**：

   ```go
   if errors.Is(deterministicErr, intent.ErrClarificationRequired) {
       return manipulation.Intent{}, deterministicErr   // ← 直接返回，模型永不被咨询
   }
   ```

**为什么一直没被发现**：单元测试 `_layer()` 这个 helper **自己造了一个匹配的 `mapRevision`**。「在被检查的地方它永远存在，在被生产的地方它永远不存在。测试和自己的构造一致，于是全绿。」

**修法**：身份改用文档里**确实可能存在**的字段（`mapId` + `calibrationRevision`）+ 「读取者是从*这张*地图的目录里拿到它的」这一事实；解析器改为「确定性成功就用它，其余一律交给模型」。

**留下的防线**：新增一个使用**真实产物形状**（没有 `mapRevision`）的测试，并**验证过「把旧检查加回去它就会红」**。

出处：`CHANGELOG.md@774bd2a2f:1123`；提交 `1f6b72e27`。

### 修复 2：控制台卡死 —— 轮询比端点响应还快

**现象**：用户报告 problems 页面「一点进去就卡死」。作者第一次用 Node 测了渲染路径（12 ms）就判断「没复现」——**漏查了端点延迟**。

**根因（实测数据）**：

| 端点 | 耗时 | 控制台轮询间隔 |
| --- | --- | --- |
| `/v1/agent/alerts` | **16 s**（重启后冷启动 >60 s） | **5 s** |
| `/v1/tasks` | **40 s** | 按需 |
| `/healthz` | 0.0005 s | — |

`web/app.js` 里 `setInterval(refreshAgentAlerts, 5000)`：**每 5 秒发一个要 16 秒才回来的请求**，在途请求越堆越多，浏览器被自己的请求队列堵死。深层原因是 `task_events` 已 173,551 条、库 389 MB，**告警投影与任务列表都要读事件，成本随账本线性增长**。

**修法（只修了一半，且如实说明）**：把固定定时器换成**自调度、不重叠**的轮询 —— 下一次在当前这次**结束之后**再排，间隔取 `max(5s, 本次耗时)`。「慢账本让控制台更新变慢——**这是正确的降级**：数字旧了还能用，标签页卡死不能用。」

**作者明确写下没修的那一半**：

> 「服务端仍按全量账本计算，端点依旧 16–60 秒。**根治要做的是让投影不随账本线性增长**（增量读、或缓存投影结果），我没有做。所以现在的状态必须说清楚：**控制台不会再被自己堵死，但首屏仍然很慢。**」

**顺带发现的运维隐患**：同一数据目录上跑两个 agent（PID 34103 占 8897、35137 占 UDP 45871）会让 API 与告警行为不可预期 —— 此前的 `database is locked` 就是它。改用受管后台任务启动，不再用 `( ... &)` 子 shell。

出处：`CHANGELOG.md@774bd2a2f:7-48`；提交 `774bd2a2f`、`134ec1a5f`。

### 修复 3：「没执行却报已执行」的假声称

**现象**：在真实接口上点「执行这一步」，得到：

```
executed: true, verified: true, verification: "地图已启用"
```

而实际上**一次工具都没调用**。

**根因**：`Result.Executed` 原本在 `loop.Run` 返回后**无条件置为 true**。而「没有配置决策器」时循环是 `BLOCKED` 收尾，什么都没做。控制台会把它画成「已执行并复验通过」，并把按钮锁成「已执行」。

**为什么这是最严重的一类**：作者自己定性 ——「**这是本仓库最不该出现的一类错误**：它把『我们什么都没做』说成『机器人动了，而且确认动对了』。」

**修法（只改一处）**：让**唯一知道答案的那一层**开口。`actionloop.Outcome` 增加 `Calls`，在**唯一一处真正下发调用的地方**自增（`outcome.Calls++` 紧跟在 `l.call(...)` 之后），执行器改为 `Executed = outcome.Calls > 0`。

**并且明确否决了另一条修法**：「为什么不在 `recoveryexec` 里从 verdict 字符串推断『哪些 verdict 意味着真的调用过』：那是把派发规则抄第二遍，两份必然漂移。」

**留下的防线**：`TestABoundedWriteIsNeverRunUnattended` 覆盖目录里全部 7 个 mutating 动作，并**验证过「把风险门去掉它就会红」**。

出处：`CHANGELOG.md@774bd2a2f:1414-1452`；提交 `34ef20321`。

### 修复 4：故障丢弃整张地图

**现象**：一次空白视野（对着近墙、深度点不足）让一段探索在 **74% 覆盖率处崩掉，整张已测绘的地图完全没保存**。

**根因**：深度点不足（<100 点）**抛异常终止整段**。

**修法**：改为**跳过并计数**，连续 25 帧才优雅结束该段并发布成果。

**留下的防线与不变量**（`docs/development/2026-09-15-robot-fault-handling-audit.md:32-34`）：

- 「**深度点不足**：跳过该帧继续；连续 25 帧才优雅收尾并发布」
- 「**什么都没测到**：不发布空地图，明确报『未能开始』」
- 「这是让『部分地图』可被识别的持久记录…**部分成果可用且不冒充完整**」（`:80-81`）

**同一份审计的总结判据**（`:13-14`）：「行为是否正确？**是，且是"失败关闭"**…**没有发现"把失败当成功"的路径**」；「**不可自动恢复**（物理结果未知、急停锁定）——必须人工核对现场」。

出处：`docs/development/2026-09-15-robot-fault-handling-audit.md:16`、`:32-34`、`:76-83`；提交 `ee7c0a2f0`；CHANGELOG 标题「机器人异常处理审计：逐项验证行为/恢复/入表，并修掉"故障丢弃整张地图"」。

### 修复 5：58 个故障码静默退化成「结果未知，禁止重试」

**现象**：`GOAL_NOT_CLEAR`（目标工作区未扫描 / 间距不足，**底盘根本没动**）被报成：

```
（UNKNOWN_OUTCOME）建议：不要自动重试：先对账确认这次动作的实际结果
```

作者的定性一句话命中要害：「**让操作员去对账一个从未发生的动作，比不报还糟。**」

**根因（两层）**：

1. 分类表覆盖不全：83 个运行时唯一码里 **58 个不在表里**，落到默认分支 `UNKNOWN_OUTCOME`。
2. **扫描方法本身也踩了坑**：第一遍只搜一种抛出写法找到 53 个，后来发现 `GOAL_NOT_CLEAR` 走的是 `ServiceError(...)` —— **整整一类被漏掉**。补上后才是 83 个。

**修法**：按既有七类语义把 83 个码**全部归类**，修复后 `GOAL_NOT_CLEAR` 变为 `（PERCEPTION）重新观测或搜索目标后再尝试`；并把清单**提交进仓库**作为永久守卫 `TestEveryCodeTheRuntimeCanEmitIsClassified`。

**为什么清单要手工提交而不是自动爬取**（这条判断极有价值）：

> 「清单提交而非自动爬取是刻意的：新增码而没加进清单会落在未分类状态，守卫就会失败——**「未分类」作为默认是正确的，作为意外是危险的。**」

**同一条原则的另一处表述**（`docs/development/2026-09-18-system-review-and-improvement-plan.md:237`）：「未识别的错误码归入 `UnknownOutcome`，而不是猜成可重试——**猜测的方向选的是安全那一侧**。」

出处：`CHANGELOG.md@774bd2a2f:1725-1757`；提交 `c8cae5af2`（「分类表补齐 113 个运行时故障码」）。

### 修复 6：三处身份缺陷 —— 279 条报告变成 7 个问题

**现象**：工作台一次列两百多条，人无法审核。

**根因（三处叠加，都是「身份」问题）**：

1. **一个问题的三个阶段被当成三个问题**。`ops.anomaly_detected` / `ops.root_cause_hypothesis` / `ops.escalation_required` 是同一个发现的三个阶段，而 ops agent 给后两者加了 `hyp-` / `esc-` 前缀，投影原样保留 → 一次异常在控制台上是三行。参考部署里 92 个问题显示成 276 条。
2. **`AnomalyReportID` 的 `#count` 后缀把一个问题切成多个**。那个后缀是「**什么时候再喊一次**」（持续存在只喊一次、恶化才再喊），不是「问题是什么」。按报告 id 分组，`ANOMALY_ABNORMAL_TASK@task` 被拆成 6 个组。
3. **升级事件的两行是空的**。升级把内容放在 `context` 映射和 `reason` 里，而投影只读 `code`/`message` → 控制台上是 `code: null` 和字面量 `"None"`。

作者的定性：「**最需要人读的那几行，恰恰是什么都没有的那几行。**」

**修法**（`tasks.GroupAgentAlerts`）：按身份分组（`code@component`），阶段作为字段而不是新行；前缀与后缀**在同一个 `rootIdentity` 里剥离**，「改动只有一个地方会错」；身份**只在载荷完全没给 id 时**才合成（此前无条件重建，把 `esc-ANOMALY_ACTION_FAILED@verify_placement` 变成了 `UNKNOWN_OUTCOME@verify_placement` —— **一个不同的问题**）；`code` 优先取 `reason` 再取 `category`，因为「`reason` 命名的是**这个发现**，`category` 命名的是它被归入的**失败类别**。一行标题写 `UNKNOWN_OUTCOME` 只告诉人这是哪类问题，不告诉人是哪一个」。

**实测：120 条报告 → 7 个问题。** 出处：`CHANGELOG.md@774bd2a2f:727-758`；提交 `0774468f1`。

### 修复 7：无效的测试 —— 验收套件钉的是规划器的步骤名，不是任务的结果

**现象**：一台配了模型的栈上，同一个巡逻指令被 LLM 规划成 `["observe-start","navigate-bedroom","verify-bedroom",…]`。**任务在做正确的事**，断言却以 "unexpected confirmed steps" 失败。

**根因**：`scripts/run_home_task_suite.py` 用 `route_steps(3) = ["observe","pre_position","navigate_00","verify_arrival_00",…]` 断言 `step_ids == expected_steps` —— 这套名字**只在确定性规划器下成立**。

**自认的讽刺**：`docs/architecture/orchestration-post-training.md` 里写着「用例断言的是『机器人去了厨房』，不是『计划里有 `navigate_route`』」，而「**评测器自己踩了它在注释里警告的坑**」。

**修法（2026-09-18 完成）**：断言改成**结果**—— `state == SUCCEEDED`、场景要求的工具都被调用过（名字与顺序不管）、每个确认步骤有 `evidenceSource == command_observation`、启用地图上的导航跳数、`verify_placement` 的 3 个稳定几何样本 + `inside:kitchen-tray` + `object_id == ceramic-mug`、每张 RGB/depth 的 SHA-256。

**留下的防线**：新测试「同一件事换成模型式的步骤名必须照样过关」；**把断言改回按名字比较恰好这条测试失败**。

出处：`docs/development/2026-09-18-system-review-and-improvement-plan.md:49-78`。

### 修复 8：静默降级 —— 三种「没有活跃地图」被吞成同一个症状

**现象**：控制台只显示 `{"ready": false, "localizationState": "unavailable", "gridUnavailable": true}`。

**根因**：`_restore_active()` 把**所有**失败都吞进 `self.active = None`，而三种「没有活跃地图」不是同一件事：从来没定位过 / 指针指向别的标定 / **指针指向的地图加载不了**（这是部署事实，操作员可以换一张图）。

**修法**：第三种写进 `active_map_error` 并在 `navigation_map()` 里作为 `localizationReason` 发布。判据一句话：

> 「报 `unavailable` 的调用方会重试；报『这张图没了』的调用方会换一张。」

**同一轮里一并修掉的三处「只找一层」假设**（四处代码各自独立地假设地图包在根目录的直接子目录里）：

| 位置 | 症状 |
| --- | --- |
| `console/maps.go listMaps` | 只看根的直接子目录 → 分组地图不出现在列表 |
| `console/maps.go mapDir` | `filepath.Join(root, id)` → 按 id 请求 `MAP_NOT_FOUND` |
| `gateway/map_catalog.py MapCatalog.open` | 要求 `directory.parent == root` → 启用失败 `manifest.json does not exist` |
| `gateway/robot_workflow.py available_maps` | 遍历 `root.iterdir()` 再 `catalog.open(目录名)`，分组目录名不是里面任何地图的 id → **查询必然落空，包被静默跳过** |

后果是「这栋房子该复用哪张地图」的答案永远是「没有可用的地图，重新扫一遍吧」。修法是四处一致地按 `manifest.json` **发现**包、允许一层分组（`GROUP_DEPTH = 1`）。

出处：`docs/development/2026-09-21-map-visibility-and-inventory.md:14-41`、`:53-70`、`:186-193`。

### 修复 9（补充）：两个生产端对同一个量用了两套约定

**现象**：`recalledGoal` 一路失败在 `goal exceeds robot workspace on navigation.z`，**错误信息听起来像坐标问题**。

**根因**：物体层的 `observedFrom` 是 **2D 底盘位姿**（x, y, yaw），抬升成 7 元组时 z 被写成 **0**（地图地面）；而委任目标带的是**底盘高度 0.035**，`withinWorkspace` 按宣告的 `navigation.z: [0.035, 0.035]` 严格比较。

**修法**：抬升用委任目标自己所在的平面（**一个平面，两个生产端共用**），有测试钉住。实测把失败点从「还没动就被拒」前移到第三步导航。

出处：`docs/development/2026-09-18-system-review-and-improvement-plan.md:144-153`。

---

# 第二部分 · 架构总览（第 2 章）

## 5. 分层图

**主要依据**：`docs/development/principles.md`（开发代码地图，第 20-49 行的「源码地图」表 + 第 5-18 行的八条必须保持的原则）与 `docs/operations/deployment.md`（部署归属表，第 33-60 行的「源码目录归属」）。两份文档是**互补而非重复**的：前者回答「改动放在哪一层」，后者回答「哪部分装到哪台机器」。

```
┌─ L1 前端 / 控制台 ────────────────────────────────────────────────┐
│  web/            控制台前端静态资源（被 console/ 与 fleet/ 双嵌）    │
│  console/        本地控制台 HTTP API + 内嵌前端资源                 │
│  职责：把任务、工具活动、证据、恢复状态呈现成人能读的界面；只读投影    │
│  绝不能：直接调用机器人（原则 7；一切走 task/runtime 应用服务）      │
└──────────────────────────────────────────────────────────────────┘
                              ↓ (HTTP / WS，同源)
┌─ L2 本地 Agent / 编排入口 ────────────────────────────────────────┐
│  cmd/local-agent/    单机 Agent 进程入口（内嵌工作台）              │
│  internal/localapp/  Local Brain 装配、依赖注入、恢复与工作队列      │
│  cmd/robot-agent/    运维 CLI：doctor/configure/pair/start/status   │
│  职责：唯一组装点（composition root），选择适配器实现                │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ L3 编排 / 任务领域 ──────────────────────────────────────────────┐
│  agent/              自然语言 → 意图（agent/intent/）              │
│  orchestration/      intent + 能力目录 → 可执行计划；计划守卫        │
│  tasks/              Task / Revision / 更新 CAS / 安全点 / 事件投影 │
│  skills/manipulation 技能模板                                       │
│  core/compiler/ core/guard/  编译与计划校验                         │
│  agentruntime/       多 Agent 运行时：事件总线、注册表、编排器、     │
│                      权限门控、只读观察 Agent、只提议的恢复 Agent    │
│  绝不能（原则 4）：导入 SQL / Redis / gRPC / protobuf / 机器人 SDK  │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ L4 世界模型 / 证据 ──────────────────────────────────────────────┐
│  core/observation/   观测信封（schema/frame/transform/freshness）   │
│  core/worldmodel/    世界投影与 Snapshot（revision 单调）          │
│  fleet/worldhub/     单写者世界投影服务（云端与本地共用）           │
│  core/harness/       后置条件判定：命令后新鲜 + 连续稳定 + 坐标一致  │
│  core/closedloop/    写工具完成门禁、失败分类、恢复建议             │
│  core/agentcontext/  决策上下文事实包（默认 legacy）               │
│  职责：回答「世界现在是什么样」与「这一步算不算做完了」             │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ L5 执行运行时 ───────────────────────────────────────────────────┐
│  edge/agent/         Local 执行循环（闭环步进、grounding、遥测）    │
│  edge/runtime/       Go 语义 Runtime：能力、命令、取消、急停        │
│  edge/robotclient/   gRPC 映射（唯一知道线协议的 Go 包）           │
│  edge/worker/        Fleet Worker：工具执行、观测上报、恢复         │
│  edge/policy/        PolicyManifest / Observation / Inference 契约  │
│  edge/recovery/      恢复分类                                     │
│  internal/actionloop/ 决策循环（PlanDecider / LLMDecider）         │
│  internal/recoveryexec/ 恢复动作执行（按目录声明工具）             │
│  绝不能：绕过 safety.py 的否决点                                    │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ L6 安全监督 / 网关（机器人侧）───────────────────────────────────┐
│  robot/gateway/       Python service、SafetySupervisor、journal、   │
│                       领域工具层（tool_layer.py + tools/）          │
│  robot/ros2_ws/src/   xlerobot_adapter、tangying_navigation、      │
│                       tangying_safety_supervisor（心跳看门狗）      │
│  robot/mcp/           MCP stdio 桥（8 个粗粒度工具，无自动批准）    │
│  职责：唯一否决点；急停锁存、租约、profile、参数校验、幂等          │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ L7 适配器 / 仿真 ────────────────────────────────────────────────┐
│  policy/sidecar/     Python VLA/IL/RL HTTP 包装（不随附已训模型）   │
│  sim/mujoco/         自有仿真适配、场景、共享世界、观测与可视化     │
│  sim/robocasa/       RoboCasa 厨房、双 XLeRobot、共享交接世界       │
│  sim/gazebo/         Gazebo 运行时与 ROS 桥（v0.7.0 接入）          │
│  examples/           适配器示例                                     │
│  XLeRobot/           上游参考（**不在其中绕过系统接口**）           │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 横切 ────────────────────────────────────────────────────────────┐
│  core/              不依赖具体基础设施的契约（原则 4 的检查对象）   │
│  middleware/        memory / sqlite 适配 + 根契约包                 │
│  fleet/             云端：auth / coordinator / eventlog / gateway / │
│                     lease / mysql / redis / registry / worldhub /   │
│                     queue / telemetry                               │
│  latency/ incidents/ train/ training/ scripts/ deploy/ proto/ gen/  │
│  python/ tests/                                                     │
└──────────────────────────────────────────────────────────────────┘
```

### 依赖方向：谁能调谁，谁绝对不能调谁

**机械检查存在，不靠约定**：`tests/architecture/dependencies_test.go`。它用 `go list -json` 读真实包导入图，包含至少四个独立测试：

| 测试 | 断言 |
| --- | --- |
| `TestCorePackagesDoNotImportConcreteInfrastructure` | `agent/... orchestration/... tasks/... core/... edge/agent/... edge/runtime/... agentruntime/...` 不得导入具体基础设施或厂商 SDK |
| `TestAgentRuntimeDoesNotImportConcreteAgents` | `agentruntime/` 不得导入 `edge/agent`、`core/harness`、`agent` —— 「a runtime that imported the agents it hosts would have to be edited every time one was added, and the promise would quietly stop being true」 |
| `TestAgentContractHasNoInternalDependencies` | `core/agentcontract/` 必须保持零内部依赖（执行路径与运行时都要依赖它） |
| `tests/architecture/powered_test.go` | 声明式表：**已实现但无生产数据源的规则会红**（见第 4 节修复 1） |

**八条必须保持的原则**（`docs/development/principles.md:5-18`）：

1. 模型只提出意图或候选动作 —— LLM 不生成审批、期限、lease、幂等键、fencing 或 safety profile
2. 任务成功需要环境证据 —— Tool 成功 ≠ 物体到了目标
3. 同一 Runtime 契约贯穿仿真与实机
4. 核心依赖接口，适配器依赖 SDK
5. 重试以物理结果为边界 —— 队列可以至少一次交付，物理动作不能重复；重启不解除急停锁存
6. 版本与身份不能倒退 —— 不通过重置数字或编辑历史解决冲突
7. 界面只呈现可信状态 —— 隐藏开发按钮不是服务端权限控制
8. 证据与结论绑定版本和环境 —— 单元测试、仿真闭环、历史签名包、现场记录是**不同证据**

### 关于「分层是物理强加的」这一论证

`docs/architecture/why-distributed.md` §1.1 给出了分层的第一性理由，可直接引用：

| 约束 | 数量级 | 后果 |
| --- | --- | --- |
| 控制回路 | 100 Hz – 1 kHz | **不能**穿过一次网络往返，更不可能穿过一次 LLM 推理 |
| 急停 | 毫秒级 | **不能**等网络——等得到的那不叫急停 |
| 感知带宽 | 点云/图像，每秒数十 MB | **不能**原样上传 |

推论同样有力：「**任何把 LLM 放进控制回路的架构都是错的。**」

而 §6 给出了一个罕见的命名反思，非常适合写进书里：这个项目实际是 **Agent Runtime + 任务账本 + 安全门禁**，不是 OS。「叫它 OS 会让人期待它保证实时性，而它保证不了——**期待错位比能力不足更危险**。」

---

## 6. 两条部署形态：共享什么，独有什么

### 结论（基于实测依赖图，不是基于文档声称）

**共享的包**（两条形态都用，且不导入任何云端专属包）：

```
agent/               意图解析
orchestration/       计划生成与守卫
tasks/               任务领域与 Revision
core/                全部契约（closedloop / worldmodel / observation / harness /
                     skills / compiler / guard / taskgraph / agentcontract /
                     robotcontract / agentcontext / telemetry / trace）
skills/manipulation/ 技能模板
middleware/          存储适配（memory / sqlite）+ 根契约
edge/agent/          Local 执行循环
edge/runtime/        Go 语义 Runtime
edge/robotclient/    gRPC 映射
web/                 前端静态资源（被 console/ 与 fleet/ 双嵌）
latency/             步骤耗时记录器
proto/ gen/ python/  线协议与生成代码
```

**Local Brain 独有**：

```
cmd/local-agent/     单机 Agent 进程入口
internal/localapp/   Local Brain 装配与工作队列
internal/discovery/  机器人广播监听
internal/pairing/    免 SSH 配对的证书签发
internal/actionloop/ 决策循环
internal/recoveryexec/ 恢复动作执行
console/             本地控制台 HTTP API
incidents/           事故记录写入器
middleware/sqlite/   SQLite 适配（云侧改用 fleet/mysql）
agentruntime/        多 Agent 运行时（由 cmd/local-agent 托管）
```

**Cloud Fleet 独有**：

```
cmd/fleet-control-plane/  云端进程入口
fleet/                    全部子包
cmd/edge-worker/          边缘执行进程
edge/worker/  edge/cloudclient/  edge/policy/  edge/recovery/
robot/mcp/                MCP 桥（对外入口）
deploy/cloud/
```

### 实测取数命令与结果

```bash
export GOCACHE=$PWD/.gocache GOMODCACHE=$PWD/.gomodcache GOPATH=$PWD/.gopath

# internal/localapp 的非测试依赖里有几个 fleet 包？
go list -deps -test=false ./internal/localapp | grep -c '/fleet/'
# → 0（exit 1，即 grep 无匹配）

# cmd/local-agent 呢？
go list -deps -test=false ./cmd/local-agent | grep '/fleet/'
# → 8 个：
#   fleet/eventlog  fleet/lease  fleet/coordinator  fleet/telemetry
#   gen/go/fleet/v1  fleet/registry  fleet/redis  fleet/worldhub

# 这 8 个是怎么进来的？
#   fleet/worldhub  ← cmd/local-agent/main.go:31 直接导入
#                     而 worldhub 只导入 core/observation + core/worldmodel
#   fleet/redis / fleet/coordinator / gen/go/fleet/v1 / fleet/telemetry / fleet/lease / fleet/registry
#                   ← edge/worker（cmd/local-agent 直接导入）
#   fleet/eventlog  ← middleware/sqlite（本地持久化适配）
```

### 「local-agent 不引用云端」这条声称的精确化

`docs/architecture/why-distributed.md` §5 有一张检查表写着「`cmd/local-agent` 引用云端 / fleet：**0 处**」。

**实测不是 0，是 8 个包（非测试依赖）**。但两者并不矛盾，因为**依赖存在 ≠ 运行时需要**：

- `internal/localapp` 本身的 fleet 依赖是 **0**；
- 唯一的直接 fleet 导入是 `fleet/worldhub`，而 `worldhub` 只依赖 `core/observation` 与 `core/worldmodel`——**它不需要 Redis / MySQL / coordinator 就能工作**；
- `fleet/redis` 与 `fleet/coordinator` 是通过 `edge/worker` 进入的，而 `edge/worker` 是 `cmd/local-agent` **同时托管 Fleet Worker 模式**用的（这正是 `docs/development/2026-09-18-system-review-and-improvement-plan.md:847` 那条待办「6.1 解耦 `agentruntime` 与 `cmd/local-agent`」与验收项「双托管」的来源）。

**结论可以直接写进书里**：这是一个**编译期共享、运行期可分**的架构——单二进制里同时装着两条形态的代码，靠组装点选择走哪条路。代价是二进制体积（工作树里的 `local-agent` 二进制 **80.7 MB**）和「静态依赖 ≠ 运行时依赖」这一层需要向读者解释的歧义。

---

## 7. 代码规模真实数字

**全部在本机实际数过，不抄 README。** 命令与结果如下。

### Go

```bash
# 包数（go list，需本地 module cache）
export GOCACHE=$PWD/.gocache GOMODCACHE=$PWD/.gomodcache GOPATH=$PWD/.gopath
go list ./... | wc -l
# → 72

# Makefile 实际参与测试的包集（GO_TEST_PACKAGES，Makefile:4）
go list ./agent/... ./cmd/... ./console/... ./core/... ./edge/... ./fleet/... ./gen/... \
        ./internal/... ./middleware/... ./orchestration/... ./skills/... ./tasks/... \
        ./tests/architecture/... ./tests/contract/... ./web/... | wc -l
# → 65

# 有测试文件的包
git ls-tree -r --name-only HEAD | grep '_test\.go$' | sed 's|/[^/]*$||' | sort -u | wc -l
# → 57

# 测试函数（含 Test/Fuzz/Benchmark）
grep -rn --include='*_test.go' -E '^func (Test|Fuzz|Benchmark)[A-Za-z0-9_]*\(' . \
  | grep -v '/.gomodcache/' | grep -v '/vendor/' | grep -v '/XLeRobot/' | wc -l
# → 1183        （其中纯 Test：1181）

# 同上，但只看已提交的 HEAD 树（排除工作区未提交改动）
git grep -h -E '^func (Test|Fuzz|Benchmark)[A-Za-z0-9_]*\(' HEAD -- '*_test.go' | wc -l
# → 1040
git ls-tree -r --name-only HEAD | grep -c '_test\.go$'
# → 178

# Go 包（按顶层目录分布）
go list ./... | sed 's|.*tangying-robot-agent-os/||' | awk -F/ '{print $1}' | sort | uniq -c | sort -rn
# → fleet 13 | core 13 | internal 8 | edge 7 | cmd 7 | tests 5 | middleware 3
#   orchestration 2 | gen 2 | agent 2 | web 1 | training 1 | train 1 | tasks 1
#   skills 1 | latency 1 | incidents 1 | console 1 | artifacts 1 | agentruntime 1
```

### Python

```bash
# 测试函数（含缩进的 class 内方法）
grep -rn --include='test_*.py' --include='*_test.py' -E '^\s*def test_[A-Za-z0-9_]*\(' . \
  | grep -v '/.venv/' | grep -v '/.gomodcache/' | grep -v '/vendor/' \
  | grep -v '/XLeRobot/' | grep -v '/datasets/' | grep -v '/.worktrees/' | wc -l
# → 1726

# 同上，只看 HEAD 树
git ls-tree -r --name-only HEAD | grep -E '(^|/)(test_[^/]*|[^/]*_test)\.py$' > /tmp/f.txt
git grep -h -E '^[[:space:]]*def test_[A-Za-z0-9_]*\(' HEAD -- $(cat /tmp/f.txt) | wc -l
# → 1370
# 测试文件数（HEAD 树）
git ls-tree -r --name-only HEAD | grep -E '(^|/)(test_[^/]*|[^/]*_test)\.py$' | wc -l
# → 132
```

### 文档

```bash
find docs -name '*.md' | wc -l
# → 144（工作区）

git ls-tree -r --name-only HEAD | grep '^docs/.*\.md$' | wc -l
# → 124（HEAD 树）

git ls-tree -r --name-only HEAD | grep -c '\.md$'
# → 137（全仓库，含 README/CHANGELOG 等）
```

### 汇总表与 README 的对比

| 指标 | 实测（HEAD 树） | 实测（工作区） | README 声称 | 差异说明 |
| --- | --- | --- | --- | --- |
| Go 包数 | — | **72**（Makefile 测试集 65） | 未声称 | README §八 只写了「`fleet/`、`edge/` 11.7k 行生产代码」 |
| Go 测试函数 | **1,040** | **1,183** | 1,079 | README 数字落在两者之间，指向一个中间快照 |
| Go 测试文件 | 178 | 219 | 未声称 | |
| Python 测试函数 | **1,370** | **1,726** | 1,656 | README 介于两者之间，同上 |
| Python 测试文件 | 132 | 160 | 未声称 | |
| 文档篇数（`docs/`） | **124** | 144 | 123 | 基本一致 |
| 全仓库 `.md` | 137 | 164 | 未声称 | |

**这个对比本身就是一条教学素材**：README 里那两个数字（1079 / 1656）**既不等于 HEAD 也不等于工作区**，说明它们是在某个中间时点手抄的，并且**没有任何机制保证它们跟着代码走**。这与仓库自己那条原则（`docs/development/principles.md:8`「报告实际通过与跳过项」）形成了有意思的张力——**原则的执行是有边界的：它管住了发布记录，没管住 README 的两个数字**。

（补充参照：`docs/development/2026-09-15-optimization-backlog.md:14-16` 记录过另一组快照：Go 53 个包、Python 1641 通过 / 34 跳过、Web 375 通过、`docs/` 93 篇 / 44.7k 行。09-13 的 `docs/development/2026-09-13-system-audit.md:144-148` 是 Python 1522 passed / 35 skipped、Gateway 定向 577 passed、ROS 2 容器 57 passed、Web 325 passed。这些数字会随版本波动，**引用时必须带日期**。）

---

# 第三部分 · 未来方向（第 17 章）

## 8. 作者自己列出的未验证清单

来源：`docs/architecture/distributed-agentos.md:33-39`，小节标题「仍须独立验证的范围」。**这是整个仓库最诚实的未来方向来源**——它不是路线图，是「我做了但没能证明」的清单。

先看它前面的语境（`:27-31`「持久化与单写范围」）：

> 「这解决单主进程重启的一部分状态恢复，**不提供跨主机共识或跨存储事务**。文件锁/损坏/保存失败应失败关闭；不能多开 writer、删除快照或降低 token 来『恢复服务』。」

### 逐条展开

**① leader fencing 与所有业务提交的同存储原子校验**（`:35`）

- **为什么重要**：今天的 leader lease 在 Redis、业务提交在 MySQL（或本地 SQLite）。**两处存储之间没有原子性**——一个刚失去 leadership 的实例，在租约过期到它下一次尝试提交之间的窗口里，理论上仍能写入。fencing token 的存在防止了旧 token 的写入，但「token 校验」与「业务提交」本身如果不是同一个事务，「检查通过 → 提交前被打断」就是真实窗口。
- **要验证它需要做什么实验**：注入「leader 切换恰好在检查与提交之间」的故障。可复用 `docs/superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md` §13 的注入点清单（`TaskSource`、`ObservationSink`、Robot Runtime client、Clock、Event Store、Redis publisher、Realtime Gateway），但注入时机要精确到**提交前 N 毫秒**，并断言「同一 task event 只落一次」。
- **判据**：`docs/development/2026-09-18-system-review-and-improvement-plan.md:848` 已有雏形——验收清单里的「6.3 租约/原子 fence」，当前状态 ⬜。

**② MySQL、Redis、世界投影与物理结果之间可恢复的资源转移 saga**（`:36`）

- **为什么重要**：这是**唯一一个跨越「软件事务」与「物理世界」的转移**。方块从 robot-1 的 owner 转给 robot-2，中间可能宕机在四个不同位置：数据库提交前、提交后事件发布前、机器人抓取中、抓取完成但 ACK 丢失。今天的设计用 outbox + 幂等 + 世界对账覆盖了大部分，但**没有一个显式的、可恢复的 saga 状态机**。
- **要验证它需要做什么实验**：`2026-08-20-distributed-agentos-world-harness-design.md` §13 的异常注入矩阵里已列出 9 类故障（观测乱序、Edge 断网重连、动作后回报前 worker 崩溃、协调器崩溃/切换、Redis 不可用、stale fencing、robot-2 交接后离线、相机丢帧/UI 重连、物体被移走）。**缺的是把「崩溃点」参数化**：同一场景在 8 个不同的提交边界各崩一次，断言最终只有一个 owner 且不重复物理动作。
- **当前基础**：`fleet/coordinator` + `fleet/eventlog` + outbox 已实现；`docs/architecture/multi-robot.md` 给出了完整的交接序列图。

**③ 数据库/队列切主、网络重排和长期多进程故障验证**（`:37`）

- **为什么重要**：今天的所有故障注入都在**单机可控环境**里；真实切主涉及 DNS/连接池/客户端重连/时钟漂移的组合。
- **要验证它需要做什么实验**：`docs/development/2026-09-15-robot-fault-handling-audit.md:92` 已经指出这条路径的根本缺口：

  > 「**没有「故障注入」的常驻手段**…应把 `tests/e2e` 的故障矩阵接进 CI 的定时任务（当前它们不在默认 `make test` 里）。」

  以及 `docs/development/2026-09-18-system-review-and-improvement-plan.md:811` 的一条硬前置：

  > 「**不在没有真实 Redis/MySQL 的测试之前，把 Fleet 用于任何真机。**」

- **判据**：验收清单 `:850`「6.6 真实 Redis/MySQL」，当前 ⬜。

**④ 实机传感器质量、坐标标定、匹配策略、实体急停、停止响应和受限任务验收**（`:38`）

- **为什么重要**：这是**软件一切结论的边界条件**。仓库里所有「已验证」都是仿真验证。README §六 写得很清楚：「**软件发布与实机放行是两项独立结论**——仿真通过不等于实机可用，这是刻意的。」
- **要验证它需要做什么实验**：`docs/production/v1-assessment-2026-09-05.md:49`、`:56` 给出量化门槛：「至少按已有手册完成 **30 次真实试验**，再补充限定任务成功率、**24/72 小时长稳**、备份恢复和故障注入」；`docs/development/2026-09-15-optimization-backlog.md:40` 有「S5：12 项『仿真通过后仍需』」清单。
- **当前状态**：`docs/production/v1-assessment-2026-09-05.md:44-49` 列出 6 条实机上线阻塞（固定驱动 / 上游兼容 / 感知策略 provider / 硬件安全 / 持久化 HA / 生产验证）。

**⑤ 目标终端可见帧率、现场容量、备份恢复和长期运维**（`:39`）

- **为什么重要**：这是「能不能长期跑」的问题，与前四条正交。
- **要验证它需要做什么实验**：`docs/production/v1-release-status.md:53` 记录过一次实测：浏览器约 498 px 宽、RGB/深度各约 18 秒刷新、底盘三路各约 7 秒 —— 并明确标注「**这是本次短时 UI 验收，不代表长时间网络、导航或实机可靠性验收**」。需要的是：目标 Linux 主机上的延迟测量（`v1-release-status.md:70`：「部署前必须在目标 Linux 主机测量相机、TF、控制与网络延迟」）+ 长时间压力测试 + 备份恢复演练。

---

## 9. 刻意的非目标

来源：`README.md:283-291`（第十节）。README 自己给这一节的理由是：

> 「一个项目的取舍比功能列表更能说明它的判断。」

### ① 不用软件急停替代实体急停

- **背后的判断**：软件状态永远低于硬件限制。落地为一条不可绕过的实现约束：**远程 API 永不暴露急停 reset**（`2026-08-18-local-first-runtime-design.md` §11「Remote APIs never expose clear/reset. A physically present operator must clear the condition locally.」）。
- **代价**：`set_speed_limit` 之类的软件安全能力**不能宣称等于安全**；软件层能做的最正确的事只是「把请求送达」并记录。`docs/development/2026-09-18-system-review-and-improvement-plan.md:808` 明确：「不用软件路径『解除』急停。保持不变——但阶段 4.5 必须补上『软件层唯一能做的正确动作』的入口。」而 4.5「急停入口」在验收清单 `:843` 仍是 ⬜。
- **同源文档**：`docs/architecture/supervision-verification.md:199`「软件不代按实体急停」。

### ② 不把仿真模型当作未经标定的实机真值

- **背后的判断**：`2026-08-21-robocasa-webgl-digital-twin-design.md` §13 定得很清楚：「视觉模型与真实硬件 revision 不匹配时明确降级，**不能把当前官方模型当作已标定数字孪生**」。同样地，`docs/production/v1-release-status.md:64` 对一组精度数字标注：「上述数值只表示理想仿真工位，**不能解释为实机精度**」。
- **代价**：所有仿真验收都不能转化为实机放行；`docs/architecture/distributed-agentos.md` 结尾那句「签名历史证据与本轮验证须分别看 V1 状态，**不能从 UI 演示推断实机已完成**」是这条的日常执行形式。
- **一条容易被忽略的推论**：这也意味着**仿真里的"数字孪生"只是视图，不是世界模型**。设计文档 §4 那句「GLB 决定对象『长什么样』，`WorldSnapshot` 决定对象『在哪里、处于什么状态』」是这条边界的技术表述。

### ③ 不在观测陈旧、资源冲突或证据缺失时猜测成功

- **背后的判断**：`2026-09-10-closed-loop-semantic-upgrade-adr.md` ADR-2 的原文推论：「未验证的物理动作既不能重试（可能重复执行），也不能宣告成功（可能没做），只能进入『未知终态 + 人工对账』。」
- **代价**：**系统会主动失败**。README §四 里那条「返程导航 ⚠️ 受地图覆盖限制」就是这条非目标的日常代价 —— 「参考地图上返程点未认证可通行，`navigation.navigate` 以 `GOAL_NOT_CLEAR` 结束。这是『不知道的地方不进去』，不是缺陷，但**在这一版上返程确实没走通**。」
- **一条正面的副作用**：因为它不猜，所以「不知道」可以被度量。`docs/development/2026-09-18-system-review-and-improvement-plan.md:237`：「未识别的错误码归入 `UnknownOutcome`，而不是猜成可重试——**猜测的方向选的是安全那一侧**。」

### ④ 不承诺单机开发栈已经具备跨地域生产级高可用

- **背后的判断**：README §二 的边界声明：「当前**主线是一台机器人**在受限环境把活干完…恢复能力限于单主进程重启范围内的一部分状态恢复，跨主机共识与跨存储事务仍在待验证清单上。」
- **代价**：Fleet 那一整套代码（`fleet/` 13 个包 + `edge/` 7 个包）**在当前主线里不产生价值**，只作为扩展路线保留。README §「其他路线（保留但暂不聚焦）」明确列出：「**多机器人 / Fleet**…单机器人把自己的任务做完即可；多机协调属于调度层，不影响上面的闭环。」
- **这条判断的元价值**：它把一个容易被误读的架构（「分布式系统怎么只跑一台？」）变成了一个明确的产品陈述。`docs/architecture/why-distributed.md` §7 的「判断表」是它的决策版本，其中有一行值得抄进书里：「一台机器人，有人在现场 → **本地单机**。少一层，少一类故障。」

### ⑤ 不让大模型输出关节角

- **背后的判断**：物理动作的粒度是任务，不是电机。README §三 把它做成了一张工具可见性表：`move_arm_to_joints`（关节角）与 `navigate_to_pose`（位姿）**默认不提供给大模型**，只留给工程调试；执行通道上「所有工具走同一条 `ExecuteSkill` 通道与安全监督；模型不接触关节角、轮速或坐标」。
- **代价**：模型的表达能力被人为收窄。ADR-2 的原文承认这条张力并给出了解法：`move_arm_to_joints` **保留为底层兜底工具**，但标记 `llm_visibility: "fallback"`，「这样『底层兜底可选、LLM 默认看不到』两个要求同时成立，**而不是假装它不存在**」。
- **MCP 侧同样收紧**：`robot/mcp/` 只给 8 个粗粒度工具，「不提供批准、不提供自动批准、不暴露关节控制」。

---

## 10. 可推断的未来方向

### (a) 源码与文档里明确的 TODO / 未实现声明 / 后继计划

**这一节全部是原文摘录，不含推断。**

| # | 明确的未实现声明 | 出处 |
| --- | --- | --- |
| A1 | 「工作台支持手动有界移动扫描，以及驱动注册的巡航路线扫描。后者使用已配置路线，**尚未实现未知住宅的自主 frontier 探索**。」 | `README.md` §五 |
| A2 | 「**返程导航（回到出发房间）**：⚠️ 受地图覆盖限制…`navigation.navigate` 以 `GOAL_NOT_CLEAR` 结束。这是『不知道的地方不进去』，不是缺陷，但**在这一版上返程确实没走通**。」 | `README.md` §四 |
| A3 | 「**没有真机/仿真端到端跑通一句话建图。** 验证停在服务与工具层：决策、清单、编排、工具面都有测试，但『人对机器人说一句话 → 真的建出图来』这条链路没有在仿真或真机上走一遍。」 | `docs/development/2026-09-19-agent-initiated-mapping.md:112` |
| A4 | 「**没有为建图完成加回调或事件**——那需要任务级编排，不在本轮。」 | `docs/development/2026-09-19-agent-initiated-mapping.md:109` |
| A5 | 「`environment` 与地图身份的对应**没有真正的索引**…真正的消歧需要地点索引，那是比这条规则大得多的改动。」 | `docs/development/2026-09-19-agent-initiated-mapping.md:108` |
| A6 | 「**没有改探索算法**。这一轮只动『地图能不能被看见、能不能被用』和地图清单。覆盖率本身要再提高，是探索层的事。」 | `docs/development/2026-09-21-map-visibility-and-inventory.md:202-203` |
| A7 | 「**为什么那个文件会过期没有被查到**…**这是一个仍然存在的未知**，值得单独查一次：一个写了却没人验证的凭据文件，下一次可能以别的方式失效。」 | `docs/development/2026-09-21-map-visibility-and-inventory.md:207-210` |
| A8 | 「**仍然没有写这段 workflow**：第 4 步（跑套件）目前在一台真栈上还过不去。**写一个跑不通的 CI job 正是这份文档一路在记录的那类缺陷。**」 | `docs/development/2026-09-18-system-review-and-improvement-plan.md:104-105` |
| A9 | 「**阶段 1–7 仍未开始**（EvalAgent、数据集、EscalationAgent、trace、分布式、自动修复）」；「**阶段 1 的验收判据本身也没达成**：文档要求『三场景 × 10 轮、成功率 ≥ 90%』…返程那一步仍失败在 `GOAL_NOT_CLEAR`，CI 门禁没接」 | `CHANGELOG.md@774bd2a2f:404`、`:473-475` |
| A10 | 「**不在 grounding 修好之前做 RL。** 环境是坏的，reward curve 看起来会像学习，**实际在拟合噪声**。」 | `docs/development/2026-09-18-system-review-and-improvement-plan.md:806` |
| A11 | 「**不在没有真实 Redis/MySQL 的测试之前，把 Fleet 用于任何真机。**」 | 同上 `:811` |
| A12 | 「`decision` 是**返回**而不是**抛出**…」；「**这个区别是刻意的**：用户主动要求建图是操作员意图，不该需要批准；而恢复流程自己发起重新巡检是补救动作，仍然要人同意。」 | `docs/development/2026-09-19-agent-initiated-mapping.md:71`、`:111` |
| A13 | 「**还没有**——循环建好了，但**没有人用它跑真实任务**。接进 runner 是下一步，也是**风险最高的一步**（物理执行路径）。」 | `docs/architecture/llm-driven-execution.md:152-158` |
| A14 | 「**没有 mDNS/DNS-SD**。广播够用且不需要额外守护进程；`_tangying-robot._tcp.local` 的设计描述仍在 `docs/superpowers/specs/` 里，**未实现**。」 | `docs/architecture/robot-discovery.md:105` |
| A15 | 「舰队级监督：**监督 agent 只在本地单机接线**；`edge-worker`（云端路线）**尚未接**。」 | `docs/architecture/supervision-verification.md:307` |
| A16 | 「完整闭环是五段，其中**两段尚未实现**。」 | `docs/architecture/review-agent.md:130` |
| A17 | 「**Section 12.3 实现边界**：新模型/工具/workflow/仿真/实机 runner **尚未统一接入**；自动归因、oracle 干预、训练导出、Pareto 搜索 **已设计，尚未实现**；连续成本门禁、多风险、顺序检验、线上监控 **已设计，尚未实现**。」 | `docs/architecture/agent-evaluation-system.md:417-430` |
| A18 | 「**先有刻度，再谈自训**」的落实顺序：「1. **修 grounding**…在那之前一切训练都是空转。2. **记录被拒绝的请求**…**没有替代来源**。3. **扩大请求语料**…4. 跑 `make nl-eval` 三方对照…5. **SFT 第一版**…**拒绝样本必须混入**。6. **最后才是 RL**，且奖励只能从闭环证据取。」 | `docs/architecture/post-training-pipeline.md:158-176` |
| A19 | 「**判据不能由被判者提供。** 账本量化、拒绝采集、评测、门禁——这四件事都必须是确定的代码；LLM 只做它真正擅长的：诊断、提议、写代码。」 | `docs/architecture/post-training-pipeline.md` 末节 |
| A20 | 明确的后续功能清单：「场景感知澄清、跨任务上下文、可验证的回合重置/重新授权和反向搬运、旧 XLeRobot provider 迁移到严格感知合同、真实传感器与新型号驱动、可信物料授权同步与 Runtime 独立 revision/step 校验、房间尺度探索与稠密地图验收、图形化标定和模型导入、纯用户端服务端权限裁剪、更长时间压力测试，以及跨主机高可用。」 | `docs/production/v1-release-status.md:171` |
| A21 | `PLACEMENT_NOT_OBSERVED` **没有测试**，「应优先补：确认它是**可恢复失败**还是**结果未知**」；`recover_to_safe_pose` 标为不可用，「所以『安全恢复』**目前只能人工介入**」。 | `docs/development/2026-09-15-robot-fault-handling-audit.md:89-90` |
| A22 | 一条**故意留白的产品决策**：`GOAL_NOT_CLEAR` 的几何有「**三条修法，各有代价，我没有替项目做这个决定**」——① 厨房路点移约 0.3 m；② 换方向补扫；③ 放宽 goal 净空判据。「**这是导航语义的决定，不是机械修补。**」 | `docs/development/2026-09-18-system-review-and-improvement-plan.md:133-142` |
| A23 | 8 项按优先级排序的优化清单，每项附**对比实验设计与量化判据**（P0-1 遥测 / P0-2 语义层进计划 / P1-3 长走廊退化 / P1-4 从零扫全 / P2-5 多视角一致性 / P2-6 文档一致性 / P3-7 学习型策略 / P3-8 影子模式） | `docs/development/2026-09-15-optimization-backlog.md` |
| A24 | 「**也没有做过性能基准**…**没有多机实测**…『类 Codex 架构更简单』是定性判断，**没有对照实现**。」 | `docs/architecture/why-distributed.md` §8 |

### (b) 推断的 3–6 个更深发展方向

以下每一条都显式标注 **（推断）**，并给出「当前卡在哪一步（源码证据）→ 解决它需要什么 → 可能的验证方式」。

---

#### 方向 1：世界模型的自学习 —— 从「重放观测」到「预测世界」（推断）

**当前卡在哪一步**

- 世界模型今天是**纯确定性 reducer**：`world.snapshot.v1` 由 observation / tool result / device presence / resource lease / task event 投影而来，规则写死（`2026-08-20-distributed-agentos-world-harness-design.md` §9）。
- 没有任何「预测」或「学习」成分。`grep` 全仓没有 world model 的学习路径。
- 而且**连语义记忆都还没有落地**：`2026-09-10-closed-loop-semantic-upgrade-adr.md` ADR-7 明确 `TagMap` 本轮未实现，理由是「仓库没有把 RTAB-Map 关键帧 ID 暴露到观测契约里；在没有真实关键帧标识的情况下建 TagMap，只能用采集序号冒充关键帧，**会把『看起来有据可查』写成事实**」。
- 语义层的**第一个真实实现**是 `map.objects.v1`（`6e3d3d934 语义层升级：物体记忆（map.objects.v1）+ 工作区可达位姿`，09-15），以及 `semantic.recall.v1`（`81b2a3a8a`）。

**解决它需要什么**

1. 先补齐**身份**：观测契约里加入可选的 `keyframeId` 与地图 revision（ADR-7 给出的前置条件）。
2. 再建**持久语义层**，带观测计数、`retired` 软删除、多视角一致性过滤（`docs/development/2026-09-15-optimization-backlog.md` P2-5 的判据是「错误目的地 −80%、召回下降 ≤ 5%、512 实例轮询从 53.9 ms 降到 < 5 ms」）。
3. 然后才有资格谈预测：世界模型的输出从「当前事实」扩展到「下一个观测的概率分布」，并**必须与 Harness 的证据判定分开**——否则预测会污染证据。

**可能的验证方式**

- 用 `docs/architecture/agent-evaluation-system.md` 的 `state` 能力层（「陈旧事实采纳率、状态一致性、更新延迟」，必测失败切片含「乱序、重复、丢包、时钟偏差、重启」）作为标尺；
- 冻结一组真实勘测回放（仓库里已有：`docs/development/2026-09-21-map-visibility-and-inventory.md:163-168` 记录了六张真实地图的覆盖率/面积/点数），对比「有/无预测」在「目标不在视野时的到达成功率」上的差异。

**为什么这条方向的收益最大**：README §五 那句「**尚未实现未知住宅的自主 frontier 探索**」与「返程导航受地图覆盖限制」其实是同一个缺口的两个症状 —— 机器人在**没去过的地方**没有任何可依赖的模型。

---

#### 方向 2：跨机经验的真实闭环 —— 从「同一份代码」到「A 机的经验 B 机受益」（推断）

**当前卡在哪一步**

- `docs/architecture/why-distributed.md` §4.1 把「多机协同、**跨机学习（A 机解决的故障 B 机受益）**」列为分布式路线的优势之一。
- 但**这条优势今天没有任何实现**。仓库里最接近的是三样东西，都不等于跨机学习：
  1. `agentruntime/` 的多 Agent 运行时（task + ops + recovery）—— **同一个进程内**的分工；
  2. `train/` 的门禁（`Compare`/`Gate`）—— 判断一个 checkpoint 值不值得上，**不提供训练循环**；
  3. `training/` 的账本导出（`cmd/training-export`）—— 从**本机**账本导出。
- 更关键的是**训练语料本身还不够**：`docs/development/2026-09-18-system-review-and-improvement-plan.md:600-601` 实测「`negative.jsonl` **0 行**；65 任务 → **15 请求**」；`:606` 记录训练导出「`sft 0 / refusals 6 / negative 0`」。
- 而且账本曾经有 99.3% 是噪声（`:197`：159,314 条事件里 158,193 条由观察者产生，执行事实只有 0.7%）——**在那种账本上做跨机学习，学到的是观察者的行为**。

**解决它需要什么**

1. 阶段 0 已完成（账本只记状态迁移）；**拒绝样本要先被采集**（`:158-176` 明确列为「没有替代来源」的高价值项）。
2. 数据集版本化、脱敏与许可（阶段 3）。
3. 跨机场景下**同一条经验的可用性判据**：机器人的 `robot.profile.v1`、标定 revision、地图 revision 都要匹配才允许迁移 —— 这正是 `docs/architecture/agent-evaluation-system.md` 里 `learning` 能力层要测的「污染与遗忘」。

**可能的验证方式**

- 用 `docs/architecture/agent-evaluation-system.md` §5 的 `Experiment` 契约：「结果出现前决定如何比较」（候选/基线、固定条件、随机化/配对、预算、假设、样本量、检验族、门禁版本）；
- 具体实验：机器 A 的 30 次失败任务 → 导出 → 在机器 B（同 profile、同标定）上跑同样 30 个任务，看成功率差；**对照组是「不做任何迁移」**。

---

#### 方向 3：VLA 与 Agent 层的接口标准化（推断）

**当前卡在哪一步**

- **契约已经在了**：`policy.manifest.v1` / `policy.observation.v1` / `policy.inference.request.v1` / `policy.inference.result.v1`，以及 `policy/sidecar` 的框架无关 HTTP 包装（`2026-08-24-policy-tools-recovery-sim2real-design.md`）。
- **但没有真实的消费者**：`docs/development/2026-09-15-optimization-backlog.md` P3-7「学习型策略的最小闭环（policy sidecar 首个消费者）」的判据是「参考工位 50 次抓取：成功率 +10 个百分点、尝试次数 −20%」，**未完成**；`2026-08-24` 设计文档 §Acceptance gates 第 7 条明确：「A real-robot release remains `NO-GO` until hardware calibration, camera/verifier integration, emergency-stop drill, **signed policy artifact**, physical shadow run, and supervised handoff evidence are attached.」
- 今天实际在跑的「策略」是**确定性仿真**（RoboCasa/MuJoCo 原生语义工具实现），不是学习模型。
- 另一处明确的缺口：`docs/architecture/agent-evaluation-system.md:427`「新模型/工具/workflow/仿真/实机 runner **尚未统一接入**」。

**解决它需要什么**

1. **动作空间标准化**：今天 `action_chunk` 的具体键名由适配器决定，而 `2026-08-21-robocasa-webgl-digital-twin-design.md` §7 已经定义了一组**规范关节键**（`joint.left.rotation` … `joint.head.tilt`，共 14 个），并规定「Console 不解析 MuJoCo 私有名字，也不依赖具体驱动 ID」。**把这组键从「可视化绑定」提升为「策略动作契约」是自然的下一步（推断）**。
2. **观测 bundle 的内容寻址**：`policy.observation.v1` 已经用 `frameRef` + SHA-256 而不是原始字节，这一步已具备。
3. **策略的失败分类要和 Go 侧同源**：`2026-09-11-standard-robot-tool-layer-adr.md` ADR-3 已经处理了这个问题的一侧（「`recoverable` 的判据…取 `core/closedloop` 的分类结论」），并有跨语言一致性测试。

**可能的验证方式**

- `docs/architecture/agent-evaluation-system.md` 的 `tool_execution` 与 `verification` 两层，以及它要求的「每个具体工具至少拆成三个互不替代的结果：**选对工具和参数 → 实现正确执行 → 结果被正确验证**」；
- 具体实验：同一批 50 次抓取，A = 固定控制器，B = 技能策略，**同一份独立判据**（`Holding ∧ ¬On ∧ Stable` 三帧连续）。

---

#### 方向 4：多机共识与跨存储事务（推断）

**当前卡在哪一步**

- 这一条**就是第 8 节的前三条**，此处只补工程视角。
- 今天的一致性模型是**「局部强一致 + 跨组件幂等/补偿」**（`docs/production/architecture.md` §5 末：「而不是假设跨 MySQL、Redis、机器人和物理世界存在一个全局事务」）。
- 具体的技术债已经点名：
  - `docs/development/2026-09-18-system-review-and-improvement-plan.md:462-475`：**云侧一个 2 分钟定时器 `reclaimStaleLocked` 会把任何超期的 RUNNING 节点改回 READY、清空 claim，完全不检查物理动作是否仍在飞**。作者原文：「这正是 `core/closedloop` 花 369 行要防的事，**在 `fleet/` 里被一个定时器放回来了**。」
  - `docs/architecture/distributed-agentos.md:28`：「`WorldHub` 的 `FLEET_WORLD_SNAPSHOT_PATH` 可保存同机单主 checkpoint…它**不提供跨主机共识**。」
- 另一处结构性缺口：`fleet/` **完全不 import `core/closedloop`**（`…improvement-plan.md:306-313`），即云侧的完成判定与本地侧的闭环契约是两套。

**解决它需要什么**

1. 先**让 fleet 用上同一套闭环契约**（这是阶段 0.3「云侧不直写成功」的内容，验收清单 `:823` 当前状态 ⬜）。
2. 删掉或改造那 2 分钟定时器（阶段 0.5，`:825` 当前状态 ⬜）。
3. 然后把 leader fencing 与业务提交放进同一存储事务（第 8 节第 ① 条）。

**可能的验证方式**

- 一条极具体的实验（已写在文档里，`:91`）：「协调器在**交接中途**崩溃…值得单独造一个『**提交后立刻杀协调器**』的用例。」
- 判据：`docs/development/2026-09-18-system-review-and-improvement-plan.md:781`「claim 过期后节点不得回到可执行状态（有测试）」+ `:848`「6.3 租约/原子 fence」。

---

#### 方向 5：长时程任务 —— 从「12 步」到「一个下午」（推断）

**当前卡在哪一步**

- 今天最长的一条已验证链路是 **12 步**（README §四 的 12 行表；v0.6.0 发布记录：「12 个工具步骤全部 `CONFIRMED`…放置验证连续 3 次采样、稳定 0.332 秒」）。
- 长时程会遇到三个今天**没有覆盖**的问题：
  1. **预算耗尽**：探索有 `DEFAULT_EXPLORE_TRAVEL_M`（40 → 75 m）与 30 分钟期限（`e28f1205a` 的对照实验记录「`frame_budget`/30 分钟期限停止，实测 81% 覆盖」）。
  2. **人在不在**：`docs/architecture/why-distributed.md` §2.2 那张对照表里一行写得极准——「人在不在：Codex/Claude Code **一直在**；机器人 Agent 运行时 **常常不在**」。长时程意味着**必须把「人不在」当作常态**，而这正是阶段 4「人到场（EscalationAgent）」要解决的，当前**未开始**（验收清单 `:841-843` 全部 ⬜）。
  3. **经验与记忆的时效**：`docs/development/2026-09-15-optimization-backlog.md` P0-2 已经把「回忆新鲜度成为部署参数」落地（`semantic.recall.v1` 带年龄；`edge/robotclient` 优先用**新鲜**（≤15 分钟）的 vantage），但**这只是一个参数的开始**。

**解决它需要什么**

- `EscalationAgent` + 认领/移交/解决/忽略的工作流（阶段 4）；
- 持久化的尝试预算（阶段 7.1：「**物理写的重试预算必须持久化才有意义**。内存里的尝试计数在进程重启时归零，于是崩溃过的 Agent 会永远重试——正是上限要防的那件事」，`…improvement-plan.md:247-248`）；
- 分级保留策略（阶段 0.8 已做；`docs/development/2026-09-21-map-visibility-and-inventory.md:91` 记录 `artifacts/maps` 从 45 MB 清到 15 MB）。

**可能的验证方式**

- 阶段 1 的判据本身就是长时程的雏形：「三场景各 10 轮，端到端成功率 ≥ 90%」（`:687`）；
- 再往上：24/72 小时长稳（`docs/production/v1-assessment-2026-09-05.md:49`、`:56`）。

---

#### 方向 6：仿真到真机的自动标定（推断）

**当前卡在哪一步**

- **标定今天已经是软件的一部分，但不是自动的**：v0.6.0 交付了「标定页支持注册算法与用户自行录入」；`954ea090d feat: guide a non-technical owner through calibrating the robot`；`fd14881fa feat(console): show the calibration numbers instead of a sentence about them`。
- **有一处明确的"曾经是错的"**：`docs/development/2026-09-14` 的 CHANGELOG 段落标题就是「**检验当前标定：头部相机的位姿一直是错的，而且没有任何测试能发现**」；另有 `beb3c4b2f fix: mount the bottom camera at the bottom, looking forward`（对应 `23ba3cd91 docs: the bottom camera is mounted at the top, and both sensors should be D435i`）—— 即**仿真模型里的相机位姿与真实硬件不一致**。
- 手眼标定已有一次真实求解：`§ 手眼标定真的解出来了：运动学取自 MuJoCo 模型，`AX=XB` 不再拒绝`（`CHANGELOG.md@774bd2a2f` 附近的 09-13/09-14 段落）。
- 还有一条明确的硬约束：「**存档标定与模型矛盾时拒绝启动**，而不是每一帧发布错的相机位姿」（同段 CHANGELOG 标题）。

**解决它需要什么**

1. **标定的来源要可追溯**：`2026-08-21-robocasa-webgl-digital-twin-design.md` §13 规定「实机 proprioception 发布相同规范关节键」+ manifest 注册模型 revision。
2. **仿真模型与真实硬件的偏差要可测量**：`docs/development/mujoco-compatibility.md` 已固定 MuJoCo 3.11.0 并加 3.12 兼容检查，但那是**依赖版本**，不是**几何保真度**。
3. **自动标定的闭环**：`docs/architecture/robot-pairing.md` + `docs/architecture/readiness.md` + `docs/architecture/robot-discovery.md` 已经把「上电就能用」的三块（发现、配对、自检）做出来了（`d4ebc79ad 走向"上电就能用"：零配置发现、可用性自检`），**缺的是标定环节的自动化**。

**可能的验证方式**

- 判据可以是「同一标定流程在仿真与真机上产出可比的数值」：`ea3148db5 feat: one calibration document for a simulated and a real unit` 与 `e58e84d8d feat: run the calibration flow against the simulated robot` 是这条路的既有基础。
- 反面判据同样重要：`docs/development/2026-09-13-system-audit.md:97` 记录过「模拟向导产物被标为 **measured**」是一个真实缺陷 —— **自动标定的第一个敌人是"看起来标好了"**。

---

## 11. 这个项目给行业的启示（第 17 章）

从工程角度提炼 5–8 条可迁移经验，每条都附本仓库的原始证据。

### ① 证据 > 回执（Evidence over acknowledgement）

> 「Tool 成功不等于物体到了目标。」（`docs/development/principles.md:8`）

**可迁移形式**：为每一类副作用定义一个**独立的完成判据**，并且这个判据**不能由执行者提供**。本仓库把它做成了带身份的契约字段（`mutates_world`）+ 时间判据（证据必须晚于命令派发）+ 来源判据（`ObservationID` 与 `ObservedAt` 缺一不可）。

**最有力的证据是本仓库自己的翻车**：`Result.Executed` 无条件为 true，于是「一次工具都没调用」的运行被报成 `executed: true, verified: true`。作者的定性值得整句引用：

> 「**这是本仓库最不该出现的一类错误**：它把『我们什么都没做』说成『机器人动了，而且确认动对了』。」

### ② 分类表 > if 分支（A classification table beats a chain of conditionals）

**可迁移形式**：把「失败之后怎么办」写成**可穷举、可测试的映射表**，而不是散落在调用点上的 `if`。本仓库的 8 类失败（`TRANSIENT` / `PERCEPTION` / `PLANNING` / `PERMISSION` / `RESOURCE` / `VALIDATION` / `UNKNOWN_OUTCOME` / `FATAL`）+ 113 个运行时码的完整归类，使「**只有 Transient 与 Perception 可重试**」成为一条可以被单测钉住的断言。

**两条配套纪律**：

- **默认值的方向**：「未识别的错误码归入 `UnknownOutcome`，而不是猜成可重试——**猜测的方向选的是安全那一侧**。」
- **清单要手工提交，不要自动爬取**：「新增码而没加进清单会落在未分类状态，守卫就会失败——**『未分类』作为默认是正确的，作为意外是危险的**。」

**反面教训也在仓库里**：58 个码曾静默退化成 `UNKNOWN_OUTCOME`，让操作员「去对账一个从未发生的动作」。

### ③ 先有刻度，再谈自训（Build the ruler before the training loop）

> 「**先有刻度，再谈自训。**」（提交 `505af6577` 的标题）
> 「**不在 grounding 修好之前做 RL。** 环境是坏的，reward curve 看起来会像学习，**实际在拟合噪声**。」

**可迁移形式**：在拿到任何"改进"之前，先能**度量**改进。本仓库为此专门造了一整套东西：`latency/`（四段耗时 + p50/p95/p99）、`orchestration/eval/`（自然语言到计划的语义断言）、`orchestration/eval/system_eval/`（20 能力 / 7 层 / 34 指标 / 配对比较）、`train/` 门禁。

**一条极硬的纪律**：

> 「**判卷的人不能改判据。**」（`…improvement-plan.md:807`）
> 「**判据不能由被判者提供。**」（`docs/architecture/post-training-pipeline.md`）

**以及一条诚实的自评**：系统级 Eval 上线时的记录是「新增模型调用 **0**，机器人动作 **0**……它**没有证明新系统本身已变强**」（`docs/architecture/agent-evaluation-system.md:415`）。

### ④ 刻意非目标比功能列表更能说明判断（Declared non-goals reveal judgement）

> 「一个项目的取舍比功能列表更能说明它的判断。」（`README.md` §十）

**可迁移形式**：把非目标写在**最显眼的位置**，并给出**代价**，而不只是给出理由。

本仓库的示范级样本是 `2026-09-11-standard-robot-tool-layer-adr.md` ADR-5：明确不宣称对接 MoveIt 2，并列出了三条可核对的事实（镜像里没有 MoveIt 2、CI 不构建动作客户端、`GripperCommand` 在仓库内不存在），末句给出判据：

> 「声称 MoveIt 2 可用却无法运行，会让整套工具层的可信度归零。」

**更强的版本是「给代价」而不是「给理由」**：`docs/architecture/why-distributed.md` §8「这份文档没证明的事」，三条全部指向自己的论证薄弱处（没有性能基准、没有多机实测、"更简单"是定性判断）。以及 §1.3 主动说出反面：「**一台机器人 + 一个人看着 + 非安全关键 → 三层是过度设计。**」

### ⑤ 让唯一知道答案的那一层开口（Single source of truth for a fact）

**可迁移形式**：当一个事实（「到底有没有真的调用过工具」）只有一个地方真正知道时，**判定必须回到那个地方**，不能在上层从字符串/状态码推断。

本仓库的两次应用：

- `actionloop.Outcome` 增加 `Calls`，在唯一一处真正下发调用的地方自增，执行器改为 `Executed = outcome.Calls > 0`。**并且明确否决了替代方案**：「为什么不在 `recoveryexec` 里从 verdict 字符串推断…那是把派发规则抄第二遍，两份必然漂移。」
- 告警投影的身份剥离「只用一份规则」：「前缀与后缀**在同一个 `rootIdentity` 里剥离**，边界用 `agentcontract.SameAnomaly` 的同一规则，**改动只有一个地方会错**。」

**推论**：当你在两个地方写了同一个判断，你已经有 bug 了，只是还没显形。本仓库把它上升为可执行检查（`tests/architecture/`）。

### ⑥ 「没通电」是一种缺陷，而且可以被测试（Wired vs implemented）

**这是本仓库最独特、最可迁移的一条工程经验。**

现象：有大量能力**实现完整、测试通过、生产环境永不触发**。作者的措辞很克制但很锋利：「规则完整实现，**永远不会触发**」。

具体样本（全部来自 `docs/development/2026-09-18-system-review-and-improvement-plan.md`）：

| 样本 | 表现 |
| --- | --- |
| 慢步骤告警 | 规则完整，但唯一的输入构造点**从不填 `Latency` 字段** → 永不触发 |
| 事故包计时 | 注释承诺 "the console's latency report verbatim"，唯一写入点**从不设置** → `timing` 恒为 `null` |
| 跨进程因果字段 | `observation.Causation` 走 protobuf 线，生产端 `.Causation =` **零命中** |
| 四段耗时遥测 | 采集器在、分位数在、HTTP handler 在，**前端零消费** |
| 闭环契约本身 | 「Gate 在**全仓只有 2 个生产调用点**」 |

**解法**：把「已实现的能力有没有生产数据源」做成**声明式检查表**（`tests/architecture/powered_test.go`），未通电项**会红**。

**为什么这条对行业最有价值**：绝大多数团队的测试覆盖率指标**天然无法发现这类问题** —— 代码被覆盖了，规则被断言了，只是**没有任何生产路径到达它**。这是一个测试覆盖率永远照不到的角落。

### ⑦ 「失败可见」优先于「失败可复现」

**可迁移形式**：当无法复现一个用户报告时，第一步不是再试一次，而是**让这条路径的失败变得可见**。

本仓库的两次应用：

- `134ec1a5f problems 页面卡死报告：让失败可见，并说明我没能复现` —— 直接以「我没能复现」为标题提交，并把不可复现本身记录为事实。
- `336115e7f 问题处理独立成页：横幅负责有事找你，页面负责怎么办` —— 把"通知"和"处置"拆开。
- 最终真相由 `774bd2a2f` 找到（轮询比端点响应还快），但**第一次报告与真相之间隔了三次提交**，而这三次提交都没有假装解决了问题。

**更硬的一条纪律是"如实说没修"**：

> 「**没修的那一半才是根治**…所以现在的状态必须说清楚：控制台不会再被自己堵死，但首屏仍然很慢。」

### ⑧ 历史文档不改写，删除要留痕（Design history is an asset）

> 「Architecture simplification must not delete them merely because the corresponding runtime is removed.」（`2026-08-18-local-first-runtime-design.md` §16）

**可迁移形式**：设计文档是**版本化的产品资产**，不是过程垃圾。被取代的文档：① 加可见的状态说明与替代者链接；② **历史内容保留**；③ 新的协议/安全决策在实现之前记录。

**本仓库的执行细节值得抄**：

- `docs/superpowers/` 只保留 `specs/`（**决策记录**），删除 `plans/`（**过程清单**），并在被删文档里加**带日期的状态说明**而不是重写它：「状态更新（2026-09-12）：发布树不再分发 `plans/`，只保留 `specs/`。上面这条描述的是当时的约定，不再描述当前仓库内容。」
- 一个更漂亮的样本：一份文档用 `scan-3b9d237aaef8` 作为证据，后来那张图被删了，处理方式是「那份文档的陈述**保持原样（写下时是真的）**，删除这件事记在这里而不是改写历史记录」（`docs/development/2026-09-21-map-visibility-and-inventory.md:173-175`）。
- 今天仍然有效的一个实例：`2026-09-10-closed-loop-semantic-upgrade-adr.md` 的 ADR-4 描述了 `Track` 重试状态机，而 ADR-10（2026-09-21 追加）明确写「上述 ADR-4 的 `Track` 重试状态机**后来已移除**…不能把本次实验里的重试策略描述成现有 Agent 的自动恢复能力」。**旧的错描述留着，新的纠正也留着，读者自己能看到演进。**

---

# 源码索引

## 引用的 commit hash

| hash | 日期 | 标题（原文） |
| --- | --- | --- |
| `ed20bd310` | 2026-08-17 16:35 | chore: initialize robot agent repository |
| `e0c801c66` | 2026-08-17 16:37 | feat: define robot and control plane protocols |
| `0bae97a8d` | 2026-08-17 16:49 | feat: add cloud task control plane |
| `225463914` | 2026-08-17 16:58 | feat: add raspberry pi robot gateway and safety supervisor |
| `b872c686f` | 2026-08-17 17:05 | feat: add fail-closed xlerobot adapter |
| `f885b141e` | 2026-08-17 17:28 | release: complete robot agent v0.1 simulation baseline |
| `4cc5cf56a` | 2026-08-18 16:39 | docs: design local-first robot runtime |
| `8cdebd64e` | 2026-08-18 16:43 | docs: plan local-first runtime migration |
| `0cbfdc562` | 2026-08-18 17:07 | refactor: simplify deployment to local first |
| `61c79a72c` | 2026-08-18 17:27 | refactor: complete local-first robot agent architecture |
| `1429295ab` | 2026-08-18 17:35 | docs: design layered runtime and middleware ports |
| `db5e58518` | 2026-08-18 17:41 | feat: add pluggable middleware contracts |
| `32033057f` | 2026-08-18 17:53 | refactor: decouple robot runtime from protobuf |
| `547d8570c` | 2026-08-18 18:02 | docs: finalize layered robot agent architecture |
| `01fa6e955` | 2026-08-19 01:25 | fix: stabilize xlerobot task scene |
| `720d0a192` | 2026-08-19 01:33 | fix: keep xlerobot task goals within single-arm reach |
| `9e163d8f0` | 2026-08-19 02:26 | fix: approach grasp targets before attachment |
| `5f8c649b2` | 2026-08-19 03:38 | fix: serialize rendering and placement commits |
| `d505f5a2e` | 2026-08-19 04:59 | fix: fail closed on stale scene frames |
| `508b1dabe` | 2026-08-19 20:35 | feat: add pure distributed AgentOS brain isolation and multi-robot graph runtime |
| `0a1016f57` | 2026-08-19 22:27 | feat: add Alibaba Cloud one-click Fleet control plane |
| `c5314e26d` | 2026-08-20 20:58 | docs: design distributed robot world harness |
| `c74cdfa03` | 2026-08-21 01:17 | docs: design RoboCasa dual XLeRobot harness |
| `b6743a921` | 2026-08-21 09:07 | docs: design RoboCasa WebGL digital twin |
| `3bcb4da90` | 2026-08-22 09:55 | fix: close RoboCasa acceptance trust races |
| `2f88ad74d` | 2026-08-22 20:27 | test: bind RoboCasa acceptance to portable frontend |
| `970172f3d` | 2026-08-22 21:32 | docs: design versioned task experience |
| `e13a4aeda` | 2026-08-22 21:47 | feat: define immutable task revisions |
| `785ca5a0e` | 2026-08-22 21:56 | feat: persist task revisions atomically |
| `e09065292` | 2026-08-22 22:19 | feat: reconcile task revisions at safe checkpoints |
| `70ce8feaa` | 2026-08-23 00:32 | feat: support live versioned robot task updates |
| `fa1ef13b9` | 2026-08-23 00:43 | docs: deliver Robot AgentOS production manuals |
| `d1127eaea` | 2026-08-23 08:36 | chore: prepare v0.2.0-rc.1 |
| `696791d12` | 2026-08-23 23:40 | ci: use egl for headless mujoco rendering |
| `ec147ad8c` | 2026-08-23 23:42 | ci: use osmesa on cpu runners |
| `efbf6a543` | 2026-08-24 01:24 | feat: define policy tool contracts |
| `1208d7441` | 2026-08-24 01:26 | feat: add policy inference providers |
| `c00c6c316` | 2026-08-24 01:32 | feat: prepare policy actions at the edge |
| `78813871b` | 2026-08-24 01:47 | feat: add framework-neutral policy sidecar |
| `c1cf62ccd` | 2026-09-06 22:32 | feat: harden V1 robot agent and refresh console and documentation |
| `7332976a8` | 2026-09-07 00:19 | feat: add heterogeneous robot adapters and unified MCP tools |
| `2e841a9c3` | 2026-09-08 20:20 | feat: add RGB-D single-robot loop and durable task recovery |
| `f4dbd307a` | 2026-09-08 23:58 | feat: integrate dual RGB-D RTAB-Map navigation and stable console views |
| `6a0349d33` | 2026-09-09 03:13 | feat: release v0.2 verified single-robot navigation loop |
| `ab6936d83` | 2026-09-10 21:41 | feat: add household mobile manipulation harness |
| `42e31df10` | 2026-09-11 00:56 | feat: enforce closed-loop contract for physical write tools (v0.3.0) |
| `46ccbc4e7` | 2026-09-11 21:36 | feat: add standard robot tool layer and task replay (v0.4.0) |
| `1c7496edf` | 2026-09-11 22:37 | feat: make task replay reachable and self-explaining (v0.5.0) |
| `ccfdbeedd` | 2026-09-11 23:32 | refactor: classify the repository by deployment target |
| `cddb5bb46` | 2026-09-12 00:43 | chore: ship decision records, not agent scratch and plans |
| `ea3148db5` | 2026-09-12 01:21 | feat: one calibration document for a simulated and a real unit |
| `954ea090d` | 2026-09-12 01:39 | feat: guide a non-technical owner through calibrating the robot |
| `fd14881fa` | 2026-09-13 06:52 | feat(console): show the calibration numbers instead of a sentence about them |
| `beb3c4b2f` | 2026-09-13 00:19 | fix: mount the bottom camera at the bottom, looking forward |
| `e58e84d8d` | 2026-09-13 06:40 | feat: run the calibration flow against the simulated robot |
| `895bfc3fe` | 2026-09-13 12:41 | feat: complete registered robot calibration, SLAM and manipulation workflows |
| `c7e7ab530` | 2026-09-13 17:19 | feat: furnish household tasks and expose SLAM keyframe diagnostics |
| `e901b6f35` | 2026-09-13 20:05 | chore: release v0.6.0 |
| `8607c7a21` | 2026-09-14 19:02 | 修复控制台关键帧面板：对每一张真实地图都报"格式错误" |
| `6e3d3d934` | 2026-09-15 00:34 | 语义层升级：物体记忆（map.objects.v1）+ 工作区可达位姿 |
| `81b2a3a8a` | 2026-09-15 12:23 | P0-1 上机性能遥测 + P0-2 目的地改用"上次看到它的地方" |
| `e28f1205a` | 2026-09-15 18:03 | SLAM 探索：门不是瓶颈，让机器人真的能扫完一整个家（38.9% → 81%） |
| `ee7c0a2f0` | 2026-09-15 22:56 | 机器人异常处理审计：并修掉"故障丢弃整张地图" |
| `5b0d857b6` | 2026-09-16 00:24 | AI 回溯诊断：incident.v1 记录 + 确定性故障族分类 |
| `ec0e5b8b8` | 2026-09-16 00:45 | 异常终态自动产出事故记录 + 自动扫掠分类 |
| `0c1a9fecb` | 2026-09-16 11:35 | 自动恢复引擎 + 随时定位的系统体检 |
| `88cf9f7ce` | 2026-09-17 08:13 | Agent 层升级为可扩展多 Agent 运行时（当前启用 task + ops） |
| `c8cae5af2` | 2026-09-17 08:13 | 分类表补齐 113 个运行时故障码 |
| `6b9c9550e` | 2026-09-17 12:17 | 恢复 Agent：只提议不执行的第三类 Agent |
| `d4ebc79ad` | 2026-09-17 12:36 | 走向"上电就能用"：零配置发现、可用性自检 |
| `34ef20321` | 2026-09-17 23:20 | 控制台可批准执行恢复步骤，并修掉"没执行却报已执行"的假声称 |
| `1f6b72e27` | 2026-09-18 08:15 | 接入模型：自然语言任务分解走通；修掉两个永远不成立的检查 |
| `505af6577` | 2026-09-18 19:16 | 编排层评测体系：先有刻度，再谈自训 |
| `21e780f48` | 2026-09-18 19:47 | 后训练流水线：不用 TrainAgent，用一条确定的流水线 |
| `0774468f1` | 2026-09-18 20:12 | 问题列表从 279 条报告变成 7 个问题；修掉三处身份缺陷 |
| `336115e7f` | 2026-09-18 20:59 | 问题处理独立成页 |
| `134ec1a5f` | 2026-09-18 21:07 | problems 页面卡死报告：让失败可见，并说明我没能复现 |
| `f9f50febd` | 2026-09-18 23:08 | 新增 docs/architecture/why-distributed.md |
| `774bd2a2f` | 2026-09-19 00:18 | 找到控制台卡死的真凶：轮询比端点响应还快 ← **CHANGELOG 全文快照点（3,282 行）** |
| `8b9683be8` | 2026-09-21 | feat: prepare v0.7.0 grounded runtime and system evaluation ← **当前 HEAD** |

## 引用的 文件:行号

### 设计决策（`docs/superpowers/specs/`，15 篇，全部按时间读了）

| 文件 | 引用位置 |
| --- | --- |
| `2026-08-17-role-based-installation-design.md` | §2 四角色表；§3 install.sh 唯一入口；§8「There is no STM32 installer」；§12 release boundary |
| `2026-08-18-layered-runtime-middleware-design.md` | §2 七条层违规；§9 `go list -json` 机械检查；§11 验收条件；§12 历史不可擦除 |
| `2026-08-18-local-first-runtime-design.md` | §1 目标；§3 Non-Goals；§11 失败语义（远程急停 reset 禁止）；§13 删除/复用清单；§16 设计文档是产品资产；§17 交付序列 |
| `2026-08-19-xlerobot-observable-sim-training-design.md` | §3 Included/Excluded；§4 模型来源与保真度 |
| `2026-08-20-distributed-agentos-world-harness-design.md` | §2.1 基线证据；§2.2 当前不足（六条）；§2.3 创新判断；§8 ObservationEnvelope 规则；§9 World Projector；§10 持久化协调；§11 核心不变量（7 条）；§13 异常注入矩阵（9 类） |
| `2026-08-21-robocasa-dual-xlerobot-harness-design.md` | §3 方案选择；§9 Harness Agent 输出四态；§10 故障清单（10 类） |
| `2026-08-21-robocasa-webgl-digital-twin-design.md` | §3 四方案比较；§4「GLB 决定长什么样，WorldSnapshot 决定在哪里」；§7 规范关节键；§13 实机迁移 |
| `2026-08-22-versioned-task-experience-design.md` | Goals/Non-goals；Domain model（TaskRevision 六态）；Reconciliation 八条规则；Harness 三事实分离 |
| `2026-08-24-policy-tools-recovery-sim2real-design.md` | Considered approaches（三选一）；Policy manifest；Recovery model 六类；Acceptance gates 第 7 条；Production truthfulness |
| `2026-09-06-heterogeneous-robots-design.md` | 方案与边界（`robot.profile.v1` / `scene.reconstruction.v1`）；验收 |
| `2026-09-08-single-robot-rgbd-design.md` | 验收边界（不以模拟器实体位置代替环境感知）；模块清单 |
| `2026-09-10-closed-loop-semantic-upgrade-adr.md` | 背景（7 项已有能力 + 5 个真实缺口）；ADR-1 `MutatesWorld` 与被否决方案；ADR-2 证据缺失 vs 验证失败；ADR-3 TagMap 选型与被否决方案；ADR-4 分类与上限；ADR-5 主动感知边界；ADR-6 ground-truth 运行时；ADR-7 范围裁剪三条理由；ADR-8 身份与时间精度；ADR-10 现状校正（`Track` 已移除）+ GCL 格式选择表 + 谓词表 + 三值逻辑 |
| `2026-09-10-home-mobile-manipulation.md` | 任务示例（12 步图）；约束 |
| `2026-09-11-standard-robot-tool-layer-adr.md` | 背景既有事实表 + 「不存在需要另起炉灶的缺口」；ADR-1 不新增执行通道；ADR-2 能力集/动作集分离；ADR-3 错误码投影；ADR-4 语义位置注册表；ADR-5 不宣称 MoveIt 2；验收映射表 |
| `2026-09-13-general-robot-system-design.md` | 已核对的边界；实施顺序 5 条；验收 |

### 架构文档（`docs/architecture/`）

| 文件 | 引用位置 |
| --- | --- |
| `distributed-agentos.md` | `:5-25` 两条边界；`:27-31` 持久化与单写范围；**`:33-39` 仍须独立验证的范围（五条）** |
| `why-distributed.md` | `§0` 三句话；`§1.1` 三条硬约束表；`§1.2` 每层存在理由；`§1.3` 反面同样成立；`§2.2` 与 Codex 对照表；`§2.3` 重试的代价；`§3` 真正有价值的三样；`§5` 本地部署检查表；`§6` 关于「OS」这个词；`§7` 判断表；`§8` 这份文档没证明的事 |
| `multi-robot.md` | 已实现的边界；共享资源交接序列；当前范围与扩展 |
| `agent-evaluation-system.md` | `:6` 状态；`:21` 增量审计表；`§2` 七层评测表；`§3` 二十项能力表；`§4` 工具层十二类；`§5` 六个数据对象；`§12.2` 覆盖视图边界（`:413`）；**`§12.3` `:417-430` 实现边界与下一步** |
| `post-training-pipeline.md` | `五、下一步该做什么（按价值排序）`（6 条）；`六、一句话`（「判据不能由被判者提供」） |
| `llm-driven-execution.md` | `:145-158` 证明/未证明 + 接进 runner 的三项要求；`十、不做什么`（4 条） |
| `review-agent.md` | `:130` 五段闭环两段未实现；`§8` `closedloop.Track` 已删除 |
| `supervision-verification.md` | `:199`「软件不代按实体急停」；`:307` 舰队级监督未接 `edge-worker` |
| `robot-discovery.md` | `:105` 没有 mDNS/DNS-SD |
| `readiness.md` | `:76` 故障处理的句子用机器人自己写的；`:93-107` `UNKNOWN_OUTCOME` 无清除路径 |
| `module-health-and-faults.md` | `:198` 落地状态：尚未全部接线 |

### 开发与生产文档

| 文件 | 引用位置 |
| --- | --- |
| `docs/development/principles.md` | **`:5-18` 八条必须保持的原则**（`:7` 模型只提意图 / `:8` 需要环境证据 / `:9` 同一 Runtime 契约 / `:12` 核心依赖接口 / `:13` 重试以物理结果为边界 / `:14` 版本与身份 / `:15` 界面只呈现可信状态 / `:16` 证据与结论绑定版本 / `:18` 部署归属另见）；**`:20-49` 源码地图表**；`:22` 部署归属是另一个问题 |
| `docs/operations/deployment.md` | `§1` 三个运行位置表；**`§2` `:33-60` 源码目录归属表**（含 `train/` 的定位）；`§3` 云端进程表 |
| `docs/production/architecture.md` | `§1` 目标与四条非目标；`§2` 两种部署形态图；`§3` 模块职责表；`§4` 权威数据流；`§5` 数据所有权与一致性表（末句「局部强一致 + 跨组件幂等/补偿」）；`§6` 安全边界；`§7` 扩展与成熟度；`§8` 学习型工具执行边界 |
| `docs/development/2026-09-18-system-review-and-improvement-plan.md` | `:46-78` 无效测试与断言改造；`:91` 协调器交接中途崩溃用例；`:104-105` 未写的 CI workflow；`:133-142` `GOAL_NOT_CLEAR` 三条修法未决策；`:144-153` 两个生产端两套约定；`:155-157` 下一步；`:158-176` 训练语料现状；`:161-166` 阶段 0 判据与实测；`:178-181` 遗留缺口；`:197-220` 账本/遥测五处未通电；`:230-237` 分类表与默认方向；`:247-259` 尝试预算/权限门控/冻结恢复建议/观察者无执行端口；`:278-287` 恢复目录分级；`:300-313` Gate 只有 2 个生产调用点；`:365-390` grounding 先于计划；`:394-436` 观察者风暴与 CI 门禁；`:440-509` 组三安全阻断；`:462-475` 2 分钟定时器；`:517-618` 组四能力缺口；`:600-601` 拒绝样本 0 行；`:629-673` 阶段 0 判据；`:678-799` 阶段 1–7 与判据；`:806-811` 三条禁止；`:819-852` 一页纸验收清单；`:881-897` 令牌发现规则错两次；`:899-922` 负载 22.6 与两次误归因；`:930-939` 模块表 |
| `docs/development/2026-09-15-optimization-backlog.md` | `:4` 不做框架级重构；`:14-20` 基线数字；`:39-44` S4/S5；`:50` 四段式结构；`:52-67` P0-1；`:69-80` P0-2；`:82-92` P1-3；`:94-104` P1-4；`:106-116` P2-5；`:118-129` P2-6；`:131-140` P3-7；`:142-151` P3-8；`:158` 排序理由；`:164` 明确不做；`:166` 新增的准入问题 |
| `docs/development/2026-09-15-robot-fault-handling-audit.md` | `:5` 测试计数；`:13-16` 行为正确性与失败关闭；`:24` 断言拿取 0 次；`:32-34` 深度不足/什么都没测到；`:41-43` 云侧 10 类故障；`:58` 10 通过 2 跳过；`:76-83` 74% 地图未保存与部分成果；`:89-92` 四条应优先补的测试 |
| `docs/development/2026-09-13-system-audit.md` | `:3` 基线提交；`:37-51` 身份冲突与能力组；`:63` 需增加真实模型评测集；`:83-91` 只读位姿/地图查看与激活独立；`:97` 相机视锥与 measured 标注；`:101-112` RoboMaker/默认 spawn；`:126` `workspaces.json`；`:133-155` 验收记录与测试计数 |
| `docs/development/2026-09-19-agent-initiated-mapping.md` | `:8-24` 工具面 28 个无建图工具；`:44-52` 决策规则七条分支；`:60` 损坏包跳过；`:71` decision 返回而非抛出；`:91-100` 验证；`:108-112` 五条没做的部分 |
| `docs/development/2026-09-21-map-visibility-and-inventory.md` | `:4-7` 62% vs 99.5% 分母；`:14-41` 四处只找一层；`:43-49` 400/404 合并；`:53-70` 三种"没有活跃地图"；`:91` 45 MB → 15 MB；`:103-105` 自动选择与缺字段；`:109-136` 令牌身份；`:154-159` survey/explore 实测；`:163-171` 六张地图表与对比；`:173-175` 历史陈述保持原样；`:184-191` 验证与新增测试；`:199-212` 六条没做的 |
| `docs/production/v1-assessment-2026-09-05.md` | `:16` 18 个对象目标组合；`:29-38` 六条缺陷；`:44-49` 实机上线阻塞与 30 次试验；`:56` 24/72 小时；`:63-64` 拒绝是正确的防失配行为；`:68-92` 环境、测试计数、未执行项、专项结果不相加 |
| `docs/production/v1-release-status.md` | `:5` 较早记录不代表最新源码；`:11` 门禁语义；`:32` MCP 8 工具；`:49-53` 视觉与短时 UI 验收边界；`:57-70` 各轮测试计数与运行限制；`:74-78` 点云抽样与历史结果边界；`:101-107` 各轮计数与不把本地目录当签名证据；`:113-130` 历史失败与另一路线；`:136-171` 固定 13 项与后续功能清单 |
| `CHANGELOG.md@774bd2a2f` | `:7-48` 控制台卡死；`:60-73` 控制台会话；`:74-154` why-distributed；`:155-322` 令牌回归；`:323-408` 阶段 0 收尾；`:404` 阶段 1–7 未开始；`:409-481` 阶段 0 收尾 + 阶段 1；`:473-475` 阶段 1 判据未达成；`:482-611` 阶段 0 止血；`:612-674` problems 页面；`:675-726` 问题处理独立成页；`:727-800` 三处身份缺陷；`:801-879` 编排层拿到世界状态；`:880-1036` 后训练流水线；`:1037-1122` 编排层评测体系；`:1123-1212` 两个永远不成立的检查；`:1213-1305` 两处"替别人说话"；`:1306-1413` 自动恢复；`:1414-1456` 假声称；`:1457-1530` 恢复执行通道；`:1531-1569` 三项决策；`:1570-1724` 一键配对与上电就能用；`:1725-1757` 分类表系统性审计（83 个码 / 58 个静默退化）；`:1758-1780` Review Agent 文档资产；`:2315-2333` SLAM 探索 38.9% → 81% 与 74% 崩溃点；`:2334-2392` P0-1/P0-2；`:3282` 全文行数 |
| `CHANGELOG.md`（HEAD，200 行） | `:9-19` v0.7.0 摘要 |
| `README.md` | `§一` 闭环契约与 GVF；`§二` 分布式=故障假设表 + 两条形态图 + 边界声明；`§三` 工具层三行表 + 28 个工具/5 级安全标注；`§四` 12 行验证表（含返程 ⚠️）；`§五` 建图顺序 + 「尚未实现未知住宅的自主 frontier 探索」；`§六` 软件发布与实机放行是两项独立结论；`§七` 日常使用；`§八` 仓库里有什么；`§九` 开发与验证；**`§十` `:283-291` 五条刻意非目标**；`其他路线`；`LICENSE 行`（文档 123 篇 / 1079 + 1656 测试函数）；`Agent 上下文评测` |
| `docs/releases/v0.2.0.md` … `v0.7.0.md` | 见第 3 节表格 |
| `artifacts/marketing/README.md` | 目录结构；三期状态表；三平台写法差异；「所有能力说法必须先过各期的 `素材来源与数字出处.md` 才能发」 |
| `artifacts/marketing/00-选题与索引.md` | 总标题；发表状态表（01–03 已发，04–10 待写）；短文连载 S01–S10 |

### 源码

| 文件 | 引用位置 |
| --- | --- |
| `tests/architecture/dependencies_test.go` | `TestCorePackagesDoNotImportConcreteInfrastructure`（包集清单）；`TestAgentRuntimeDoesNotImportConcreteAgents`（含原文注释）；`TestAgentContractHasNoInternalDependencies` |
| `tests/architecture/powered_test.go` | 已实现能力的通电审计检查表 |
| `core/closedloop/closedloop.go` | `Gate` / `Classify` / 失败分类（README §一 引用路径） |
| `core/skills/manifest.go` | `MutatesWorld` 字段（ADR-1） |
| `edge/agent/runner.go` | 写工具门禁（ADR-2）；`:224-226` grounding 先于计划；`:618-632` `Manifest \|\| RuntimeMutatesWorld`；`:700` `"FRESH"` 初值 |
| `edge/robotclient/client.go` | `:331` `Observe(streams=["entities","reconstruction"])`（grounding 的第二条路径） |
| `edge/worker/` | `tasksource.go` 导入 `fleet/redis`；`worker.go` 导入 `fleet/coordinator` |
| `edge/cloudclient/client.go` | `:22-23` 导入 `fleet/coordinator` + `fleet/telemetry` |
| `fleet/coordinator/coordinator.go` | `:237-258` `reclaimStaleLocked`；`:571` `sharedHandoff` 分支；`:596` 硬编码 `EntityID:"red-block"`；`:603` 无条件 `StatusSucceeded` |
| `fleet/worldhub/` | 只导入 `core/observation` + `core/worldmodel` |
| `internal/localapp/` | 非测试依赖中 **0 个 fleet 包** |
| `cmd/local-agent/main.go` | `:31` 导入 `fleet/worldhub`（唯一的直接 fleet 导入）；`:78` `--listen` / `LOCAL_LISTEN` |
| `internal/recoveryexec/tools.go` | `:92` `MutatesWorld: service.GetMutatesWorld()`（自我豁免）；`:297` `"FRESH"` 初值 |
| `internal/actionloop/loop.go` | `:569` 构造 Declaration；`Outcome.Calls` |
| `console/server.go` | `:1` 包注释 loopback-only；`:118` `Handler()` 只挂 CSP；`:162-164` latency handler；`:335` `time.NewTicker(200ms)` |
| `console/recovery_execute.go` | `:31-33` 「The local console has no authentication」；`:114-117` 硬编码 `OperatorApproved: true` |
| `console/maps.go` | 地图发现与符号链接围栏 |
| `web/app.js` | `setInterval(refreshAgentAlerts, 5000)`；`:2018-2026` 世界轮询 |
| `web/task_trace.js` | `buildTaskTrace` / `renderTaskTraceNodes` / `renderTaskTrace` |
| `robot/gateway/tangying_robot_gateway/safety.py` | 唯一否决点（急停锁存、租约、profile、参数校验、journal） |
| `robot/gateway/tangying_robot_gateway/contracts.py` | `TOOL_PARAMETERS`（12 个 Pydantic 模型）；`mutates_world()` 单一真源 |
| `robot/gateway/tangying_robot_gateway/runtime.py` | 传输中立模型（`Capability` / `RuntimeInfo` / `SemanticState` / `Command` / `Result`） |
| `policy/sidecar/` | Python VLA/IL/RL HTTP 包装 |
| `Makefile` | `:4` `GO_TEST_PACKAGES`（15 个路径模式）；`:26-48` test-go / test-python / test-web / test |
| `.github/workflows/ci.yml` | 当前 CI 跑的验收脚本（与 `scripts/run_home_task_suite.py` 的差异见改进计划 `:424-436`） |

### 环境与工具链（取证用）

| 项 | 值 |
| --- | --- |
| Go | 1.26.2（`docs/production/v1-assessment-2026-09-05.md:68-69`） |
| Python | 3.11.9 |
| Node | 24.14.1 |
| MuJoCo | 3.11.0（生产锁定）；3.12 兼容检查 |
| `make test` 的 Go 包集 | 65 个包 |
| `go list ./...` | 72 个包 |
| 本文件用到的临时 cache 变量 | `GOCACHE=$PWD/.gocache GOMODCACHE=$PWD/.gomodcache GOPATH=$PWD/.gopath` |

---

## 附 · 给后续写作者的三条提醒

1. **CHANGELOG 的全文在 `774bd2a2f`，不是 HEAD。** HEAD 的 `CHANGELOG.md` 只有 200 行的版本摘要。所有 09-19 之前的技术细节叙事都要用 `git show 774bd2a2f:CHANGELOG.md` 取。取数命令：`git show 774bd2a2f:CHANGELOG.md | sed -n '1414,1456p'`。
2. **本仓库的每一个「已验证」都带适用范围。** 引用任何测试数字或精度数字时，必须同时给出：**日期、命令/脚本、报告位置、未覆盖项**。这是仓库自己定的规矩（`docs/development/principles.md:56-70` 的「文档与变更一起更新」表最后一行：「测试结果与发布范围｜V1 状态、Changelog｜标明日期、命令、报告位置和未覆盖项」）。
3. **两处 README 数字（1079 / 1656 个测试函数）既不等于 HEAD 也不等于工作区**（实测 HEAD 1,040 / 1,370；工作区 1,183 / 1,726）。写进书里请用本文件第 7 节的实测值并注明取数日期。
