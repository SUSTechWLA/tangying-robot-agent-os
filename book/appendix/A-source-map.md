# 附录 A · 源码地图与实测数据

> 本附录给出**原书写作时**每一类东西放在哪个目录，以及当时实测到的规模数字（附测量命令）。2026-09-25 云边升级新增 `internal/agentharness`、`internal/modelroute`、`deploy/edge-orin` 和统一 Agent 镜像；现行角色与证据见[第 17 章](../chapters/ch17-cloud-edge-agent-harness.md)。以下历史数字不代表当前 HEAD。
>
> **原则：凡是引用一个"数量"的地方，都应该同时给出测量它的命令。**
> 否则那个数字在下一个提交后就开始腐烂。

---

## A.1 仓库地图：每类东西放在哪

| 目录 | 放什么 | **会被提交吗** |
| --- | --- | --- |
| `docs/` | **人读的文档**：`install/`（装）、`guides/`（用）、`architecture/`（原理）、`development/`（开发与逐轮升级记录）、**`experiments/`（有完整论文结构的实验报告）**、`operations/`（放行与安全）、`production/`（当前状态与契约）、`releases/`（发布身份）、`sim2real/`、`frontend/` | 是 |
| `artifacts/` | **机器产出的证据**：地图、标定、验收记录、基准报告、事故记录（`incidents/`）。大多数被 `.gitignore` 排除，只有**结论性小文件**入库 | **部分** |
| `artifacts/marketing/` | **对外宣传材料**，按"一期一目录"组织，**不属于产品构建，也不参与测试** | 是 |
| `core/` `agent/` `orchestration/` `tasks/` `middleware/` | **共享运行时代码**（两条部署形态都用） | 是 |
| `edge/` `fleet/` | 边缘运行时与控制面 | 是 |
| `robot/` `sim/` | 机器人网关与仿真后端 | 是 |
| `console/` `web/` | 本地控制台 API 与前端 | 是 |
| `internal/` `cmd/` | 装配与命令行入口 | 是 |
| `proto/` | **跨语言契约**（`robot.v1`、`world.snapshot.v1`、`scene.reconstruction.v1`、`robot.profile.v1`） | 是 |
| `scripts/` | 可执行工具：评测、验收、诊断 | 是 |
| `tests/` | 单元、e2e、故障矩阵与**文档校验**（含链接检查） | 是 |
| `artifacts/training/` | 训练产物与检查点；策略以 manifest + 权重哈希登记 | **否（忽略）** |
| `XLeRobot/` `datasets/` `vendor/` | **上游或参考代码**——先确认所属仓库和版本，**不在其中绕过系统接口** | 部分 |

### 判断一个文件该放哪

| 文件的来源 | 放哪 |
| --- | --- |
| **能被脚本重新生成**（地图、点云、基准、事故记录） | `artifacts/` |
| **人写给人看**（指南、设计、宣传） | `docs/` 或 `artifacts/marketing/` |
| **接口约定** | `proto/` |
| 其余 | 按代码模块归属 |

### `docs/development/` 与 `docs/experiments/` 的分工

| 目录 | 回答什么 |
| --- | --- |
| `development/` | **"当时怎么想的、改了什么"** |
| `experiments/` | **"测了什么、数字是多少、能得出什么结论"** |

**只有结论没有对照数据的，属于 `development/`。**

---

## A.2 代码规模（实测）

### 测量命令

```bash
cd /Users/wanglian/Projects/tangying-robot-agent-os

# Go
find . -name "*.go" -not -path "./.git/*" -not -path "./vendor/*" \
  -not -path "./.gocache/*" -not -path "./.gomodcache/*" | wc -l
find . -name "*.go" -not -path "./.git/*" -not -path "./vendor/*" \
  -not -path "./.gocache/*" -not -path "./.gomodcache/*" -print0 | xargs -0 cat 2>/dev/null | wc -l
find . -name "*.go" -not -path "./.git/*" -not -path "./vendor/*" \
  -not -path "./.gomodcache/*" | xargs -n1 dirname 2>/dev/null | sort -u | wc -l
grep -rhI --include="*.go" -E "^func (Test|Benchmark)" \
  --exclude-dir=.git --exclude-dir=vendor --exclude-dir=.gomodcache . | wc -l

# Python（排除 artifacts 与 XLeRobot 里的代码快照）
find . -name "*.py" -not -path "./.git/*" -not -path "./.venv/*" \
  -not -path "*/node_modules/*" -not -path "./.gomodcache/*" \
  -not -path "./artifacts/*" -not -path "./XLeRobot/*" -print0 | \
  xargs -0 cat 2>/dev/null | wc -l
grep -rhI --include="*.py" -E "def test_[a-zA-Z0-9_]+\(" \
  --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git \
  --exclude-dir=vendor --exclude-dir=.gomodcache . | wc -l

# 文档
find docs -name "*.md" | wc -l

# 提交
git rev-list --count HEAD
```

### 结果（v0.7.0 工作区）

| 项 | 值 |
| --- | --- |
| 提交数 | **328** |
| 时间跨度 | 2026-08-17 → 2026-09-21（36 天） |
| **Go 文件** | **562** |
| **Go 行数** | **118,181** |
| **Go 包** | **144** |
| **Go 测试函数** | **1,183** |
| **Python 手写文件** | **436** |
| **Python 行数** | **109,491** |
| **Python 测试函数** | **1,735** |
| Web JS | 约 **18,364** 行 |
| **`docs/` 下 Markdown** | **144 篇** |
| 发布标签 | 9 个（`v0.1.0-rc.1` → `v0.6.0`） |

### ⚠️ 不要抄 README 的测试数字

| 口径 | README | HEAD 实测 | **工作区实测** |
| --- | --- | --- | --- |
| Go 测试函数 | 1,079 | **1,040** | **1,183** |
| Python 测试函数 | 1,656 | **1,370** | **1,735** |
| `docs/` 篇数 | 123 | **124** | **144** |

**差异来自两处**：

1. **统计口径**（是否算 `Benchmark`、是否排除 `artifacts/` 下的代码快照）；
2. **工作区是否有未提交的新测试**。

**规律：README 里的数字既不等于 HEAD 也不等于工作区——它们指向某个中间快照。**

---

## A.3 逐日提交量

这张表本身就是一份项目史。

| 日期 | 提交数 | 当日性质 |
| --- | --- | --- |
| 08-17 | **24** | 从空仓库到 v0.1 rc |
| 08-18 | 17 | local-first + 分层中间件**两次重构** |
| 08-19 | 33 | XLeRobot 可观测仿真训练 |
| **08-20** | **2** | **只有两笔：分布式世界模型 harness 的设计 + 计划** |
| 08-21 | 13 | RoboCasa 双机 + WebGL 数字孪生 |
| 08-22 | **44** | 全天最密（含 12 笔"验收证据的信任边界"修复） |
| 08-23 | 19 | 版本化任务体验 + 生产手册 + CI 收口 |
| 08-24 | 25 | Policy sidecar，v0.2.0-rc.2 |
| **08-25 → 09-05** | **0** | **空档 12 天** |
| 09-06 → 09-13 | 1/1/3/10/4/7/33/23 | 单机器人主线重启期（**8 天打了 5 个版本标签**） |
| 09-14 → 09-19 | 11/16/10/19/11/1 | 运维 / 评测 / 恢复期 |
| 09-21 | 1 | v0.7.0 准备 |

**两个数字最值得注意**：

| 数字 | 含义 |
| --- | --- |
| **08-20 只有 2 笔** | 全仓库**唯一一次"先写完整设计文档再动手"** |
| **08-25 → 09-05 有 12 天零提交** | 之后第一笔是方向切换（`c1cf62ccd`） |

**判据**：

- 提交频率突然从 30+ 掉到 2 → **它不在写代码，在决定写什么**；
- 长时间零提交后跟着一个 `harden` / `refactor` 开头的提交 → **方向调整**。

---

## A.4 七层与依赖方向

```
L7  前端           web/  console/
L6  控制面         fleet/  cmd/fleet-control-plane
L5  边缘           edge/  cmd/edge-worker  cmd/local-agent  internal/
L4  机器人网关     robot/gateway（Python）  robot/ros2_ws/
L3  本体           robot/ros2_ws/src/xlerobot_adapter  sim/{mujoco,gazebo,robocasa}

═══════ 横切：共享内核 ═══════
  agent/  orchestration/  tasks/  middleware/
  core/{taskgraph,skills,compiler,guard,observation,worldmodel,
        harness,closedloop,agentcontract,robotcontract,agentcontext}
  proto/  gen/
═════════════════════════════
```

**八条硬约束（`docs/development/principles.md:5-18`）**：

| # | 原则 |
| --- | --- |
| 1 | **模型只提出意图或候选动作。** LLM 不生成审批、期限、lease、幂等键、fencing 或 safety profile |
| 2 | **任务成功需要环境证据。** 陈旧、冲突或缺失时等待或失败关闭 |
| 3 | **同一 Runtime 契约贯穿仿真与实机** |
| 4 | **核心依赖接口，适配器依赖 SDK**（`tests/architecture` 检查） |
| 5 | **重试以物理结果为边界。** 队列可以至少一次交付，**物理动作不能重复** |
| 6 | **版本与身份不能倒退** |
| 7 | **界面只呈现可信状态** |
| 8 | **证据与结论绑定版本和环境** |

**验证原则 4 的命令**：

```bash
go list -deps ./core/... ./agent/... ./orchestration/... ./tasks/... \
  | grep -E 'database/sql|redis|grpc|protobuf|mysql'
# 应该没有输出（或只有预期内的）
```

**⚠️ 但这个检查有覆盖缺口**：`tests/architecture/dependencies_test.go` 的**被测包集合不含 `./cmd/...` 与 `./internal/localapp/...`**，禁列表也不含 `fleet` / `cloudclient`。

---

## A.5 关键常量与默认值

### 时间与预算

| 常量 | 值 | 位置 |
| --- | --- | --- |
| `DispatchPrecision` | **1 ms** | `core/closedloop/closedloop.go:349` |
| `ClockSkewAllowance` | **5 s** | `core/telemetry/freshness.go:18` |
| `DefaultMaxAge` | **60 s** | `core/telemetry/freshness.go:24` |
| `DefaultTelemetryMaxAge` | **10 s** | `agentruntime/opsrules.go:61` |
| `DefaultStepLatencyBudget` | **30 s** | `agentruntime/opsrules.go:67` |
| `OutboxClaimTTL` | **30 s** | `fleet/eventlog/store.go:13-14` |
| 设备租约 | **15 s** | `fleet/gateway/gateway.go:99-100` |
| 声明租约 / 资源租约 | **2 m** | `fleet/coordinator/coordinator.go:153,913` |
| 领导者租约 | **15 s** | `fleet/coordinator/coordinator.go:274` |
| `MaxDispatchBudget` | **10 m** | `edge/runtime/dispatch.go:10` |
| 恢复计划冷却 | **2 m** | `agentruntime/recoveryagent.go:695-696` |
| `recoveryPlanCooldown` 的超时 | **3 m** | `console/recovery_execute.go:60` |
| 渲染超时 | **60 s** | `sim/mujoco/tangying_sim/rendering.py:23` |

### 数量与容量

| 常量 | 值 | 位置 |
| --- | --- | --- |
| 分类表 | **179 条目 / 177 唯一码** | `core/closedloop/closedloop.go:83-274` |
| 运行时码清单 | **123 条目 / 119 唯一码** | `classification_coverage_test.go:128-254` |
| 恢复目录 | **16 条**（6 read_only / 7 bounded_write / 3 never_automatic） | `agentruntime/recoverycatalog.go:122-229` |
| 工具（注册 / 给 LLM / 不给） | **31 / 29 / 2** | `tools.json` |
| Go 技能清单 | **12** | `skills/manipulation/plugin.go:21-60` |
| MCP 工具 | **9** | `robot/mcp/tangying_mcp/server.py:157-199` |
| 规范工具词汇表 | **13** | `robot/gateway/tangying_robot_gateway/contracts.py:25-29` |
| 动作关节键 | **14**（12 臂 + 2 头） | `xlerobot_backend.py:28-32` |
| 电机 | **16** | `robot/gateway/tangying_robot_gateway/calibration.py:61` |
| 事件队列每订阅者容量 | **256** | `agentruntime/bus.go:29` |
| `seen` 清空间隔 | **4096** 次投递 | `agentruntime/bus.go:91-94` |
| 事故包保留 | **200** 份 | `incidents/bundle.go:35` |
| 世界 delta 环 | **512** | `cmd/fleet-control-plane/main.go:314` |
| 自动恢复每计划上限 | **3** 个动作 | `internal/autorecovery/supervisor.go:67` |
| `maxEvidenceRefs` | **8** | `agentruntime/opsrules.go:71` |
| `ESCALATION_THRESHOLD` | **5** 次复发 | `module_faults.py:69` |

### 版本与 pin

| 项 | 值 |
| --- | --- |
| Go | **1.26.2**（`common.sh:8`） |
| Python | **≥ 3.11**（`pyproject.toml:8`） |
| 平台（robot-pi） | **仅 `linux:ubuntu:24.04:arm64`** |
| XLeRobot 提交 | **`3d14695e40c9c68229c0aacffca6053c75cd3eb6`** |
| LeRobot | **0.4.1** |
| Protobuf | **6.33.5**（**不能**单独升级生成工具） |
| 家具资产 | AWS Small House **`ff9631ca…`**（MIT-0） |
| 机器人模型 | XLeRobot **`3d14695e…`**（Apache-2.0） |
| 固件（仿真） | MuJoCo **3.11.0** |

---

## A.6 端口全表

| 端口 | 用途 |
| --- | --- |
| **8787** | 本地单机控制台（**家庭场景 8897**） |
| **50051** | 仿真 Runtime（**家庭场景 50161**） |
| **45871/UDP** | 机器人广播（发现） |
| **45872/TCP** | 配对（enrollment） |
| 18790 / 18791 | 机器人端导航容器 |
| 8091 | Policy sidecar（默认 `127.0.0.1`） |
| 443 + 8444 | 云端（HTTPS + gRPC） |
| 18080 | 云端**仅回环** |
| 3306 + 6379 | MySQL / Redis，**仅容器网** |

**8787 vs 8897 不是两个服务**，是**同一个 Local Agent 在两种场景下的端口**——因为**两份数据要同时存在**。

---

## A.7 本地单机的进程拓扑

```
make home-furnished
 └─ scripts/furnished-home-demo.sh start --sim-port 50161 --agent-port 8897
      └─ exec bash scripts/sim-stack.sh start … --scene home_task --perception rgbd
           ├─ 进程 1  .venv/bin/python -m tangying_sim.server --listen 127.0.0.1:50161
           └─ 进程 2  bin/local-agent --dev-insecure
                      --robot-safety-profile desktop_standard
                      --listen 127.0.0.1:8897 --robot 127.0.0.1:50161
```

**两个进程。**

**注意 `--robot-safety-profile` 是显式传的**——Local Agent **必须显式配置安全档位**。

---

## A.8 关键文件速查

### 契约（最常被引用）

| 内容 | 路径 |
| --- | --- |
| **闭环契约** | `core/closedloop/closedloop.go`（分类表）、`gate.go`（证据门禁） |
| 世界模型 | `core/worldmodel/{types,projector,predicate,checkpoint}.go` |
| 观测契约 | `core/observation/{envelope,catalog}.go` |
| Harness | `core/harness/evaluator.go` |
| 任务状态机 | `core/taskgraph/state.go` |
| 技能清单 | `core/skills/manifest.go` |
| Agent 契约 | `core/agentcontract/{contract,event,memory,anomaly,payload}.go` |
| 机器人契约 | `core/robotcontract/{contract,faults}.go` |
| 运行时命令 | `edge/runtime/{runtime,dispatch}.go` |
| 工具目录 | `tools.json`（生成物） |
| proto | `proto/robot/v1/robot.proto`、`proto/fleet/v1/fleet.proto` |

### 最值得先读的五个文件

| # | 文件 | 为什么 |
| --- | --- | --- |
| 1 | `core/closedloop/closedloop.go` | **包注释就是全书的核心命题**；分类表的每条注释都是设计论证 |
| 2 | `core/closedloop/gate.go` | 四道判决，顺序不可交换 |
| 3 | `docs/architecture/why-distributed.md` | **与 coding agent 的对照，以及"这份文档没证明的事"** |
| 4 | `docs/architecture/lifecycle-objects.md` | 一次任务里十种对象各自的生死 |
| 5 | `internal/actionloop/loop.go` | "结果未知终止一切"和"批准范围"的落点 |

### 最值得先读的五个测试

| # | 文件 | 为什么 |
| --- | --- | --- |
| 1 | `core/closedloop/gate_test.go` | 门禁的全部边界用例（含毫秒粒度） |
| 2 | `core/closedloop/classification_coverage_test.go` | **把危险本身钉住** |
| 3 | `agentruntime/opsagent_test.go` | **字段白名单反射测试** |
| 4 | `agentruntime/faultmatrix_test.go` | 16 个场景，每个都断言"是否禁止重试" |
| 5 | `tests/e2e/test_fleet_faults.py` | **唯一真正跨越进程边界的测试**（SIGSTOP + "恰好一次"） |

---

## A.9 版本与引用提醒

### v0.6.0 → v0.7.0

| 项 | 值 |
| --- | --- |
| 写作基线 | `774bd2a2f`（v0.6.0） |
| 当前 HEAD | `8b9683be8`（v0.7.0 准备） |
| `VERSION` | `0.7.0` |
| **v0.7.0 标签** | **尚未创建**（"已准备、未打标"） |

### `CHANGELOG.md` 被收敛过

| 版本 | 行数 | 内容 |
| --- | --- | --- |
| `774bd2a2f` | **3,282 行** | 逐项技术叙事 |
| HEAD | **200 行** | 版本摘要 |

**所有旧版 CHANGELOG 引用写作 `CHANGELOG.md@774bd2a2f:<行号>`。**

```bash
git show 774bd2a2f:CHANGELOG.md
```

### 已知的文档与代码冲突（书中均已标注）

| # | 冲突 | 真相 |
| --- | --- | --- |
| 1 | 失败分类"七类" | **代码是 8 类**（`closedloop.go:48-68`） |
| 2 | 工具"27 / 25 / 28 个" | **31 注册 / 29 给 LLM** |
| 3 | MCP"8 个工具" | **9 个**（缺 `get_survey`） |
| 4 | "113 个故障码" | **历史值**；今天清单 119 唯一、分类表 177 唯一 |
| 5 | "279 条报告 → 7 个问题" | 有**四个版本**（279→7 / 120→7 / 276→8 / 276→92） |
| 6 | `cmd/local-agent` "引用 fleet 0 处" | **无运行时依赖，有编译期依赖**（二进制里 227 次符号命中） |
| 7 | `lifecycle-objects.md` 的 `Track` 状态机 | **已删除**（`Track`/`ErrUnknownRetry` 全仓零命中） |
| 8 | `readiness.md` §7"结果未知无清除路径" | **已补上**（`console/reconcile.go`）；文档是过期文本 |
| 9 | "标定会过期" | **全仓无 `CALIBRATION_STALE`/`CALIBRATION_EXPIRED`** |
| 10 | README 的 12 步计划"返程未走通" | 与后续 8/8 条腿闭环**是不同版本的代码**，引用须带日期 |

---

## A.10 一句提醒

> **本书里每一个技术断言都可以回溯到源码或文档。**
>
> **凡是推断，标注「推断」；凡是文档与代码冲突，标注冲突并给出代码事实。**
>
> **这本书不要求你相信作者，它要求你去读代码。**
