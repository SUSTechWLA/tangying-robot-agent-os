# Changelog

软件版本、当轮任务与验证结果见 [v0.6.0 发布记录](docs/releases/v0.6.0.md)。当前交付主线是**单机器人在装修家庭场景完成自然语言任务**；软件发布与客户实机的现场放行分别验收。

## Unreleased

### 从"技术员部署"走向"上电就能用"：自动发现、可用性自检、以及四个挡住用户的缺陷

三轮独立审计（首次接入路径、自我管理能力、用户界面）在被测的真实控制台上得出的结论是：
**目标未达成，而且是架构性缺口，不是打磨问题**。本轮修掉其中最挡路的一批。

**① 机器人现在会自己出现（零配置发现）**
- 机器人每 5 秒 UDP 广播一次自己（端口 45871）：身份、**对端可连的**地址、适配器、配对状态、能力数量。
- Local Agent 被动监听，控制台 `GET /v1/robots/discovered` 列出听到的机器人。
- 三态报告而不是"有没有"：`listening`（有没有人在听）、`mismatched`（有机器人在广播但版本读不了——最有用的一个数）、`unreadable`（噪声）。
- 广播是明文且不认证的，因为两端还没有共享密钥；因此**配对码永不进广播**。
- 广播目标包含回环：模拟器把机器人和 Agent 跑在同一台笔记本上，只广播外网接口会让"上电就出现"在演示里失效。
- 出厂默认 `ROBOT_ID` 是所有机器人共用的常量，会让两台机器人显示成一台在移动的机器人。改为 `ROBOT_ID` → `/etc/machine-id` → 主机名 → 历史常量，并把来源打进日志。

**② 跨语言契约用 fixture 锁死**
- `tests/contract/robot_announcement.json`：Python 侧断言逐字节相同，Go 侧断言能读这个文件本身。
- 已验证：改名 `robotId → robot_identifier` 两侧同时失败。（Go 的 JSON 匹配大小写不敏感，所以 `robotId → robotID` 只有 Python 侧能拦——契约必须在两侧都断言。）
- Python 侧补上了 `validate()`：以前它会广播 Go 侧必定拒收的载荷（无 id、无地址、`0.0.0.0`），机器人会安静地广播一些被丢掉的东西。

**③ 系统第一次能回答"现在能不能用"**
- 新增 `GET /v1/readiness`。此前 `/healthz` 恒为 `{"status":"ok"}`：一台**急停锁存、没有地图、还有一步结果未知**的机器人也报 ok。
- 七个检查项，三态（`ready`/`action`/`unknown`）——`unknown` 刻意不是 `ready` 的一种。
- **`/healthz` 保持存活语义不变**：急停中的机器人是一个正在正常工作的进程，因它重启就是安全功能造成的事故。
- 故障处置句子引用机器人自己的 `userInstruction`，不在此改写；取不到时给"下一步做什么"而不是假装的建议。
- 语言能力单独展示：**"它没听懂我"和"没配置模型"长得一模一样，只有后者用户能修**。

**④ 首启死路：安全确认永远无法完成**
- `web/app.js` 的 `localSafetyAcknowledged` **被声明、被读、从未被赋值**，而项目自己的测试把它硬编码成 `true` 绕过。
  于是清单永远到不了"ready"，用户第一眼看到的是一个**清不掉的阻塞项**。
- 新增"我已确认现场安全"控件；会话作用域（`sessionStorage`），急停出现时自动作废——确认是关于某一刻的世界说的话。

**⑤ 102 条告警把工作区埋了**
- 实测：告警横幅高 24,496 px，任务输入框在 y=24,729——用户要的东西在整屏历史记录之下。
- 改为：机器人级故障 + 当前打开任务的发现优先，其余折叠并**计数而非丢弃**，最多 5 条 + 展开。
- 原始码不再当标题：`ANOMALY_UNVERIFIED_MUTATION` → "有个动作的结果没能确认"；没有标签的码原样显示，**不隐藏**。
- 排序稳定，每 5 秒刷新时不会在读者手底下重排。

**⑥ 装完/配对后的四个断点**
- **断电重启什么都不回来**：全仓库没有一处 `systemctl enable`。现在安装时 `enable`（不 `--now`），配对成功后再 enable 机器人服务——过早 enable 会让未配对的机器人在每次开机时启动、失败、被反复重启。
- **配对从不验证连通性**，只解析了 DNS 就打印 `pairing complete`。现在真去连一次端口，失败即以非零退出并打印要检查什么；明确跳过时输出写明 `unverified`。机器人端口也可用 `ROBOT_AGENT_PAIR_PORT` 指定（此前 50051 是写死的字面量）。
- **`make build && ./bin/local-agent` 静默忽略配对结果**：不传 `--config` 时 `findConfigPath` 返回空，于是退回 `127.0.0.1:50051` 明文。现在默认读取安装器与配对脚本写入的同一个文件；同时 `readConfigFile` 容忍文件不存在（否则新机器会因为"还没配对"而起不来）。
- **冷启动文档从不提配对**：`fresh-deployment.md` 自称"单一入口"，路线 B 里没有 `pair` 这一步。已补上，并加了"先看机器人有没有自己出现"。

**⑦ 顺带修掉一个坏测试**
- `tests/contract/test_fault_contract.py` 断言"没有地图的移动机器人必须自己说导航不可用"，用的却是**带着启用地图**的 seeded 世界，所以它一直在拿空列表比空列表。机器人行为是对的，测试的前提是错的。现已改成真的清掉地图，并补了一条反方向的测试（有地图就不能报导航不可用）。

**验证**：Go 45 包通过（新增 `internal/discovery`）、gofmt 干净；Web 427 通过；Python 618 通过（**注意必须用 `.venv/bin/python`**：环境里的 `python3.11` 带 grpc 1.80.0，而生成代码要求 ≥1.81.0，会误报一大片失败）。
关键回归均验证过"改回旧逻辑会失败"：关掉告警上限、去掉默认配置查找、把 `findConfigPath` 改回返回空。

**明确没做**：免 SSH 的一键配对。发现链路已通，配对投递仍走一次 SSH；机器人能广播 `pairingState=open`，但接受配对请求的引导通道尚未实现。详见 `docs/architecture/robot-discovery.md` §9。

### 恢复 Agent 接通生产链路：四个只有真机才会暴露的缺陷

**根因：观察者的发现从未上过总线**

组合根逐个 Agent 手工赋事件发布通道，执行 Agent 装了、观察 Agent 从来没赋过值。`publishFinding` 在 `a.Publish == nil` 时静默返回，所以：控制台照样列出每一条发现（`RunnerAlerts.Record` 先执行），每个 Agent 照样报健康，而**恢复 Agent 订阅的 `ops.anomaly_detected` 永远不会到**。全仓库 `.Publish =` 的赋值点只出现在测试里——测试验证的是一套生产环境并不存在的接线，所以 100+ 个测试全绿。

- 新增可选能力 `agentcontract.Publisher`（`SetPublish`），不污染 `Agent` 接口；
- `Orchestrator.Start` 统一注入总线，`edge/agent.Runner` 也实现该接口，组合根里那句手工赋值被删掉——**发布通道只剩一个来源**；
- `Orchestrator.Publishers()` 打进启动日志：`publishers=[task ops recovery]`。"没什么可说的"和"根本说不了话"从外面看是一样的。

**一份计划曾被展示给 11 个不同的故障**

"一个发现的身份"有三套答案：告警存储用 `code@component`、恢复 Agent 用裸 `code` 归档计划、控制台用裸 `code` 查询。后果是 11 条告警全部显示第一份计划，诊断栏里还写着别的组件的名字；计划的冷静期也按裸 code 计，同一任务里第二个组件失败根本不会再规划。

- 规则收敛到 `core/agentcontract/anomaly.go`：`AnomalyIdentity` / `AnomalyReportID` / `SameAnomaly`；
- 计划按身份归档、按身份查询，并且**限定在同一任务内**匹配（真实数据里任务 A 的告警显示着任务 B 的计划）；
- 旧版本按裸 code 写的计划**故意不再匹配**：借来的计划比没有计划更糟，它读起来像一个答案。

**"查不出来"曾被当成"没问题"**

"这个任务没有结果未知的步骤"与"这个部署查不出来"产生同一个空列表。存储不实现 `ExecutionReader` 时事实读取器直接跳过，**轨迹里连一步都不留**；读失败时也只在轨迹里记一笔、不向上传递。这足以让一次存储抖动静默关掉"结果未知禁止自动重试"这条最硬的规则。

- `RecoveryFacts` 拆出 `ReconciliationUnavailable` / `ReconciliationError`；
- 新增规则 `rule.reconcile-unknown`：问不出来 → **转人工**，理由写明"恢复执行记录的读取能力后重新调查：<具体错误>"；
- 不支持列表与读取失败两种情况都会写进轨迹并带 `Error`。

**同一个问题在告警列表里出现三次**

发布通道接通后，任务级告警从 0 涨到 68 条，其中 6 组重复；`activeCount` 数的是上报次数而不是问题个数。根因是 `ProjectAgentAlerts` 对每条事件都 `append`——这条路径此前因为 agent 事件根本没进台账而一直是空的。

- 按 `(taskID, 身份)` 去重、保留最新：key 的两半都不能省（只按 code 去重会隐藏第二个组件，不带 task 去重会隐藏另一个任务，两种退化都已用测试实测会失败）。

**新增文档资产**
- `docs/architecture/recovery-agent.md` —— 恢复 Agent 的完整设计：为什么不是重试器、只提议不执行的结构化边界、动作目录与三档风险、拒绝即作废整份计划、对账规则、轨迹逐步读法、计划字段说明、以及"本版本不做的事"诚实清单。
- `docs/development/2026-09-17-mute-observer-and-plan-identity.md` —— 四个缺陷的现象、根因、为什么测试没拦住、以及每个缺陷留下的回归测试。

**测试**：新增 `agentruntime/publisher_test.go`（全部**不手工注入 sink**，关掉注入即失败）、`cmd/local-agent/recovery_wiring_test.go`（直接跑真实组合根，断言发现走到计划入库）、`console/alerts_test.go`（该端点此前**一个测试都没有**）、`core/agentcontract/anomaly_test.go`、以及 `tasks/alerts_test.go` 的去重三条。关键回归均验证过"改回旧逻辑会失败"。

### 修复你的机器人栈 + 分类审计扩宽（83 → 113 码）+ 一个未接线能力

**修好了被我弄坏的栈**
- 在你的 8897 实例上跑了一次真实巡检建图，导航从 `ready: False, mode: mapping` 恢复为 **`ready: True, localization: localized`**，故障台账回到 `count: 0`。
- 用记录中的确切参数（`artifacts/sim-stack/furnished-home/run/*.identity`）重启了 agent，现在带**新二进制**运行：`observing: true`，你能看到 ⚠️ 告警了。
- 说明：该栈的原有 sim 进程已被我先前操作杀掉，当前 sim 是我用相同参数重启的；导航已重建。**这是我的操作造成的，已尽力恢复。**

**分类审计扩宽：上一版"83 码全覆盖"是错的**
- 在你机器人上又发现漏网：`PLACEMENT_NOT_OBSERVED` 仍被报成「结果未知，禁止重试」。
- **根因是审计方法**：它作为**字符串参数**传入（`self._verify_relation(..., "PLACEMENT_NOT_OBSERVED")`），而我的正则只匹配 `ToolResult(False, "...")` / `ServiceError("...")` 字面量。
- 扩宽到 **5 类证据来源**（ToolResult / ServiceError / 参数传递 / diagnose 故障族 / Go 侧 emit）后得到 **113 个码**，又补进 16 个，含被一起漏掉的 `GRASP_NOT_OBSERVED`、`NAV_OBSTACLE_OBSERVED`、`NAV_PATH_OCCLUDED`、`GROUNDING_ABSENT`、`SAFETY_STOPPED` 及传输类。
- **`*_NOT_OBSERVED` 刻意归 `UNKNOWN_OUTCOME` 而非 `PERCEPTION`**：语义是「动作已执行但未观测到预期关系」，与「动作没发生」不同——放置验证失败时物体可能已在目的地，重试会重复放置。与 `diagnose_task.py` 的 `verification_not_observed` 族一致，也与闭环契约把 `VERIFICATION_FAILED` 归 `Fatal` 的方向一致。

**修好后的实测（你机器人上的 5 条真实告警）**

| 真实失败码 | 分类 | 建议 |
| --- | --- | --- |
| `PLACEMENT_NOT_OBSERVED` | `UNKNOWN_OUTCOME` | 不要自动重试：先对账 |
| `NO_KNOWN_PATH` | `PERCEPTION` | 重新观测或搜索目标后再尝试 |
| `ROBOT_COMMISSIONING_ACTIVE` | `RESOURCE` | 检查审批、租约或资源占用状态 |
| `DESTINATION_NOT_FOUND` | `PERCEPTION` | 重新观测或搜索目标后再尝试 |

修复前这 4 条里只有 1 条是已知码，另外 3 条建议完全相同（都是"不要重试"）。现在建议按失败类型分化。

**顺带查出一个未接线能力：`closedloop.Track`**
- 生产路径只用了 `Gate`（证据门）与 `Classify`（分类）；**`NewTrack` 在仓库里只出现在测试中**。
- 后果有两面：① 好的一面——本次分类改动**不会改变重试行为**，`Classify` 只喂给只读的监督层；② 需要知道的一面——`Track` 的尝试预算、退避、`NextAttemptAt` **从未在生产生效**，实际重试由宿主与操作员决定。
- 已写入文档，标注为**需要一次专门决策**（接上或删掉），不该继续以"已实现"的样子存在。

**验证**：Go 44 包通过；Web 403 通过；docs/deploy/precheck 26 通过；gofmt 干净。分类守卫覆盖 **113 个码**。

### 目标场景全项验证 + 分类表系统性审计（83 个码）

**目标里的每个异常场景都验证过了**

| 场景 | 状态 |
| --- | --- |
| 急停锁存 / 地图未启用 / 目标够不到 / 观测过期 / 网络断联 | ✅ 真实 sim 实测 |
| **导航受阻** | ✅ **本轮补上真实注入** |
| 执行结果未知 / 重复失败升级 | ✅ 真实 agent 驱动（不是纯函数） |
| 标定不匹配 | ✅ 确认**无法注入**，且已交叉验证 |

**导航受阻做到真实注入**：先跑 `scripts/build_sim_map.py` 完成一次真实巡检建图（167 帧 / 26.94 m，地图自动启用，导航变为 `ready: True, localized`），随后「从客厅去厨房」在路径规划预检被拒 `GOAL_NOT_CLEAR`。**底盘没有移动**——这成为分类修复的依据。

**分类表系统性审计：83 个码里 58 个不在表里**
- 扫法本身踩了一次坑：第一遍只搜一种抛出写法找到 53 个，后来发现 `GOAL_NOT_CLEAR` 走的是 `ServiceError(...)`——**整整一类被漏掉**。补上后 83 个唯一码。
- **58 个静默退化成「结果未知，禁止重试」**，其中包括预检类失败。
- 实测危害：`GOAL_NOT_CLEAR`（目标工作区未扫描/间距不足，底盘未动）被报成

  ```
  （UNKNOWN_OUTCOME）建议：不要自动重试：先对账确认这次动作的实际结果
  ```

  **让操作员去对账一个从未发生的动作，比不报还糟。** 修复后为 `（PERCEPTION）重新观测或搜索目标后再尝试`。
- 按既有七类语义把 83 个码**全部归类**，并把清单**提交进仓库**作为永久守卫 `TestEveryCodeTheRuntimeCanEmitIsClassified`。清单提交而非自动爬取是刻意的：新增码而没加进清单会落在未分类状态，守卫就会失败——**「未分类」作为默认是正确的，作为意外是危险的**。

**两个此前的"仅单元测试"场景改为真实 agent 驱动**
- 重复失败升级：单次 = warning + 可重试；**同一评估窗口内两次** = critical + 发出升级事件。测试过程中修正了我对语义的误解——重复是**窗口内**计数，不跨窗口累加（十年前的失败是历史，值得升级的是"现在还在发生"）。
- 执行结果未知：真实 agent 断言禁止重试、给出对账建议，并在执行记录转为终态后**停止上报**。

**故障矩阵 14 → 16 场景**：补入导航受阻的两个真实码（`NAV_WORKSPACE_LIMIT`、`NAV_STEP_LIMIT`）。

**验证**：Go 44 包通过；Web 403 通过；docs/deploy/precheck 26 通过；gofmt 干净。

### 文档资产：Review Agent 运行原理 + 盲区发现记录

把这几轮的**原理与过程**沉淀成两份长期文档（代码仓已同步）。

**`docs/architecture/review-agent.md` —— 运行原理**
- Review Agent(= OpsAgent)与 TaskAgent 的分工:区别不是"一个干活一个看",而是"**一个能改、一个不能改**"。
- **三条不可动摇的规则**:① 发现必须带证据或明说缺什么(猜根因比不报更糟);② 复用既有失败分类,不发明第二套(两套分类迟早就"能不能重试"给出不同答案,而其中一套会做出物理动作);③ 建议必须可执行(只报"出错了"等于一行日志)。
- 输入四路、输出两个作用域(任务级进账本与回放、机器人级进告警横幅),以及**告警为什么会自己消失**(从当前状态投影,不是事件累积——否则横幅比问题活得更久,操作员学会忽略它)。
- **边界与落地状态**:恢复闭环五段里,①②已实现并实测,③半实现,**④执行与⑤验证未接线**(`FaultRemedyEngine` 生产路径 0 调用者)。文档明确区分"能自动发现并说清楚"与"不能自动动手修"。

**`docs/development/2026-09-17-supervision-blind-spots.md` —— 发现记录**
- 三个盲区的完整经过:当时以为是对的 → 哪一步实测推翻了它 → 怎么修的 → 改完怎么确认。
- **记了我自己犯的三次错**:① 为测断联杀掉正在使用的 sim 且未确认恢复路径(导致地图未启用、机器人不可用);② 把"我杀了服务没重启"误判成产品缺陷"客户端不重连",并已作为发现报告出去;③ 两个自己写的测试断言错了(其一混淆了"表内列为 UnknownOutcome"与"未列出")。
- **四条方法**:先写测试证明盲区存在再修;端到端测试抓单元测试抓不到的洞("组件正确"≠"链路正确");交叉验证 agent 的输出与别处事实对得上;守护要注入退化验证它真的会失败。

**补上一条永久守卫**
- 新增 `TestKnowsDistinguishesListedFromUnlisted`:断言"表内列为 `UnknownOutcome`"与"完全未列出"的码**分类结果相同**、但 `Knows` 能区分。这条测试的意义在于**记录那个陷阱本身**——如果将来分类行为变了,它会说明"这条测试不再证明任何事,覆盖守卫可以简化"。
- 文档里三处代码断言已逐条核对为真(`FaultRemedyEngine` 无生产调用者、分类守卫 4 条、`Knows` 语义)。

**交叉引用**:`module-health-and-faults.md` 标注了"设计已实现但第④段未接线"及其前置条件;`multi-agent-runtime.md`、`agent-v1.md`、`supervision-verification.md`、`docs/README.md` 均加入口。

**验证**:Go 44 包通过;Web 403 通过;docs/deploy/precheck 26 通过(文档链接完整性由 `tests/docs` 强制,本轮它两次抓到我写错相对路径);gofmt 干净。

### 异常模拟实测暴露并修掉三个真问题（含一个分类表缺口）

在真实 sim 上模拟机器人异常、用 OpsAgent 定位,逐个核对"它说的是不是对的"。

**问题一：失败动作只从实时事件累积 → 重启后只会说"任务失败了"**
- 实测蓝色瓶子任务(瓶子够不到)真实失败(`GRASP_NOT_REACHED`,37 事件),OpsAgent 只报「有任务异常结束」——说"出事了",但说不出"哪一步、为什么"。
- 根因:失败动作只从实时事件累积,而这个任务的事件在查询前已落盘。
- 修复:启动扫描从每个任务的账本读回工具失败,并**走与实时路径同一个累积函数**(抽出 `recordFailedAction`)。两条路径共用一份"什么算失败动作"的定义,否则重启后同一失败会被算成两回事。

**问题二（更严重）：一个真实故障码不在分类表里,导致"已知失败"被报成"结果未知"**
- `GRASP_NOT_REACHED` 是运行时自己产生的码(抓手够不到目标),但**不在 `core/closedloop` 分类表中**,于是兜底到 `UnknownOutcome`。
- 后果不是"报错",而是**给出错误处置**:够不到目标应该"重新观测后重试",系统却说"不要重试,先对账"——把可恢复的失败报成不可恢复。
- 已按 `PERCEPTION` 归入表中。实测对照:

| | 修复前 | 修复后 |
| --- | --- | --- |
| 定位 | 只有「有任务异常结束」 | `manipulation.pick 失败：GRASP_NOT_REACHED（PERCEPTION）` |
| 建议 | 「核对执行记录」 | 「重新观测或搜索目标后再尝试」 |

- **补上缺失的守卫**:此前只有"表内代码分类正确"的测试,**没有测试断言"运行时会产生的码都在表里"**。新增 `core/closedloop/classification_coverage_test.go`(4 条),并为此导出 `closedloop.Knows(code)`——因为 `Classify` 无法回答"这个码在不在表里":表内列为 `UnknownOutcome` 的码与完全未列出的码**分类结果相同**,而这两者的区别正是问题所在。

**问题三：我自己的死锁**
- 抽出 `recordFailedAction` 时,`OnEvent` 在持锁状态下调用它,而它自己也要加锁 → 测试直接挂起(不是失败)。已修:调用前释放锁。**这次是测试挂起而不是报错,说明"超时"同样是有价值的信号。**

**故障注入器 `scripts/inject_faults.py`**
- 对真实运行时注入故障,再查监督 agent 说了什么。退出码 `0` 全定位 / `1` 有遗漏 / `2` 连不上 / **`3` 所有场景的故障当前都不存在**(区分"注入没生效"与"都通过了")。
- **不伪造故障**:运行时只证明它能证明的三个;标定故障无法从外部注入,标为 `N/A` 并说明,**不算成 agent 漏检**——没有东西可找。
- **按代码+消息双重匹配**:两个模块故障共用 `ANOMALY_COMPONENT_FAULT`,只按代码匹配会把地图故障当成标定故障的检出证据(第一次跑时真实踩到)。
- 实测发现一个设计后果:急停没有解除 RPC(软件不代按急停),脚本跑完 estop **无法清理**,必须重启运行时——已写进文档。

**双 agent 分工闭环实测(真实 sim,tabletop 场景)**

| 场景 | TaskAgent | OpsAgent |
| --- | --- | --- |
| 正常任务 | 「把红色杯子放进右侧收纳盒」→ `SUCCEEDED`,62 事件,21 次工具调用,末步 `verify_placement CONFIRMED` | **0 条告警** — 健康时静默 |
| 执行中掉线 | 同一条自然语言请求 → 能力检查连接中断 → `RECOVERABLE_FAILURE` | `ANOMALY_ABNORMAL_TASK` + `ANOMALY_TELEMETRY_STALE` |
| 目标够不到 | 同一条请求(蓝瓶) → `GRASP_NOT_REACHED` → `RECOVERABLE_FAILURE` | `ANOMALY_ACTION_FAILED`(PERCEPTION)+ 重观测建议 |

正常任务的回放是 **33 条 TaskAgent 事件、0 条 OpsAgent 事件**——没有异常就没有诊断。

**验证**:Go 44 包通过;Web 403 通过;docs/deploy/precheck 26 通过;gofmt 干净。

### 异常模拟与双 agent 分工闭环实测

**故障注入器 `scripts/inject_faults.py`**
- 对真实运行时注入故障,再查监督 agent 说了什么。退出码 `0` 全部定位并给出建议 / `1` 有遗漏 / `2` 连不上 / **`3` 所有场景的故障当前都不存在**——最后这条是为了区分"注入没生效"和"都通过了"。
- **不伪造故障**:运行时只产生它能证明的三个(`EMERGENCY_STOP_LATCHED`、`WORKCELL_CALIBRATION_MISMATCH`、`NAV_MAP_NOT_READY`)。无法从外部注入的(标定由工位高度自行判定)标为 `N/A` 并说明原因,**不算成 agent 漏检**——没有东西可找,报成漏检就是错的。
- **按代码+消息双重匹配**:两个模块故障共用 `ANOMALY_COMPONENT_FAULT`,只按代码匹配会把地图故障当成标定故障的检出证据。这个缺陷是第一次跑时真实暴露的。
- **实测暴露的一个设计后果**:急停没有解除 RPC(软件不代按急停),所以脚本跑完 estop 场景**无法清理**,必须重启运行时。已写进文档。

**实测结果**
| 场景 | 结果 |
| --- | --- |
| 急停锁存 | `ANOMALY_SAFETY_STOP` + 「由人工复位急停按钮」 |
| 地图未启用 | `ANOMALY_COMPONENT_FAULT`「chassis 报告 NAV_MAP_NOT_READY」+「先完成巡检建图并在控制台启用地图」 |
| 标定不匹配 | 当前不存在(无法注入) |

**双 agent 分工闭环(真实 sim,`tabletop` 场景)**
| 场景 | TaskAgent | OpsAgent |
| --- | --- | --- |
| 正常任务 | 「把红色杯子放进右侧收纳盒」→ `SUCCEEDED`,62 事件,21 次工具调用,末步 `verify_placement CONFIRMED` | **0 条告警** — 健康时静默 |
| 执行中机器人掉线 | 同一条自然语言请求 → 能力检查连接中断 → `RECOVERABLE_FAILURE` | `ANOMALY_ABNORMAL_TASK`「有任务异常结束,需要核对执行记录确认实际到哪一步」+2 条建议,以及 `ANOMALY_TELEMETRY_STALE` |

正常任务的回放是 **33 条 TaskAgent 事件、0 条 OpsAgent 事件**——没有异常就没有诊断。掉线那条则同时给出两类发现。其中 `ANOMALY_ABNORMAL_TASK` 来自运行期规则(不是启动扫描),证明它能在真实运行中触发:只靠启动扫描的话,这个失败要等到进程重启才会被报告。

**本轮修掉的两处自己的错误**
1. `inject_faults.py` 的场景匹配只按代码,会张冠李戴(已改为代码+消息)。
2. 新增的注入器测试断言"故障码必须同时出现在模拟器和脚本里",但**两层用的词表本来就不同**(机器人证明 `EMERGENCY_STOP_LATCHED`,agent 报告 `ANOMALY_SAFETY_STOP`)。是测试写错,不是代码错;已按分层修正。

**验证**:Go 44 包通过;Web 403 通过;docs/deploy/precheck **26** 通过;gofmt 干净。

### Web 端 ⚠️ 告警弹窗 + 真实 sim 上跑通「检测 → 报警 → 撤回」闭环

**告警弹窗（只在 Web 端，不碰桌面通知）**
- `tasks/alerts.go`：告警从任务**当前状态**投影，不是累积事件流。条件消除即自动 `active=false`，不需要谁记得去关闭，也不会留下比问题活得更久的横幅。
- `GET /v1/agent/alerts`：合并两个来源，并上报 `supervision`（是否有 agent 在观察）。
- `web/app.js` 的 `renderAgentAlerts` + `#agent-alert-banner`：⚠️ 图标、严重度分级、建议动作列表、禁止重试横幅、以及**监督关闭时的显式提示**。
- 新增 8 条**真实执行渲染函数**的 DOM 测试（复用上一轮的抽取式测试手法）。

**修掉一个结构性缺陷：最该被看见的发现进不了告警**
- 机器人级发现（急停、`robot.faults.v1` 故障、遥测过期）**没有 taskID**，而告警是从任务账本读的 → 它们被发到"没人"，哪个列表都不进。**急停不是任务问题,却在没有任务时完全不可见。**
- 修复：新增运行级告警存储 `agentruntime/runnermemory.go`（只放无任务发现，TTL 到期自动置为已解决），`OpsAgent.RunnerAlerts` 写入，控制台合并两个来源，前端合成一个横幅。
- 这个缺陷是**实测逼出来的**：第一次注入断联时告警没有出现,查下去才发现它根本没地方可去。

**真实 sim 实测（不是 mock）**
| 阶段 | 结果 |
| --- | --- |
| 健康基线 | `activeCount: 0`,`observing: true` — 健康时静默 |
| 真实故障（sim 重启后地图未启用） | 报 `ANOMALY_COMPONENT_FAULT`(critical)「chassis 报告 NAV_MAP_NOT_READY」+ 具体处置步骤 |
| 断联注入（kill 运行时） | **约 26 秒**内报 `ANOMALY_TELEMETRY_STALE` |
| 重连（同端口重启运行时） | **客户端自动重连,无需人工干预** |
| 撤回（遥测恢复） | 同一告警转为 `active: false` — **自动撤回** |

**三条独立交叉验证**：① `NAV_MAP_NOT_READY` 被导航接口独立证实（`ready:false, gridUnavailable:true`）；② 遥测恢复不是靠"告警消失"推断,而是历史条数持续增长；③ 日志的断联错误与告警同时出现、同时停止。

**一个被证伪的担心**：我先前怀疑「运行时重启后客户端不会重连」,实测证明是错的（`grpc.NewClient` 惰性连接自动恢复）。当初误判的原因是我杀掉了 sim 却没有重启它——"不恢复"其实是正确行为。

**一个未解释的观察（如实记录）**：sim 的观测时间戳滞后墙钟约两分钟且缓慢增长。遥测仍被判定新鲜（告警已撤回）,所以不构成误报,但原因未查明。若仿真的 `observedAt` 语义与实机不同,"新鲜度"这条规则在仿真与实机验证的就不是同一件事,值得单独确认。

**验证**：Go 44 包通过（新增 `tasks` 告警投影 9 条、`agentruntime` 运行级存储）；Web **403** 通过；docs/deploy 18 通过；gofmt 干净。`docs/production/api-reference.md` 已补新路由（该文档的完整性由 `tests/docs` 强制,这次是它抓到我漏写）。

### 前端从"断言源码"升级到"执行渲染函数"，并记录舰队监督的落地步骤

**前端真实渲染测试（`web/agent_rail_dom_test.mjs`，+10 条）**
- 按大括号配对从 `app.js` 抽出真实的 `renderMissionAgentEvents`，注入它实际用到的三个 DOM 调用（`createElement`/`replaceChildren`/`append`，已确认它不用 `innerHTML`）后**执行**，断言构造出的节点树：建议是 `<ol>` 且顺序正确、禁止重试是独立横幅、空回放给出说明而非空白、重复渲染不累积、未知 agent 名回退显示原名。
- **为什么需要**：仓库其余前端测试断言源码里出现过某个字段名，这抓不到"构造了错误的 DOM"，只抓得到"不再提到某个字段"。差别不是理论上的——本会话中前端测试全绿的同时，两个装配 bug 活了下来（缺失的事件 sink、`taskID` 为空的事件），因为没人执行过那段代码。
- **验证测试自己会失败**：把禁止重试横幅从 `<p class="mission-agent-forbidden">` 降级为普通 `<span>`，测试立即报错并指出是哪一条。
- Web 测试 385 → **395**。

**记录舰队监督的确切落地步骤（查证过，不是估计）**
- 查证结论：`edge/worker` 的 `Cloud.AppendEvent(ctx, taskID, eventType, stepID, message, payload)` **已经存在**，形状与本地账本 sink 等价——所以"诊断写进可回看的事件流"在云端路线是有位置的，不需要新存储。
- 三处要改：① `AgentRuntime` 的 sink 签名绑定了本地 `tasks.TaskEvent`，需放宽成与 `Cloud.AppendEvent` 同形（纯接口放宽，本地路径不变）；② `OpsAgent.History` 依赖 `tasks.Service.List`，而 `edge/worker` **没有本地任务账本**，云端路线需要一个新的 RPC 返回"哪些任务异常结束"——这是唯一真正的新接口；③ `workerInstance.Run(ctx)` 阻塞，监督运行时须并列并随 ctx 收尾。
- **没有顺手做完的理由**：第 ② 步要新增云端 RPC，而本环境没有可跑的云服务器与舰队栈，**无法验证**。在不能验证的地方留下一段看起来能跑的新接口，比诚实地留着不做更糟。
- 折中路径已写明：先做 ①+③（本地可验证），舰队可先获得**机器人级**监督（故障台账、急停、遥测过期、策略拒绝），暂缺任务级的"未确认步骤"。

**验证**：Go 44 包通过；Web **395** 通过；docs/deploy/precheck 24 通过。

### 监督 Agent 验证：14 个故障场景 + 一个真实盲区的发现与修复

把「TaskAgent 执行 / 监督 Agent 观测」这条链路从"有代码"验到"真的有用"：**每个故障场景都断言检出、分类、以及是否给出可执行建议**。

**故障矩阵（`agentruntime/faultmatrix_test.go`，14 个场景）**
- 七类失败分类逐一注入真实故障码：`NAV_BRIDGE_UNAVAILABLE`(TRANSIENT)、`OBJECT_NOT_FOUND`(PERCEPTION)、`TARGET_UNREACHABLE`(PLANNING)、`APPROVAL_REQUIRED`(PERMISSION)、`FENCING_TOKEN_STALE`(RESOURCE)、`TOOL_PARAMETERS_INVALID`(VALIDATION)、`EXECUTION_OUTCOME_UNKNOWN`(UNKNOWN_OUTCOME)、`VERIFICATION_FAILED`(FATAL)。此前只断言了 5 类，**PLANNING / RESOURCE / FATAL 从未端到端验证过**。
- 非错误码类：机器人 `blocked` 故障、急停锁存、物理步骤未确认、阶段超预算、观测过期、完全没有观测、任务异常结束。
- **"是否给了可执行建议"这一条以前完全没有测试**。一个只报"出错了"的 finding 等于一行日志：告诉操作员他本来就能看到的事，而不告诉他该做什么。现在每个场景都断言建议里必须出现具体内容（如 VALIDATION 必须提"参数/版本"而不是"重试"）。
- 矩阵**发现并修掉一个真问题**："观测过期"有建议，"完全没有观测"没有——同一类问题的两个分支行为不一致，后者把 "You're on your own" 交给操作员。修的是代码不是测试。

**发现并修复一个真实盲区（先写测试证明它存在，再修）**
- **问题**：`OpsAgent` 只订阅未来事件，任务集合只由 `OnEvent` 填充。**进程重启后，崩溃前发生的失败它完全看不见**——磁盘上躺着"可能已经动了但没人知道"的物理步骤，监督者报告一个干净安静的机器人。这是监督者最糟的失效模式：**沉默被读成健康**，而"重启后还活着的失败"恰恰最值得复核。
- 修了三层：① 新增只读 port `agentcontract.TaskHistory`（`TaskIDs` + `Abnormal`）；② `OpsAgent.History` + 一次性启动扫描（持久记录的变化也会以事件到达，每 tick 重读是为学不到的东西做查询）；③ **`Finding.TaskID`**——这一层是第一版修的时候漏掉的。
- 第 ③ 层由**端到端测试**暴露：诊断确实产出了，但重启后 `latestTask` 为空，事件的 `taskID` 是空的，**落不进按任务的账本**。现象是"诊断出来了但账本里没有"，只写单元测试这个洞会留到生产。
- `Abnormal` 定义刻意收窄：不含 `CANCELLED`（操作员自己的决定）与 `PAUSED`（例行等待）。算成异常会让监督者变吵，而吵的监督者没人看。两个方向都有测试。
- 新增 `ANOMALY_ABNORMAL_TASK`：任务异常结束但无法定位到具体步骤时**也要报**，否则"知道它结束得不好"会表现为沉默。

**另一个真问题：情况变糟时反而沉默**
- 异常上报有 60 秒冷却（防每 tick 重报，仓库故障台账踩过这个坑）。但身份原来是 `code@component`，所以「1 个任务异常结束」与「5 个任务异常结束」是**同一个身份**，冷却窗口内第二次被抑制——而"数量变了"正是最需要听到的时刻。
- 修复：带计数的 finding 身份包含计数（`ANOMALY_ABNORMAL_TASK@task#5`）；不带计数的保持原身份，稳定条件的冷却照常工作。测试 `TestWorseningSituationIsReportedAgain`。

**端到端可回溯（`TestFaultDiagnosisReachesTheReplayWithAdvice`）**
- 走完整条链：故障 → 监督诊断 → 落同一份按任务的账本 → 回放投影 → 前端。断言回放里能看到：`agent=ops` 归属、`severity=critical`、**建议动作**、**禁止自动重试**、`confidence`、证据链。
- 刻意分开 anomaly（看到了什么）与 hypothesis（意味着什么）：合成一个事件，读者就分不清哪句是观测、哪句是推断。
- 回放投影新增 `recommendedActions`、`automaticRetryForbidden`、`missingEvidence`、`confidence` 四个字段（全部 `omitempty`，既有消费者不受影响）。

**前端（`web/`，+4 条测试）**
- "系统观察与诊断"一节现在渲染：建议动作（有序列表）、**禁止自动重试横幅**（不能是一条普通列表项，扫读时不能漏）、"还缺什么"、置信度。全部经 `textContent`，有测试断言不出现 `innerHTML`。

**验证**：Go 44 包通过（agentruntime 83 条、tasks 53、cmd/local-agent 18，含 `-race` 干净）；Web 385 通过；docs/deploy/precheck 24 通过；gofmt 干净。

**已记录的缺口**：监督能力目前**只在本地单机形态接线**，`edge-worker`（云端多机路线）尚未接同一套；审批队列与超时仍是字符串 `ApprovalID`。见[监督 Agent 验证](docs/architecture/supervision-verification.md)第 7 节。

### 仓库清理 + 全新机器上线方案：冷启动预检与单一入口

**仓库从约 14G 收到 2.2G。** 删的全是缓存、构建产物、运行时残留、可重建数据与一个嵌套仓库；**没有动任何 tracked 文件**（`git status` 无 `D` 记录），清理后 Go 44 包、Web 381、Python 基线全部不变。

- 删除：`.gocache`/`.gomodcache`（1.8G，本次工作产生的构建缓存）、`bin/`（179M 构建产物）、`.worktrees/`（1.8G，两个已完成的 codex 分支工作树，用 `git worktree remove` 注销，分支全部保留）、`tangying-ai-operation-system/`（337M，**独立 git 仓库**、自有 remote、0 个文件被本仓库跟踪）、`datasets/robocasa`+`robosuite`（4.2G，`make robocasa-install` 可重新下载）、`artifacts/maps-archive`（93M 历史地图）、`artifacts/natural-language-eval`+`acceptance`（3.0G 历史产物）、`artifacts/sim-stack/local-agent`（165M 旧任务账本，事故证据已由 `artifacts/incidents/` 保留）、`MUJOCO_LOG.TXT`、`.playwright-mcp`、`logs/`、各类 cache。
- **刻意保留**（仿真环境的地图、robot 模型、家庭场景模型）：`artifacts/sim-assets`（304M）、`artifacts/maps`（29M，含 `furnished-home`）、`artifacts/calibration`（56K）、`artifacts/sim-stack/furnished-home`（96M）、`XLeRobot/`（920M）。`artifacts/maps/furnished-home` 与 `artifacts/calibration/furnished-home` 是一对，单独删任一个都会让导航在启动时报标定不匹配。
- 删除顺序与逐项安全检查：每一项删除前都用 `git ls-files` 确认无 tracked 文件、`git check-ignore` 确认忽略状态；`.gitignore` 忽略的是 `datasets/robocasa/` 而不是 `datasets/` 本身，这一点是查证后才动手的。

**新增冷启动预检 `scripts/precheck.sh`**（只读：不装任何东西、不改配置、不起服务，所以可以反复跑）
- 按角色（`sim` / `local` / `robot-pi` / `cloud`）判定**这台机器能不能装、缺什么**，`FAIL` 才是阻挡，`WARN` 是可选。退出码 0/1/2。
- 补的是真实缺口：`install.sh` 只在失败时说一句 `unsupported platform for <role>`，告诉你不满足但不告诉你有什麼、差多远。
- 平台判定与 `scripts/install/common.sh` 的 `validate_role_platform` 对齐，并由测试钉住"预检不得比 install.sh 更宽松"。
- 版本比较器**先用单元测试写、发现是错的、才修好**：最初的 bash 参数展开版本把 `go1.26.2` 判为早于 `1.26`、把 `Python 3.11.9` 读成 `11.9`。会误判的预检比没有预检更糟——它让人去修一台本来就好的机器。改用 awk 精确提取，12 个用例覆盖（含 `1.26` 不缺省为早于 `1.26.0`、`unknown` 永不放行）。
- 新增 `tests/install/test_precheck.py`（6 条）：版本比较器回归、只读性守卫（检测"命令位置的变更命令"，会打印文件名+行号；已用注入 `pip install` 验证它真的会失败）、角色覆盖、平台行与 install.sh 一致性。

**新增 `docs/operations/fresh-deployment.md`**：新机器/新机器人/新云服务器的单一入口。开头第一件事就是跑预检；三条路线（仿真跑通家庭场景 / 本地单机接真机 / 云端+机器人端），每条给命令、给验证方式、给常见问题。明确了「文档引用的每个路径都实际存在」并由 `tests/docs` 的链接检查强制。

- 记录了三个容易踩的点：家庭场景控制台在 **8897**（`sim-stack.sh` 默认 8787，`home-furnished` 显式覆盖）；`make setup` 用 `python3.11`（可用 `make setup PYTHON=python3` 覆盖）；`cloud` **不是** `install.sh` 角色而是容器栈（`install.sh` 已刻意移除该角色并明确报错）。
- 如实列出不覆盖的部分：RoboCasa 线需要重新下载 4.2G；软件装成功 ≠ 现场已验收（云端与机器人端是两项独立放行结论）。

**验证**：Go 44 测试包通过；Web 381 通过；Python **与清理前基线逐项一致**（59 failed / 35 skipped / 17 errors 不变，passed 1801→1807 即新增的 6 条）；`tests/docs` 与 `tests/deploy` 全通过。存量失败全部是沙箱限制（`/bin/ps: Operation not permitted`，进程生命周期类测试需要 `ps` 读启动标识），清理前即存在。

### Agent 层升级为可扩展多 Agent 运行时（当前启用 task + ops）

把 Agent 层从"一个执行器"改成"可扩展多 Agent 运行时"。**新增 Agent 只需实现接口并注册，不改核心代码**——这条由架构测试机械保证：`agentruntime` 不允许导入任何具体 Agent。

设计上只有一条主线：**运行时是执行的观察者，不是执行的参与者**。任务完成判定、证据要求、重试规则仍然完全由既有闭环契约、失败分类和权限/审批/租约/fencing 决定；运行时慢、错、被关掉，都不改变任何任务结果。

**接口与契约**（`core/agentcontract/`，新增）
- `Agent` 接口：`Name`/`Version`/`Capabilities`/`Subscriptions`/`Permissions`/`Health`/`OnEvent`/`Execute`/`Shutdown`。放在 `core/` 而不是运行时包，是因为 TaskAgent 必须能作为 Agent 使用，而核心执行路径不该因此导入还知道注册表与事件总线的层。
- 事件词表与 payload 结构（用结构体 + `Encode()` 声明，不在各调用点手写 map）：14 个 topic、6 个命名空间。订阅词表外的模式**启动即报错**——拼错的订阅在运行期表现为"看起来健康但从不报告"。
- 三层记忆接口 `Ledger`/`Beads`/`Execution`，为 ExperienceAgent 留好扩展点；**当前只有 `Execution` 是真实实现**（包住既有 `middleware.ExecutionStore`）。

**TaskAgent 是原生 Agent**（`edge/agent/agent.go`）
- `edge/agent.Runner` 直接实现 `Agent`，`Execute` 委托给既有 `RunControlled`——闭环规则一条不少：返回成功仍只是"去看世界"的触发，没有新鲜证据的写操作仍留在 `STARTED`，结果未知仍禁止自动重试。
- `Run(ctx, task)` 保持原签名不动：本地执行生命周期、暂停/恢复路径与十几个既有测试都依赖它，把接口迁移扩大成调用点重写不会买到任何东西。
- 健康检查只报告**能验证**的事：没有执行存储=unhealthy，缺执行端口=degraded，未验证的不返回 HEALTHY。

**OpsAgent：只读观测**（`agentruntime/opsagent.go`、`opsrules.go`）
- **它没有执行端口**——没有 invoker、没有 task service、没有 bus 句柄。"观察者碰不到机器人"是这个类型的性质，有测试按字段清单钉住，加字段就会失败并强制重新决策。
- 确定性规则集（纯函数、可穷举测试、无 I/O、无自己的时钟）：急停、`robot.faults.v1` 的阻塞故障、结果未知的物理步骤、工具失败、步骤超时、观测过期/缺失。
- **复用既有闭环分类器**给失败定 category，不新建第二套分类表：仓库已决定一个失败码只有一个安全恢复动作，观察者再发明一套，两边迟早就"能不能重试"给出不同答案。
- 结果未知时输出 `automaticRetryForbidden: true`——唯一一条不允许被下游软化的建议。
- **第一版对任何恢复建议恒为 `advisory` + `requiresApproval`**，有测试断言。不调用 Python 侧 `fault_remedy.py`，不执行任何恢复动作。

**EventBus / Registry / Orchestrator**（`agentruntime/`）
- 每订阅者独立有界队列：慢的观察者丢自己的事件并计数归属，不阻塞发布者（发布者是正在执行任务的 Agent），也不花掉邻居的额度。队列满时按优先级淘汰，且**只有更低优先级的待投递事件会被顶掉**。
- 权限门控：只读 Agent 的写请求一律拒绝并记 `agent.permission_denied`（没有痕迹的拒绝和"根本没问过"无法区分）。门控不重新推导安全，只回答"提出请求的 Agent 是不是那种可以做这件事的 Agent"。
- **关键物理动作不被抢占**：`SENDING`/`RUNNING` 到终态之间按**计数**标记动作在飞，此期间 `ops.recovery_proposed` 不投递给任何 Agent，改记 `ops.recovery_deferred`；到达安全点后带新身份重新投递。仲裁落在 Orchestrator 而不是 OpsAgent 自己——让 Agent 自我约束会把规则变成建议，而且如果仲裁建立在"Agent 订阅的并集"上，关掉一个观察者就会悄悄关掉保护移动机器人的规则。所以 Orchestrator 自己订阅全部命名空间。

**回放与配置**
- Agent 事件写进**同一份**任务账本（以 topic 作为事件类型），`tasks.AgentEventsFromEvents` 只投影带 `agent` 归属的条目到 `task.experience.v1` 的新字段 `agentEvents`（`omitempty`，既有字段与消费者不受影响），控制台新增"系统观察与诊断"一节。只投影带归属的条目是刻意的：工具活动是执行，不是某个 Agent 对执行的陈述。
- 同一件事两条路都到（Agent 发布 + 账本投影）时按事件身份抑制重复，否则每个动作在回放里出现两次。
- `TANGYING_AGENTS` 默认 `task,ops`；`TANGYING_AGENTS=task` 只启用执行 Agent，是**受支持的部署形态**（运维用来判断观测本身有没有改变行为），等价于接入前行为。写了没注册的名字**启动报错**而不是静默降级；运行时启动失败不阻止 local-agent 启动。
- `tasks.Service.ObserveEvents` 是新增的可选观察者回调：状态提交之后、不持锁调用，返回错误不影响状态变更。

**测试与实测暴露并修掉的真问题**
- 新增 5 个包的测试面：`core/agentcontract`（12）、`agentruntime`（54，含 bus/registry/orchestrator/opsrules/bridge）、`edge/agent` 契约测试（12）、`tasks` 观察者与回放、`cmd/local-agent` 配置、`web` Agent 事件渲染（6）。
- 三条真 bug 是被测试逼出来的，不是事后补的：① 投递循环在 `Subscribe` 之后、首次收取之前收到唤醒信号时会把后续信号全部丢弃并永久阻塞（改为先排空再等待）；② Orchestrator 只看得到"某个 Agent 订阅了"的事件，导致仲裁依赖 Agent 配置（改为自己订阅全部命名空间）；③ `Shutdown` 关闭了发送方仍在使用的 `wake` 通道（race 检测器报出，改为独立的 `stop` 通道）。
- **闭环契约回归**：既有 `core/closedloop`、`edge/agent` 全部测试通过，`runner.go` 的执行逻辑未改。质量门禁新增两条架构断言：运行时不得导入具体 Agent、Agent 契约不得依赖模块内其它包。

**验证**：Go 全量 `go test ./...` 44 个测试包通过（含 `-race`）；Python `make test-python` 通过；Web 381 通过；`make lint` 干净。

### 硬件故障发布成观测：从"报出来"到"摘能力、进世界状态"

上一轮建好了故障词汇表与能力联动，但**没有真实故障走这条路**：运行时不会产生它们，Go 侧也不认识。这一轮让运行时把自己**能自证**的硬件故障变成 `robot.faults.v1` 随观测发布，Go 用契约解码后进 WorldHub——"哪个模块坏了"从此是大脑能读到的一条事实，而不只是日志。详见[本轮记录](docs/development/2026-09-16-faults-as-observations.md)。

**运行时只发布证明得了的故障**（`sim/mujoco/tangying_sim/rgbd_runtime.py`）
- `estop:EMERGENCY_STOP_LATCHED`（`safety`）、`workcell:WORKCELL_CALIBRATION_MISMATCH`（`blocked`，实测支撑高度差 > 15 mm）、
  `chassis:NAV_MAP_NOT_READY`（`blocked`，移动场景没有启用地图）。条件不再成立即 `clear`，故障自己消失而不是留在台账里。
- **`safety` 级故障整体封锁**：急停不是"某个模块坏了"，它撤掉**所有声明了依赖的能力**；唯一幸免的是声明"零依赖"的
  `emergency_stop`（已经停住的机器人仍然必须能被停住）。
- **封锁在最后一遍做**：实测发现 `navigation.navigate` / `navigation.pre_position` / `recover_to_safe_pose`
  此前是"不可用但 blockers 为空"——控制台只能告诉操作员"不能导航"，说不出"因为急停"。现在**每一项不可用都指名故障**。

**Go 侧契约与投影**（`core/robotcontract/faults.go` 新增，16 条测试用例）
- `DecodeFaults` 拒绝：未知字段、`null`、未知严重度/模块种类/处置类、`count` 与条数不符、**标题严重度不是最严重的那条**、
  同一 `module:code` 出现两次、`capabilityBlockers` 与 `unavailableCapabilities` 互相矛盾、blocker 指向不存在的故障。
  理由：这份文档是能力门禁的输入，**自相矛盾的故障表比没有故障表更危险**。
- 观测里根本没有 `faults` 键 = **未知**（遥测照常流动）；发了但读不懂 = **拒绝**——两者不混为一谈。
- 故障随遥测走到 `world.snapshot.v1` 的 `robots[<id>].faults`（深拷贝，读者改不动投影状态），控制台 `GET /v1/world` 可直接看。

**跨语言一致性由测试钉住**（`tests/contract/test_fault_contract.py` + `tests/contract/faults_probe`）
- 真实 sim 运行时（含真实 `EmergencyStop` RPC）产出的文档喂给真实 Go 解码器：`severity` / `count` / `keys` /
  `safetyStopped` / 操作员指令 / 不可用能力集合双方一致；再改坏文档（count、严重度降级、编造 remedy、`ageMs=null`、
  多一个 `rootCause`）逐条确认 **Go 拒绝**。两个语言各写各的测试永远发现不了"我以为你发的是这个"。

**实测暴露并修掉的两个真问题**
- **计数单位错了**：运行时每次观测都重发当前故障，"重复 5 次升级为需要人"的规则会在两秒内把每个自愈故障判成人手故障
  （实测锁存急停 4 秒 `occurrences=10`）。改为 **episodes**：仍在坏只刷新 `lastSeen`，**修好又坏才 +1**；
  被清除的故障记住次数（有界），所以"反复发作"仍然会升级。实测修复后 8 秒内 `occurrences` 始终为 1，`ageMs` 从 125 涨到 8124。
- **三项能力没说原因**（同上），修复后真机栈实测：12 项能力，急停后 7 项不可用**全部**在 blockers 里指名
  `estop:EMERGENCY_STOP_LATCHED`，`emergency_stop` 保持可用。

**验证**：非 e2e 全量 Python 1877 通过 / 35 跳过；Go 全包通过；`make lint` 干净；Web 375 通过；
真机栈（`/v1/world`、`/v1/runtime`）实测干净→急停→重启全流程状态正确。

### 自动恢复引擎：自愈也不能自己说成功

补齐"自动定位 → 自动恢复 → 自动解决"里缺的执行与验证环节。设计与系统一贯的原则对齐：
**恢复是否成功，由"故障消失"判定，不由恢复动作自述**——正如闭环门不相信工具自报成功。

**新增 `robot/gateway/tangying_robot_gateway/fault_remedy.py`（10 条测试）**
- `Remedy`：有界恢复动作（"重新观测""机械臂回零""重连设备""重试命令"），声明它适用于哪些故障码、
  是否会让机器人移动；实现由部署提供，引擎只决定"该不该跑、按什么顺序跑、结果如何"。
- `FaultRemedyEngine.resolve(fault_key)`：按声明顺序尝试，**成功判据 = 复查台账后故障消失**；
  动作返回 "SUCCESS" 而故障仍在 → 记为失败尝试（测试里专门有一条"喊成功但什么都没做"的用例）。
- 四条约束：只跑故障自己声明的 `self_recover`（`operator_assist`/`service_required` 直接升级给人，
  测试断言"人干的活机器人一次都不许试"）；会动机器人的动作在 `robot_can_move=False` 时不跑；
  单故障最多 2 次尝试（与台账的重复 5 次升级叠加）；动作抛异常记为一次失败尝试而不是崩溃。
- 每次尝试都留证据：动作、描述、耗时、错误、故障是否消失——"它自己修过什么"必须可查。
- `default_remedy_order()`：给操作员的去重动作清单（一条总线故障牵连两个模块 = 一件事，不是两件）。

**设计文档补一节**（`docs/architecture/module-health-and-faults.md` §三之二）：自动定位的三层出口
（遥测快照 / 事故记录 / 诊断脚本）、自动恢复的闭环（定位 → 恢复 → 复测 → 升级 → 归档），以及
**仍然不自动做的三件事**：代按急停复位、代插拔线缆、代改标定——这三件的正确反应是**把人叫来**。

**随时定位：`diagnose_task.py --system`**（3 条测试 + 实测）
- 一次拉取运行时就绪/阻塞/逐项能力、事故目录、归类结果，输出**一句话结论 + 依据 + 建议动作**；
  退出码 0/2 可直接接巡检告警。实测（运行中的栈）：结论"历史故障里有未归类的错误码"，
  列出就绪=True、12 项能力、5 起历史事故及其故障族。
- **实测立刻暴露两个真问题并修掉**：① 传输错误（`rpc error: code = Unavailable…`）被当成故障码
  ✗ → 新增 `transport_failure` 故障族并把这类文本规范化成 `RPC_UNAVAILABLE`/`RPC_DEADLINE_EXCEEDED`/
  `CONNECTION_REFUSED`（原文保留为细节）；② 没有错误码的事故被写成"（无错误码）" ✗ → 改为明确的
  `NO_CODE_RECORDED（事故里没有任何错误码，需要补采集）`，直接告诉人缺什么。

### 短文连载改成"讲人话"：受众放大，去术语、上场景

第一批 10 条按"一个点讲透"写出来后仍然偏技术（代码名、术语密度高），受众太窄。整批重写：
**受众改为"对机器人好奇的普通人"**（产品、学生、想转硬件的后端、关心家里老人的子女），
正文从 371–433 字压到 **317–421 字**，术语全部换成生活类比。

**语气规范写进计划**（`artifacts/marketing/讲什么-短文连载计划.md` 第一节）：开头用生活场景；
术语先翻译再出现；数字最多一个且能"心里比大小"；结论句要能被复述；讲踩过的坑而不是"本系统采用"；
技术出处留在计划表与出处表，**不写进正文**。附**去术语对照表**（闭环契约→"干完必须自己看一眼"、
幂等键→"认出这条我处理过了"、fencing→"班次号，前任签字作废"、ATE→"走一圈回到原点差几厘米"…）。
发布前自查：念给一个不做机器人的朋友听，他能不能说出这条在讲什么。

**10 条新钩子**：机器人说"我做完了"你信吗 / 一台机器人的"公司"有八个岗位 /
我为什么不让 AI 自己决定"往左多少厘米" / 给每一步装"行车记录仪" / 停电再来它没有从头开始（置顶）/
机器人做"分布式"跟你想的不是一回事 / 我演练过五种"翻车" / 断网时它会停，这是故意的 /
前任说"我干完了"系统为什么不认 / 有一种失败我让机器人永远不许重试。

规格复核：全 10 条标题 ≤20 字、正文 317–421 字、零 markdown 残留，可直接整段复制。

### 宣传改为短文连载：长文拆成每天一条，并为论文留骨架

两期长文（3046 / 5501 / 4292 字）数据不好——这个平台的阅读发生在几秒钟里。改为短文连载：
**一条一个点，350–550 字，纯文本可直接粘贴**。见 [连载计划](artifacts/marketing/讲什么-短文连载计划.md)
与 [shorts/](artifacts/marketing/shorts/README.md)。

**规则**：标题 ≤20 字；正文 350–550 字（超过就拆）；结构=钩子→为什么→怎么做（带真实数字）→收尾；
一张真实配图；一行仓库链接；4–6 个标签；**每个技术说法指得出实现或测试**。求职诉求只在置顶那条
（S05）与每周一条里出现。

**已完成 10 条（够发 10 天）**：S01 返回"成功"不算做完 · S02 八层架构 · S03 为什么不让模型碰坐标 ·
S04 每一步都留能翻旧账的证据 · S05 断电之后接着做（置顶）· S06 分布式不是因为高并发 ·
S07 五个故障剧本 · S08 断网之后机器人会停 · S09 旧主的上报会被拒 · S10 结果未知永不重放。
每条 371–433 字、标题 ≤19 字、无 markdown 残留，全部来自第 01/02 期长文的拆分与仓库里可查的证据。

**论文骨架已排好**：引言（S01/S06/S11）、系统设计（S02/S03/S07/S12）、实现（S04/S09/S10/S14/S15/S22）、
评测（S16–S21 对应 `evaluate_slam.py`、探索覆盖 38.9%→81%、语义层基准、故障矩阵）、讨论与局限
（各升级文档的"仍未解决"）。**诚实提醒写进文档**：还差相关工作、统一实验协议、与外部系统的对比三项，
不补这三项，短文拼起来只是技术报告。

### 模块化机器人的故障上报与自愈设计（含可落地第一片）

XLeRobot 由底盘/双臂/夹爪/头部/相机/总线/上位机等模块组成，任何一块都可能坏，而现状是零散字符串
（ARM_FAILED、SERIAL_PORTS_UNAVAILABLE、HARDWARE_ERROR）——**没有模块身份、严重度与处置方式**，
大脑只能说"出错了"。本轮给出方案并落地第一片。详见[设计文档](docs/architecture/module-health-and-faults.md)。

**设计（五层）**：① 模块身份进 `robot.profile.v1`（`moduleId`/`kind`/`capabilities`/`dependsOn`/`selfTest`/
`repairClass`）；② 故障作为**观测事实** `robot.faults.v1`（模块、码、严重度、首见/最近一次、次数、
用户指令、证据）；③ **能力联动复用已有门禁**（运行时标 `available=false` + `blockers`，Go 侧计划期
失败关闭）——故障词汇只存在于 Python 一处；④ 处置阶梯 `self_recover`/`operator_assist`/
`service_required`；⑤ 用户提醒说清"现状 / 还能做什么 / 该做什么"。检测分四层（驱动、运行时、任务、
周期自检），自检默认不带运动。

**两条硬规则**：LLM 只能执行故障自己声明的 `self_recover`（请人处理永远是允许的，那是消息不是运动）；
**自愈不得掩盖故障**——同一故障重复到 5 次自动升级为需要人，因为每十分钟要复位一次的模块即使还能动
也是坏的。

**已落地第一片**（`robot/gateway/tangying_robot_gateway/module_faults.py` + 13 条测试）：模块种类/
四种严重度/三类处置的词汇表、`Fault` 校验、`FaultLedger`（去重、有界、首见与最近一次分别记录、清除、
自愈升级）、`capability_impact()`（把故障翻成能力封锁）、`snapshot()`（控制台与大脑读的那一份）。
测试覆盖：模块坏了正好封锁依赖它的能力、底盘故障不牵连机械臂、`info` 不封锁、多模块同时坏各自列名、
自愈重复到阈值升级、重复故障保留首见时间、非自愈故障不允许自动处置、清除已修复模块、畸形数据被拒、
台账满时明确报错而不是悄悄丢弃、`emergency_stop` 不依赖任何模块因而永远可用。

**明确不做**：让 LLM 写寄存器/改标定/复位急停、用自愈掩盖劣化、严重度未知时降级继续跑、故障只写日志
不进世界状态、为每种模块新增专属上报路径。

**落地顺序**（第 2 步前不改变任何现有行为）：1 词汇表与台账（已完成）→ 2 运行时把现有硬件码统一成
`Fault` 并进 telemetry → 3 `robot.health`/`robot.self_test` 工具与控制台模块面板 → 4 事故记录带故障
快照 + `diagnose_task.py` 加硬件故障族 → 5 周期自检与 `dependsOn` 级联。

### 仓库整理：docs 归类、新人入口、artifacts 与 marketing 的边界

按"同一类型文档放一起、方便新人上手"整理仓库，全程以仓库自带的两个检查器验证。详见[整理记录](docs/development/2026-09-16-repository-organization.md)。

**`docs/` 归类（16 篇各归其位）**：`architecture/`（architecture、protocols、middleware、agent-v1、
orchestration、distributed-agentos、multi-robot、fleet-cloud、fleet-paper-loop）、`operations/`
（deployment、production-readiness、safety-checklist、robocasa-handoff）、`guides/`（quickstart、
user-console）、`install/`（xlerobot-setup）。根目录只剩索引 `README.md`。
- 116 处链接被重写（出向 + 入向 + 跨移动文件 + 非 markdown 目标），文档链接检查通过；`tests/test_repository.py`、
  `scripts/start-all.sh`、`tests/install/test_start_all.py`、`cmd/edge-worker/main.go`、
  `scripts/calibrate_xlerobot.py` 里的旧路径同步更新。
- 第一次尝试只改了入向链接，被链接检查器当场抓出被移动文件的出向链接 ✗，回退重做——记录在案。

**索引重排（`docs/README.md`）**：新增**新人第一小时**（看主张 → 跑起来 → 看一次完整任务的证据 → 读架构与
闭环契约 → 改第一行代码）与**仓库地图**（每个目录放什么、谁维护、会不会入库）；把 12 行逐轮升级记录从
"按任务阅读"表摘出，另立**升级与审计记录（按日期）**一节，主线表只剩 25 行任务。根 README 加入口指引。

**artifacts 与 marketing 的边界写清**：`artifacts/` 在本仓库的含义是"不参与产品构建与测试的非产品材料"
（脚本产出的证据 + 人工撰写的对外材料）。营销材料严格按"artifacts=生成物"的口径应放顶层，本仓库选择保留
现址的三条理由（已被规则写清、引用稳定、一期一目录风格一致）与"想改怎么改"（`git mv` + 同类链接重写）
一并写进文档。另补**模型产物放哪**一节：权重不进仓库，进仓库的是"用哪个权重"的契约与哈希。

**修掉仓库检查自身的一个 bug**：顶层区域归类检查用 `git ls-files` 按行解析，而 git 默认给含非 ASCII 的
路径加引号，中文目录名因此解析出伪顶层目录 `"artifacts`（任何文档都无法归类）。改用 `git ls-files -z`
按 NUL 切分。

**验证**：文档链接与 make 目标检查 5 通过；顶层区域归类/发布树 154 通过 1 跳过；全量 Python 1478 通过
28 跳过；Go 全包通过；Web 375 通过；`make lint` 干净。

### 异常终态自动产出事故记录 + 自动扫掠分类（AI 自动运维的第一段闭环）

补上上一版两个关键缺口："要人工按需调用"与"诊断只活在接口里"。

**Go `incidents/` 包 + `internal/localapp` 钩子：异常终态自动写 `incident.bundle.v1`**
- 任务到达异常终态（可恢复失败/安全失败/取消/运行错误）时自动写出
  `artifacts/incidents/<taskId>.bundle.json`，内容全是事实：任务身份与终态、恢复判定
  （canResume/requiresReconciliation/reasonCode）、环境指纹（软件/目录/地图/标定修订）、时间线
  （含每步参数与错误）、步骤执行历史、证据引用（含 rgb/depth 哈希）、耗时报告（原样，没有就是 null，
  从不编造）。
- 写入尽力而为且**不改变结果**（失败只记日志）；写临时文件再 rename，读者看不到半份记录；
  默认保留最新 200 份并按时间淘汰；目录是部署参数 `TANGYING_INCIDENT_DIR`（车队可指向共享卷）。
- 记录自带说明"只包含事实…不修改任何东西"——不假装自己修好了什么。

**`scripts/diagnose_task.py` 增加 `--bundle` 与 `--sweep`**
- Go 只写事实，**故障族知识仍只在 Python 一处**（另一种语言的第二份拷贝必然漂移）。
- `--bundle` 把 Agent 写的 bundle 分类成 incident.v1；`--sweep <dir>` 批量分类并打印每条故障族与
  错误码，未归类计入返回值（非零退出码可用于巡检告警）。

**实测（运行中的栈，非演示）**：提交一个必然失败的请求（"从客厅出发，去卫生间确认一下环境"，地图无该
区域）→ 任务 RECOVERABLE_FAILURE → **Agent 自动写出 bundle（零手动步骤）** → sweep 分类为
`goal_or_localization_unclear`（命中 GOAL_NOT_CLEAR），给出 4 条可能根因、3 项先查操作与覆盖该族的
回归测试；全过程未修改代码、未动机器人。证据：`artifacts/incidents/`。

**测试**：Go `incidents/bundle_test.go` 5 条（事实与"未修改"声明、缺任务身份被拒并自填时间戳、空切片
序列化为列表、旧记录被淘汰、摘要可区分不同故障）；Python `tests/test_diagnose_task.py` 增至 12 条
（bundle 交接、非本契约被拒、扫掠分类与未归类计数、空目录）。

**仍缺**：每次运行的完整耗时序列仍不落盘（只落了故障时刻的快照）；世界历史（本地 WorldHub 是内存
环形）仍无法回放；扫掠尚无定时任务在跑。

### AI 回溯诊断：incident.v1 记录 + 确定性故障族分类（含真实失败任务验证）

回答"出故障能不能让 AI 回溯根因、能不能让 coding agent 自动复盘"。结论：证据够，但今天不是
"一条故障=一条可诊断记录"——事实散在五个接口、且缺"可能根因 + 该跑哪些测试"的知识层。本轮
把这条链补齐到机器可直接消费。详见[设计文档](docs/development/2026-09-15-ai-incident-diagnosis-and-ops-loop.md)。

**新增 `scripts/diagnose_task.py`**（可连真实控制台，也可离线跑 fixture）产出 `incident.v1`：
任务身份与终态、恢复判定、环境指纹、时间线、每步四段耗时、证据采集（含哈希）、观察到的错误码，
以及 `diagnosis`（故障族 + 命中码 + 未归类码 + 可能根因 + 先查项 + 建议处置 + 覆盖该族的真实回归测试
+ 是否需要人工）。三条原则：**事实与推测分开**、**不知道就说不知道**（未知码进 `unclassified`
并给出 `missingEvidence`，不猜根因）、**只提议不行动**（`automation.acted` 恒为 false）。

**7 个故障族**，每族配真实回归测试：`physical_outcome_unknown`、`verification_not_observed`、
`goal_or_localization_unclear`（含参考驱动的逐脉冲拒绝码）、`mapping_session_fault`、`safety_stop`、
`grounding_failure`、`fleet_consistency`。有测试专门断言"每族引用的测试文件与测试名都真实存在"。

**真实验证**：对实际失败任务 `task-35ec47d5652d8bd669b82d61` 跑出完整记录——正确识别两个错误码
（`PLACEMENT_NOT_OBSERVED` + 未归类的 `DESTINATION_NOT_FOUND`）、归入 `verification_not_observed`、
给出 16 份证据采集、并从 P0-1 的耗时接口读出最慢能力。

**测试**：`tests/test_diagnose_task.py` 8 条（分类与根因、未知码不猜、不确定物理结果标记人工、
测试引用真实性、多故障不隐藏第二个码、永不声称已行动、缺接口时仍能产出、同族多状态归并）。

**仍缺（写进文档，按优先级）**：耗时统计与世界历史不持久（本地 WorldHub 是内存环形，重启即丢——
"当时系统认为世界是什么样"回答不了）；变更指纹（工具目录/策略 manifest）未完整进记录；故障族表需
人工维护；incident 尚未接进任务终态钩子自动产出。

### 可扩展性落地（一）：物体物理属性成为数据、地图冲突只读可查

按上一轮评审的建议，先做两个"只加数据、不改结构"的扩展点。详见[评审文档](docs/development/2026-09-15-extensibility-review-tools-and-navigation.md)第三节。

**扩展点 ① · 物体物理属性**（新增 `physical_attributes.py`）
- 词汇表：`material ∈ {rigid, soft, fragile, deformable, granular, unknown}` + 可选 `mass_g`、`max_grip_force_n`，复用现有 `attributes` 字符串通道，**不改协议**。
- 预算推导：`grasp_budget()` 给出容差 / 最大夹持力 / 合爪速率系数。**未声明即与今天逐位一致**（默认值就是被替换掉的那两个常量）；`fragile` 收紧容差、限力 6 N、合爪更慢；物体自报上限优先于材质默认。
- 可诊断拒绝：未知类别不再 `KeyError`，改为 `PLACEMENT_PROFILE_UNAVAILABLE` 并列出已知类别；非法声明分别是 `PHYSICAL_ATTRIBUTE_UNKNOWN` / `PHYSICAL_ATTRIBUTE_INVALID`。
- 记录：本步预算随状态发布（`grasp_budget`），"为什么抓得这么轻"可从记录回答。
- 测试 8 条：未声明=旧行为、fragile 收紧、自报上限优先、字符串质量解析、五种非法声明各自原因码、未知类别列出已知集。

**扩展点 ④ · 地图冲突只读报告**（`mapping.conflicts` 服务）
- 把最近采集的点与在用地图逐格比对，报告"地图说自由、观测说占用"的格子数、反向清空格子数与冲突比例，并给出 `suggestsRescan` 建议。
- **只读**：只统计与返回，不改写地图（测试断言地图数组逐位不变）；要更新仍走 `mapping.start {baseMapId}` 续建，保持"昨天能走今天为什么不能走"可解释。
- 测试 2 条：冲突计数与只读性、无地图/无测量时明确声明不可用。

### 可扩展性评审：工具封装（软/硬/易碎）与导航受阻（地图过期、路径被挡）

回答"这样封装工具类好不好、导航到不了怎么办、v1 要不要现在做复杂"。结论：**结构对、缺两条轴**，并给出四个"只加接口、缺省不变"的扩展点。详见[评审文档](docs/development/2026-09-15-extensibility-review-tools-and-navigation.md)。

**现状（读代码得出，非印象）**：一个能力由五处共同定义——规范工具名（Python/Go 两侧白名单）、参数契约、安全分类（`PHYSICAL_TOOLS`/`MUTATES_WORLD_TOOLS`）、技能清单（安全级别/必需参数/审批策略/是否改世界/租约）、本体声明（`RobotProfile.tools`）。
- **已留好的扩展口**：`ActionParameters.policy_execution` 已能表达"用某个具体权重执行"（deterministic/vla/imitation/reinforcement + 权重哈希 + manifest）；每台机器人的能力可裁剪（没有吸盘就在计划阶段失败关闭）；工具层与运行时面分离。
- **写死的轴**：物体类别是**运行时代码里的字面量**（`half_height = {"cup": 0.06, "bottle": 0.08}[category]`、`category in {"cup","bottle"}`），换物体等于改代码且失败不可诊断；全仓库没有材质/易碎/可变形字段；抓取力与后置条件是固定的。

**导航受阻**：保护层是真的（逐脉冲同帧深度证据、未知格不通行、认证净空目标、DWB 局部体素层 marking/clearing、物理结果未知永不重放），**缺的是决策与记忆**：任务期不改写在用地图（刻意的，用续建流程代替）、"被挡住"只是拒绝码而非可决策事件、计划层没有重试预算/策略、`plan_work_area` 的候选位姿还没进 Go 计划。

**v1 只加接口的四个扩展点**：① 物体物理属性作为观测数据（`attributes` 加可选 `material/mass_g/max_grip_force_n`，未知即沿用现行为）；② 抓取策略注册表 `(category, material, gripper_kind) → strategy_id`（第一版只有参考控制器一条）；③ 受阻原因结构化 + 步骤级重试策略（第一版只实现 replan/abort）；④ 地图冲突**只读**事件（`MAP_STALE_SUSPECTED` + `map.conflicts()`），v1 不自动改图。

**明确不做**：为每种材质新增工具名、让模型输出夹持力/关节角、任务期自动改写在用地图、引入第二套导航实现。

### PLACEMENT_NOT_OBSERVED：定位、失败自证、房间目标认证

上一轮审计把这条列为最高优先级（它决定"能不能把活干完"）。本轮完成定位与两处修复，并**实测到抓取链首次全绿**。详见[升级文档](docs/development/2026-09-15-placement-verification-and-certified-goals.md)。

**恢复语义（实测）**：失败任务的恢复视图是 `RECOVERABLE_FAILURE` + `canResume: true` +
`requiresReconciliation: false` + `RESUME_AVAILABLE`——放置动作已被闭环门确认，失败的是只读核验步骤，
所以不需要对账。**实测续跑**证实了这一点：事件序列跳过 pick/place 直接重新感知与解析，**物理动作一次
都没重放**。

**根因**：杯子物理上就在收纳盘内（水平偏差 5–8 mm、底部与盘面同高），失败发生在"核验时没取到 3 个
连续稳定样本"，而系统当时**没留下任何可判读的原因**——只有一行 `PLACEMENT_NOT_OBSERVED`。

**修复 1 · 失败的核验必须自证**：`_verify_relation` 记录完整判定（`passed`、`sample_count`、
`expected_relation`、`observed_relation`、`stable_duration_s`、`max_displacement_m`），随结果 payload
返回、写入发布状态与遥测；失败消息变成
`observed 1/3 stable samples of 'inside:kitchen-tray'; last relation 'none', max displacement 0.0000 m…`。

**修复 2 · 房间目标必须被地图认证**：实测中抓取链全绿后，返回客厅的最后一段导航以 `GOAL_NOT_CLEAR`
失败（登记点正落在勘测起点那块未认证地面上）。运行时现在发布房间目标时把落不到认证净空地面的目标
**吸附到同房间内最近的认证位姿**（朝向不变），并发布 `goalAdjustments`（`adjusted`/`offsetM`/`commissioned`）
——计划下达的、`verify_arrival` 核验的、读者看到的始终是同一个位姿；找不到认证位姿时如实报
`adjusted: false`，绝不悄悄挪到地图从未批准的位置。

**测试**：失败核验自证 1 条、房间目标认证 3 种情形（吸附、已认证原样、无认证位姿明确标记）1 条。

**如实记录一次无效运行**：修复 2 之后的确认运行因仿真栈尚未就绪（建图脚本 `Connection refused`，任务
跑在旧地图上）而无效，不作为结论；修复 2 的端到端确认仍待一次干净的栈启动。

### 机器人异常处理审计：能否恢复、异常是否入表（附一处真实缺陷修复）

按要求审计分布式系统对机器人异常的处理，逐项验证"行为是否正确 / 任务能否恢复 / 异常是否入表"，
并对没有测试的异常补测试。结论与对照表见[审计文档](docs/development/2026-09-15-robot-fault-handling-audit.md)。

**实跑结果**：云侧故障矩阵 `tests/e2e/test_fleet_faults.py` **11 通过**（10 类故障 + 断线重连集成）；
RoboCasa + 本地恢复 `test_robocasa_faults.py` / `test_rgbd_recovery.py` **10 通过 / 2 跳过**（缺可选依赖）；
Go 全包通过；Gateway 522 通过；`make lint` 干净。

**结论**：行为是"失败关闭"（物理结果未知永不重放、低 fence 写不进、观测有洞要求 resync、急停不能被
任务动作解除）；恢复分三类（重试即可 / 暂停重启后继续且不重放已完成步骤 / 不可自动恢复需人工核对）；
异常落在四层持久记录（任务事件流、步骤执行历史、证据库、世界快照）。

**修掉一处真实缺陷**：会话级故障（`STALE_CAPTURE`、`CALIBRATION_CHANGED`、任何非预期异常）此前会
**丢弃已测绘的整张地图**——实测一张已探明 74% 的地图完全没保存。现在抽出 `_handle_session_fault`：
有测量就**尽力发布已测绘部分**，并在 provenance 记 `partial: true` 与 `fault{code,message}`
（非预期异常记 `WORKFLOW_FAULT` + 类型）；什么都没测到则仍然不写空地图。地图的持久记录因此能自己
说明"这是被中断的部分地图"，不必去问产生它的服务。

**另有两处补测试**：急停锁定必须在恢复视图里被拒（`CanResume=false`、`reasonCode=SAFETY_STOPPED`、
事件在库）；干净的可恢复失败应给出 `RESUME_AVAILABLE`（并顺带确认状态机不允许从 READY 直接跳
`RECOVERABLE_FAILURE`——"没开始过的任务不算可恢复"）。

**仍未覆盖（写进文档）**：`PLACEMENT_NOT_OBSERVED` 无测试（本轮实跑第一次触发，直接影响"能不能把活
干完"，优先级最高）；`recover_to_safe_pose` 无真实回位轨迹；协调器"提交后立刻崩溃"的组合场景只有
outbox 间接覆盖；故障矩阵尚未接进默认 `make test`。

### 三项收口：勘测回望、任务前置摆位、回忆新鲜度成为部署参数

上一轮记录的三条待办逐项处理，每项带对照实验，详见[升级文档](docs/development/2026-09-15-survey-lookback-and-pre-position.md)。

**① 勘测回望**：起点脚下那块地是前向相机永远看不到的地方，建成的地图随后拒绝从该位姿发起的第一段导航（实测起点 0.32 m 邻域 13 个未知格）。勘测路线现在走完客厅后**保持朝向后退 0.6 m 回望**（驱动每次只允许 0.5 rad 朝向变化，转身回望发不出去）。实测未知格 **13 → 6**；剩下的是底盘自身脚下的那块，任何单一视角都看不到——因此交给 ②，而不是放宽净空判据。

**② 任务前置摆位（新能力 `navigation.pre_position`）**：先查清 `NAV_ROTATION_LIMIT` 的真实成因——不是当前位置不干净，而是**目标朝向**（驱动要求终点朝向与当前朝向差 ≤ 0.5 rad）。新增一步：① 不在认证净空地面就在 1.2 m 内按环搜索并挪过去；② 给了 `alignYaw` 就**原地分步转**（每步 ≤ 0.8×上限）；③ 全程走普通有界脉冲导航，安全准入/新鲜度/扫掠净空一律不变。契约只允许传"朝哪边"，不允许传位置（运行时选地方）。计划在 `observe` 之后、首段 `navigate` 之前插入该步并传入首个目标朝向。
- **对照**：同一张新地图、同一任务，唯一差别是计划里有没有这一步。改动前第一段 `navigation.navigate` **FAILED `NAV_ROTATION_LIMIT`**；改动后 `pre_position` CONFIRMED（alignYaw=1.421）→ 首段导航 **CONFIRMED** → verify_arrival CONFIRMED → pick/place CONFIRMED。**"起步即失败"消除，且没有放松任何安全判据**。
- 顺带暴露下一个真实阻塞：任务第一次跑到 `verify_placement` 返回 `PLACEMENT_NOT_OBSERVED`（与本次改动无关，已列为下一条待办）。

**③ 回忆新鲜度成为部署参数**：`TANGYING_RECALL_GOAL_MAX_AGE_MS` 覆盖默认 15 分钟（上限 7 天），非法值一律回退默认（"没设"与"打错字"不能都表示"什么都信"）；实验脚本每次报告里记录 `recallWindowEnv`，让两臂可比。单元对照：超出默认窗口 1 分钟的目击默认被拒、24 小时窗口下被接受并如实上报 `recallAgeMs`。

**仍未解决（如实记录）**：本轮两臂任务级对照**仍未取得差异**——地图里物体层有 9 个 `cup` 实例（含真实杯子 (2.24, 3.39)、vantage (2.24, 2.77)），但任务事件 `goalSource` 仍是 `commissioned`，即运行时没把 `semantic_recall` 送到落地环节（可复现的断点，下一步一次 curl 即可定位）。

验证：Python 1662 通过 / 34 跳过；Go 全包通过；`make lint` 干净；`tools.json` 校验通过。

### SLAM 探索：让机器人真的能扫完一整个家（38.9% → 81%）

用户反馈"很多房间进不去"。诊断后的结论是：**门不是瓶颈，预算与目标选择才是**，另外还有一个只有实跑才会暴露的崩溃点。详见[升级文档](docs/development/2026-09-15-slam-exploration-coverage-upgrade.md)。

**诊断（实测）**：门宽 3.0 / 1.5 / 1.5 / 1.21 m、走廊净宽 1.55 m，而底盘只需约 0.7 m；前三个门线都有实打实的自由通道。22.3 m 的探索里**卫生间轨迹点为 0**（74% 未知），而已经 0% 未知的走廊与 11% 未知的厨房吃掉了一半以上里程——不是进不去，是**没往那儿去**。

**四处改动**

- `exploration.py`：新增区域尺度评分 `region_gain`（连通块格数 + 局部未知）与 `FAR_REGION_FACTOR=2.5`——近处一圈优先的规则保留，但远处有**明显更大**的未测绘区域时值得开过去。（第一版我按"效用/米"比较，效用除以距离永远偏向近处，被测出来的失效抓了回去。）
- `DEFAULT_EXPLORE_TRAVEL_M` 40 → **75 m**（全屋一轮 50–60 m）。
- 关键帧间距 0.10 m/0.16 rad → **0.14 m/0.22 rad**，上限 400 → **600**，字节预算 8/12 MB → 12/16 MB：400 帧会在 50 m 的屋子中途截断，而点云仍来自每个被接受的采集，**降密度不降质量**。
- 深度点不足（<100 点）从"抛异常终止整段"改为**跳过并计数**，连续 25 帧才优雅结束该段并发布成果：实测一次对着近墙的空白视野让一段在 74% 处崩掉、**地图完全没保存**。

**对照（同场景同入口）**：已探明 **38.9% → 81%**，整图未知 61% → **19%**，卫生间从 **0% 轨迹点 / 74% 未知** 变成 **11% 轨迹点 / 20% 未知**，走廊与厨房降到 0% 未知；同样 21–22 m 里程时已探明 **38.9% → 66%**，说明起作用的是选择策略而不只是预算。关键帧 263/600。产物：`artifacts/slam-exploration-coverage/`。

**测试**：探索选择器新增 2 条（整间房胜过脚边缝隙、仅略大的远处区域仍然等待），并把既有"站在柜子旁先捡便宜"的断言改成新策略并注明原因；深度不足可存活性 1 条。

**仍未解决（写进文档）**：整图 19% 未知多为家具背后与相机盲区；leg 1 仍在 39% 报 `no_reachable_frontier`（门框附近自车体点云/幽灵占用判死通道，值得单独立项）；100 点阈值未在真实相机上标定；只测了一个场景，下一步要用窄门户型做自变量重复对照。

### P0-1 / P0-2：上机性能遥测（四段耗时）与"回到上次看到它的地方"

按 [系统评价与重点优化方向](docs/development/2026-09-15-optimization-backlog.md) 的优先级开工。**P0-1 已交付并在线验证；P0-2 代码与机制测试完成、真实产物已验证，任务级 A/B 因三个环境发现未能取得可用数据（已在升级文档中如实记录）。** 详见 [升级文档](docs/development/2026-09-15-latency-and-recall-goal-upgrade.md)。

**P0-1 上机性能遥测**

- 新增 `latency/`：有界环形缓冲（4096 条）+ **排队 / 安全准入 / 执行 / 证据核验**四段计时，按工具、安全级别、机器人、结果分组，给出 p50/p95/p99（最近秩，不外插）；溢出条数如实上报（`droppedSamples`），不静默丢弃。
- `edge/agent/runner.go` 在**每个出口**记录：完成、拒绝、失败、**物理结果未知**（"很慢然后失联"正是最该看到的情形）。观测不到不影响执行——记录器为空时任务照常跑。
- 控制台新增 `GET /v1/telemetry/latency?groupBy=&windowMs=`；未配置采集返回 `LATENCY_UNAVAILABLE`（"没测"不能长得像"都很快"），非法分组/窗口 400。已写入 `docs/production/api-reference.md`。
- **实测开销**：记录一条 **8.6–14.9 ns/op、0 alloc**；一次聚合读（500 样本）**71–79 µs**。据此判断不需要开关——10 步任务的记录成本约 10⁻⁴ ms。

**P0-2 目的地改用"上次看到它的地方"**

- `object_memory.py` 每次目击**同时记录当时底盘位姿**（`observedFrom`）：物体位置不是目的地，底盘不能开进桌子；能开的是当时站过、看得见它的那个位姿。支持平面里程计与四元数两种输入，重锚定时位置与朝向一起变换。
- 新契约 `semantic.recall.v1`：驱动按类别发布"上次看到"记录（`pose` 仅作记录、`vantagePose` 可导航 + 年龄 + 目击次数），**文档级身份校验**，不属于当前地图就拒识并给出 `semantic_recall_error`，观测本身不受影响。
- `edge/robotclient` 优先用**新鲜**（≤15 分钟）的 vantage 作为操作检查点目标，过期/缺失/无 vantage/越界一律回退登记点；来源写入 `GROUNDING_EVIDENCE` 任务事件（`goalSource` + `recallAgeMs`），审查与实验读同一条记录。`verify_arrival` 仍核验**实际下达的位姿**。
- **真实产物已验证**：探索地图上 `cup` 物体 (2.27, 3.41)、看到它时底盘位姿 (1.92, 3.00, yaw 1.42)（登记点 (2.05, 3.00)）。

**本轮暴露的三个真实问题（已写进升级文档，作为下一步）**

1. 探索/勘测结束时的底盘位姿可能让第一步导航被 `NAV_ROTATION_LIMIT` 拒止；
2. 15 分钟新鲜度上限意味着两臂对照必须在制图后 15 分钟内完成（前两次尝试因此按设计回退登记点）；
3. 勘测起点 0.32 m 邻域内有 13 个未知格（探索地图 8 个），从该位姿发起的任务被 `LOCALIZATION_NOT_CLEAR` 拒止——"你脚下那块地你没看过"。

**同时修掉一个只有真实运行才会暴露的 bug**：物体层的身份写在文档级而非条目级，最初按"每条目带 mapId"过滤，单元测试用的假数据也这么造，于是**测试全绿而真实地图一条都过不了**（`goalSource` 一直是 `commissioned`）。已改为校验文档级身份，测试改用真实文档形状。

### 地图开始记住"物体在哪"：`map.objects.v1` 与工作区可达位姿

语义层此前只回答两个问题：有哪些**地点**（`map.semantics.v1` 的房间工作区），以及有哪些**可操作对象**（运行时目录，而且**故意不含测量位姿**）。没有人回答第三个问题——**这个东西上次是在哪儿看到的**。于是任务一旦没站在能看见目标的位置，就只能报可恢复失败。这一轮补上这一层，并顺手把"目的地"从单点变成可达位姿集合。

**1. 物体层随地图发布（`robot/gateway/tangying_robot_gateway/object_memory.py`）**

建图期间（`_sample` 以 1 秒节拍）向驱动索取一次感知实体，按地图锚点折算到**地图坐标系**累积；发布时再按最终锚点重新锚定，写进地图产物 `objects.json`（`map.objects.v1`）。三条规则让它算证据而不是传闻：

- **只有观测进来的才算**：commissioned 目录依旧没有测量位姿，这一点没变；
- **每次目击都带时间**：`lastSeenUnixMs` / `ageMs` / `sightings` 逐条给出，检索按年龄过滤，过期条目不会出现在发布层里；
- **关联有上限**：同类别、属性不矛盾、地图坐标距离在 0.12 m 门限内才归为同一个实例——**被移动的物体会变成新实例，而不是瞬移的旧实例**。

续建（`baseMapId`）会继承上一段地图的物体层，并保留各自的时间戳（旧目击不会因为被继承就变新鲜）。驱动侧绑定为 `WorkflowBindings.observe_entities`（一次普通观测，纯读取）；探测失败只记进会话，不会打断正在行驶的勘测。

**2. 目的地 = 可达位姿集合（`plan_work_area` / `navigate_to_work_area`）**

`workspace_planner` 与 `map_catalog.plan` 早就存在，但**只在测试里注册**。现在两者都进了生产工具面（28 个工具）：

- `plan_work_area(location_name)`：按当前地图、底盘足迹、机械臂包络给出**排序后的可达底盘候选**（只规划，不动）；
- `navigate_to_work_area(location_name, candidate_index)`：**组合技能**——先规划，再通过原子工具 `navigate_to_pose` 驱动到某个候选，并**回报实际下达的位姿**。它不偷偷改目标：调用方必须用返回的 `final_pose` 再做一次到达核验，闭环契约因此对"真正下达的动作"成立，而不是对一个机器人从未去过的点成立。

没有配置地图提供者时，这两个工具**依然注册**并给出明确拒绝（`no deployment map provider …`），而不是从工具表里消失——能力必须可被发现。

**3. 地图可放大看细节（控制台）**

地图面板新增"地图里的物体"一节与图层：物体标签**随相机距离出现**（≤4 m，可在面板勾选开关），形如 `white cup · 3 小时前`；没有物体层的历史地图会说明原因，几何与关键帧不受影响。点击条目即把相机定位到该物体。

**量化对比实验（本轮起，模块升级必须给出对照数据）**：`scripts/benchmark_semantic_layer.py` 用同一张地图、同一个脚本化机器人、同一个检测器跑 baseline / upgraded 两套配置，报告见 `artifacts/semantic-benchmark/run-1/report.json`，机制与结论见 [`docs/development/2026-09-14-semantic-object-layer-upgrade.md`](docs/development/2026-09-14-semantic-object-layer-upgrade.md)：

- **复杂任务**："拿杯子放进收纳盘"（检测器只在真值 0.6 m 内报告目标）交付成功率 **0/5 → 5/5**。baseline 走到登记点后无路可走（3.76 m 白跑）；upgraded 多走 4.3 m，把失败换成成功——**收益不是省时间，是把 0 变成 1**；
- **运行时间 / 响应**：`recall_object` 中位 **1.43 ms**（P95 1.64）；`plan_work_area` 中位 **9.05 ms** 给出 **8 个可达候选**；轮询复用 `observe_scene` 的观测路径，一次感知中位 **41.7 ms**，1 Hz 下占 4.2%；
- **占用资源**：物体层 **606 B/条**、真实勘测地图 **3,367 B**、地图产物 +0.33%，检索峰值内存 1.6 MiB；512 条上限时单次轮询关联 **53.9 ms**（线性关联是已知瓶颈，下一步换空间索引）；
- **真实勘测交叉验证**（`scan-9bd97f00b5c4`）：130 次轮询 / 12 次目击 / 3 个实例，收纳盘与陶瓷杯位置误差 **0.5 cm / 2 cm**；同一次勘测出现 1 个"仅 1 次目击"的误报实例，正好说明 `sightings` 字段的必要性；
- **为什么这样提升**：位置与年龄绑定（记忆 ≠ 事实）、关联门限让"被移动"可见、目的地由单点变成候选集（把地图质量与任务可行性解耦）、组合技能不新增能力；
- **本层消费者目前是工具层/MCP**：Go 任务计划仍按登记点下发导航，要吃到这份收益需在计划里插入"回忆 → 规划 → 导航"三步（下一步）。

验证：物体层 13 条单测（关联 / 过期 / **重锚定** / 续建继承 / 外来层拒识 / 预算）+ 工作流层 3 条（发布、探测失败不打断、续建继承与身份校验）+ 地图目录 5 条（召回排序与属性过滤、未来时钟丢弃、旧地图无层即空、无提供者拒绝、组合技能回报位姿）；Web 375 通过（新增物体层归一化 4 条、缩放门控 1 条）。

### 修复：控制台的关键帧面板对每一张真实地图都报"格式错误"

现象：以前地图上会画关键帧、点开有详情弹窗，后来**整个面板空了**（列表为空、地图上没有方向标记，摘要显示错误）。原因是前端校验器与后端记录格式各走各的：

| | 面板原来的要求（`web/map_keyframes.js`，2026-09-13 加入） | 后端实际写的（`dense_slam.py`，2026-09-14 起） |
| --- | --- | --- |
| `status` 取值 | 只认 `accepted` / `rejected` | 写入**拒绝原因**：`no_overlap`、`too_flat_or_few_normals`、`underconstrained`、`correction_too_large`、`weak_loop` … 只有通过全部门限才写 `accepted` |
| 每帧配准记录数 | ≤ 2 | 1 次相邻配准 + 每个回环候选最多 2 个朝向猜测 → **实测 2.1–3.5，上限 9** |

于是 `normalizeSession` 对任何真实地图都抛错，`load()` 把错误写进摘要——**看起来就像功能被删了**。两处都修：

- 面板不再枚举状态词，接受任何"原因形状"的字符串（`^[a-z][a-z_]{2,31}$`），**只有 `accepted` 算证据**；未知原因原样显示。这样 SLAM 以后再加拒绝理由也不会再把整个面板弄坏；
- 每帧预算改为 9（= 1 + 4 个回环候选 × 2 个朝向猜测），后端把这两个数字变成常量并在 Python 测试里断言 `1 + MAX_LOOP_CANDIDATES × LOOP_GUESSES ≤ 9`，任何一侧放宽都会被测试拦住。

顺带把面板做得比原来更好用：

- 弹窗里的每条配准记录现在显示**具体未通过原因**（中文标签 + 原始码），并补充对应点数量与修正量；
- 帧列表给"只靠里程计进入地图"的帧打 `仅里程计` 标记（该帧所有配准都被拒），漂移段一眼能看出；
- 摘要显示整图配准质量：`通过 343/865 · 未通过 522 次（主要：与参考子图重叠不足）`；
- 弹窗支持 ←/→ 翻帧，不必在几百帧里反复瞄准按钮。

验证：对**正在运行的**控制台（真实地图 `scan-5863b747424e`，355 帧）跑完整数据通路——会话 865 条配准记录归一化通过、355/355 帧预览通过 SHA/尺寸校验并可解码（首帧 RGB 4622 B JPEG、深度 7084 B PNG）、识别出 8 个仅里程计帧与 8 个回环帧。Web 测试 371 通过（新增 3 条覆盖真实拒绝词表、超预算与未知原因），Go `web`/`console` 包测试通过，Python 全套通过，`make lint` 干净。

### 原地转身时深度配准会"发明"位移：一次勘测把最后 124 个关键帧拉偏 0.38 m

上一轮之后，从零探索的覆盖率还在 70% 上下反复。把停下来的地图拆开看，发现**地图里有一段是坏的**：

- 一段航迹的最后 124 个关键帧，优化位姿相对里程计被拉动 **0.14 m 起、单调增长到 0.38 m**——仿真里里程计是精确的，这 0.38 m **全部是损伤**；
- 后果是同一面墙被写进地图 **五次**，间隔 0.2 m（走廊里量到 x = +0.25/+0.45/+0.65/+0.85 与 −0.75/−0.95/−1.15/−1.35 五组平行"墙"），走廊和客厅的目标位姿因此被判为不可通行，覆盖率也上不去。

根因在会话记录里，是一次**纯原地转身**：

```
kf 229..239  里程计只有 yaw 在变（0.158 → −1.812 rad），位置恒为 (−1.566, +7.464)
kf 231       配准被拒（correction_too_large，旧阈值 0.25 m）
kf 232       配准 accepted，correctionM = 0.0676 m   ← 原地转身却"测到" 6.8 cm 位移
kf 233       配准 accepted，correctionM = 0.0532 m
```

这两条边进入位姿图后，**权重（约 75）高于它们所否定的里程计边（40）**，于是整条链被拉走；因为位姿图只固定第一个关键帧，后面的关键帧一路继承这个偏移。原地转身是深度配准最坏的情形：视野完全换掉，只有少数一直留在画面里的表面能配对，对应点就沿着这些表面滑动。

**修法**：一次配准最多只能修正"这一步实际观测到的运动"的一个比例（25%），并带一个 2 cm / 0.02 rad 的地板。真实里程计的误差是行驶距离的百分之几，不是三分之一；而原地转身的地板保证了"里程计说没动，配准就不许说动了 6.8 cm"。回环是唯一的例外，显式给了宽得多的上限（2 × 回环搜索半径）——闭合已经积累的误差正是它的职责。

验证：
- 现实中那两条边（0.0676 m / 0.0532 m）现在都超过 0.02 m 的允许量，被拒为 `correction_too_large`；
- 评测夹具 **room / house 两条路线的误差一个字节没变**（0.60/1.60 mm、2.60/4.80 mm），注入 4% 漂移的恢复能力也没变（room 43 mm 对里程计 85 mm）——门限只砍掉"里程计从未看到的位移"，不砍真实漂移修正；
- 给夹具**补了两条路线**（`west`：客厅→走廊→卧室→卫生间→原路返回；`bathroom`：到卫生间角落连续转身再回家），因为原来的 room/house 路线根本不经过西翼——这次损伤正是发生在那里，而夹具看不见。bathroom 路线无漂移 ATE 0.2 mm、最大 3.3 mm；
- **重跑一次从零探索（三段共 753 个关键帧）**：逐帧修正最大值 **0.027 m**，没有任何一帧超过 0.05 m（改动前同一位置是 0.14→0.38 m、124 帧超过 0.10 m）。走廊里那五组平行"墙"消失：x 方向的竖直面只剩 ±0.7..0.9 两组，正是commissioned 的两面墙。五个房间目标位姿全部可通行，占用格 4471 / 未知 7473（探明 81%，比改动前的 69–80% 更好，而且**是因为跑到评测夹具的 30 分钟上限才停下**——探索不再谎报"没得探了"）。
- **这张从零地图上的家居自然语言验收全部通过**：`inspect-kitchen`（去厨房确认环境 + 从厨房回客厅）、`patrol`（巡检卧室和卫生间后回客厅）、`mug-transfer`（去厨房拿杯子→放进收纳盘→回客厅，含 `pick`/`verify_grasp`/`place`/`verify_place`）——**4 个任务全部 SUCCEEDED，0 重试 0 复位**。

#### 顺带关掉一个长期"未验证"的空白：自滤除

两轮前记录过"自滤除在巡检位姿下标记 0/76800 像素，无法确认它是好是坏"。这一轮用**对照渲染**把它问清楚了：把机器人自身的 45 个几何体缩小到看不见，用**同一台相机、同一个位姿**再渲染一次，逐个像素比对——

```
keyframe 61（走廊）  深度改变的像素 0 个，颜色改变的像素 0 个
keyframe 178         深度改变的像素 0 个，颜色改变的像素 0 个
```

也就是说：**基础相机根本拍不到自己**（相机就装在底盘前缘，机身全部在它身后/下方）。两轮勘测共 713 个关键帧的 `selfMaskedPixels` 全是 0，这个 0 是正确答案，不是失效。RGB 图像底部那条均匀色带深度无效（近平面内），不会变成地图点。


### 地图自相矛盾：23% 的占用格在自己的点云里没有证据（并纠正上一轮的错误结论）

这一轮本来在追"厨房目标为什么还是 `GOAL_NOT_CLEAR`"。上一轮把原因写成"贴近低矮家具、掠射角观察时深度误差被摊开成一片体积"——**这个解释是错的**，这一轮把它推翻了：

- 把 96 个真实勘测位姿逐个放回仿真器，用与运行时相同的相机、相同的深度转点云代码重新渲染：**没有任何一个位姿在那两格里产生过点**。传感器是无辜的。
- 逐关键帧位姿修正也都很小：三段探索的最大修正分别是 0.023 / 0.041 / 0.033 m。位姿图同样没有把地图拉出去。
- 但发布的栅格在那两格上写着占用，而**发布的点云在那两格里各有一个测到的地面点、没有任何障碍高度的点**。

原因在续建：每一段都会把继承来的点云重新做一次体素降采样（点被平均、重新分箱），四段之后证据被逐代磨稀；而 `merge_grids` 的规则是"占用压过自由"，于是一个格子一旦被标成占用，**即使它的证据已经被降采样磨掉，它仍留在栅格里**。实测：6494 个占用格中有 **1491 个（23%）** 在发布的点云里没有任何障碍高度点。操作者打开地图根本看不到这个"障碍"，路由器却因为它拒绝了一个完全可达的目标位姿。

修法是一条发布前的不变量：**栅格里的占用格必须能在同一张地图的点云里看到**（`reconcile_grid_with_cloud`）。对账只在"没有点云支撑"时生效：同格测到地面 → 降为自由（有正面证据）；什么都没有 → 退回未知（"这里没人看过"才是诚实答案）。

| 同一张地图（四段从零探索） | 修复前 | 修复后 |
| --- | --- | --- |
| 占用格 | 6494 | **5003**（正好等于点云支撑数） |
| 自由格 | 24994 | 25722 |
| 未知格 | 8112 | 8875 |
| 厨房目标位姿 0.32 m 内的阻挡格 | 2 | **0（可通行）** |
| 外墙占用格（40 个支撑点） | 保留 | 保留 |

同时补上一条更早该有的规则：**一个视角不算证据**。障碍格现在需要**至少两个不同关键帧**看到（`MIN_OBSTACLE_OBSERVATIONS`），单个视角的飞点或边缘混点不会再变成一堵墙；从基础地图继承来的证据豁免，不被重新审判。规划栅格与发布栅格用同一套判据。

#### 这一轮没有解决的两条（都量过，写清楚）

1. **卫生间目标位姿周围还有 17 个未知格**（上一张地图）：这是覆盖率问题（那一轮从零探索到 80%），不是障碍问题。最新一轮探索后五个房间的目标位姿全部可通行，但覆盖率在 69%–80% 之间波动，**"从零一次扫全"还没有稳定达成**。
2. **厨房停靠点是"贴着包络"选的**——这一条查清了，也修了。实测（把机器人放回该位姿，用驱动自己的包络函数量）：

   | 房间目标位姿 | 最近的环境几何 | 距离 | 旧守卫 0.40 m 的余量 | 新守卫 0.355 m 的余量 |
   | --- | --- | --- | --- | --- |
   | **kitchen (2.05, 3.00)** | `home_task_table` 桌面 | 0.405 m | **0.005 m** | **0.050 m** |
   | living_room | `living_table` | 0.450 m | 0.050 m | 0.095 m |
   | bedroom | `bedroom_bed` | 0.650 m | 0.250 m | 0.295 m |
   | home_corridor | 走廊墙 | 0.720 m | 0.320 m | 0.365 m |
   | bathroom | `bathroom_sink` | 0.856 m | 0.456 m | 0.501 m |

   厨房停靠点离任务桌真实桌角 0.405 m，而驱动自己的净空守卫是 0.40 m——**余量 4.8 毫米**。路由器更松（0.32 m），所以地图说"可通行"、驱动说"会碰"，任务停在 `NAV_MODEL_COLLISION`。

   **先验证了"移动停靠点"这条路，实测走不通**：停靠点沿远离桌角方向外移 0.04 m，抓取仍成功但**放置失败**；外移 0.06 m **抓取就失败**（`GRASP_NOT_REACHED`）。工作台本身就在臂展边缘：该位姿到杯子 0.472 m、到收纳盘 0.529 m，已经是这套工作台的可用极限，所以相对几何不能动。

   于是改的是那个守卫本身：它原本是**写死的 0.40 m**，而机器人 CAD 在守卫实际检查的高度带内只伸到 **0.305 m**——也就是说 0.40 里有 95 mm 是没有依据的余量，正是这 95 mm 拒绝了工作台需要的那个位姿。现在守卫 = **实测 CAD 包络 + 0.05 m 明确余量 = 0.355 m**，仍然是"比规划器 0.32 m 更严的一方"，物理余量也从 95 mm 变成 100 mm（对真实几何）。守卫的数值不再是"祖传常数"，而是可以从 CAD 复算出来的量。`_swept_model_collision` 里那一路**真实几何接触检查完全没动**，它依旧是权威；改的只是它外面那圈保守包络。

   验收（从零探索产出的地图 `scan-bd950d7903eb`，未经任何巡检底图）：`inspect-kitchen` **两个任务全部 SUCCEEDED**；`patrol`（卧室→卫生间→客厅，3 段导航 + 3 次到达核验）**SUCCEEDED**；`mug-transfer`（去厨房拿杯子→放进收纳盘→回客厅，`pick`/`verify_grasp`/`place`/`verify_place` 全链路）**SUCCEEDED**。三项均 0 重试 0 复位。
3. **机器人在非commissioned朝向下无法导航**：驱动的单条指令转角上限是 0.5 rad，任务层的 `navigation.navigate` 直接下发目标位姿，不做分段转向——上一轮勘测结束时机器人停在任意朝向，紧接着跑验收就报 `NAV_ROTATION_LIMIT`（建图路径早就解决了这件事：`_turn_by` 会分块）。验收因此必须先重启仿真让机器人回到客厅起始位姿。**这是一个待修的真实缺口**：真实机器人停放朝向是随机的。

顺带把碰撞拒绝做成了可诊断的：`NAV_MODEL_COLLISION` 现在会带上最近的环境几何名和实测距离（`_nearest_envelope_obstacle`），而不是只说"模型预测会接触"——上表就是这样量出来的，操作者也能自己复现。

#### 覆盖率不稳定：两个真缺陷（规划/证明/守卫不一致；近处不可达前沿遮住远处可达前沿）

同一套代码两次从零探索：一次探明 80%，一次只有 69%，都以 `no_reachable_frontier` 诚实收尾。查下去是**两个**缺陷：

**(1) 上一轮改守卫时留下的静默回归**——规划、证明、守卫三个数字不再一致：

- 轨迹认证问驱动的半径是 `footprint + 0.08 = 0.40 m`（当年守卫恰好是写死的 0.40 m，所以驱动能证明它）；
- 守卫改成"实测包络 + 0.05 m = 0.355 m"之后，这个 0.40 m 查询**被正确拒绝**（证明不得声称比背后的检查更宽）——但**整条行驶轨迹就此静默地不再被认证**；
- 于是规划器（当时要求 0.40 m 净空）看到的自由空间比它自己规划所需的还窄，机器人走了半天却探不到可规划的前沿。

现在三个数字出自同一处：**规划净空 = 驱动证明的半径 = 认证半径 = 自身半径 + planningMarginM（0，即 0.32 m）**，驱动守卫 0.355 m 严格大于它；回归测试锁住"认证问的半径必须等于规划净空"。

**(2) 近处不可达的前沿会把远处可达的前沿一起藏起来**——这是覆盖率反复停在 70% 上下的真正原因：

`explore_target` 按"最近的候选"算出"近似并列带"（`NEAR_TIE_RATIO 1.25` + 2 m），然后只在这个带内挑目标。但那个"最近"取的是**几何最近**，不是**能规划到的最近**。实测这件事发生在真实地图上：

```
机器人终点 (-0.07, 6.56)，地图仍有 28.5% 未知、1754 个前沿格、23 个可达前沿簇
最近的三个簇：1.65 m / 3.15 m / 3.15 m 处的不可达碎片（规划失败）
第一个簇把带设成 1.65*1.25 + 2 = 4.06 m，7.6 m 处那个"规划成功"的簇直接落在带外 → 返回 None
```

也就是说：**一个开不过去的碎片决定了机器人愿意看多远**。修法是让近似并列带锚定在"第一个真正能规划到的簇"上，而不是几何最近的簇。用同一张停下来的地图复测：**24 个实际勘测位姿全部都能找到可达前沿**（终点处是 7.5 m 外、121 格的前沿），"这里没得探了"的报告因此不再是假警报。距离是偏好，不是否决权。

### 从零一次扫全：四个把探索卡死的缺陷，以及现在到哪一步

上一轮把 SLAM 的精度量出来并改了。这一轮拿着"从零一次扫全"去跑，卡住的**不是精度，是探索循环本身**。四个缺陷，都是实测定位的：

1. **规划器在算，但看起来像死机**。A\* 是纯 Python 的，地图一大（接上底图后 4 万多栅格），每个候选 frontier 都跑一次完整搜索；40 个候选中只要有几个不可达，一次规划就是几十秒。表现是机器人原地不动、CPU 100%、十分钟不产生关键帧。现在限制候选数量（40→12）并给单次搜索加扩展预算（15000 个节点），**同样规模的地图一次规划 99 ms**。
2. **机器人自己站的那一格在地图里是"未知"**。前向相机永远拍不到脚下的地面，而轨迹认证只覆盖"开过的路段"——起点没有路段，于是起点保持未知，路由器拒绝从自己的位置出发（`LOCALIZATION_NOT_CLEAR`）。现在把机器人实际站过的位置按自身半径标为可通行：**这是本体感受，不是对房间的推断**；已观测到的障碍依然优先，不会被覆盖。
3. **分段发布的中间态被当成"扫描结束"**。自动探索每发布一段地图，状态会短暂变成 `completed`——CLI 看到就退出并报告成功，而机器人还在跑第 2、3、4 段。现在段与段之间状态是 `exploring`，只有整轮结束才是 `completed`；同时会话记录不再把信息矩阵（ndarray）写进 JSON（那会让保存地图直接抛异常）。
4. **规划净空比认证净空更严格**（上一轮发现，这一轮验证）。规划器开不进自己刚开过的走廊。

#### 实测：一次从零开始的自动探索

```
leg 1  frames 356  travelled 24.5 m   unknown 33.4%  frame_budget
leg 2..4                              → 最终 unknown 17.0%（探明 83%）
最终地图 scan-61271b06bf64：185,988 点，自由 22,964 / 占用 11,099 / 未知 6,969
```

对比本轮开始时：从零探索停在 63%–69%，且地图在起始位姿处就有伪障碍。

家居自然语言任务验收（从零探索产出的地图，未经任何巡检底图）：

```
巡检卧室和卫生间，最后回到客厅    SUCCEEDED  7 步（3 段导航 + 3 次到达核验）
从客厅出发，去厨房确认一下环境    RECOVERABLE_FAILURE  GOAL_NOT_CLEAR
```

**巡检已经跑通**——这是第一次在"从零自动建图"的地图上完成跨房间多段导航。厨房任务仍失败在目标位姿：commissioned 厨房停靠点周围 0.32 m 内出现了占用栅格。

#### 仍然没做到的，以及原因

那一块占用栅格查清了：厨房桌子西南角有 **907 个点**散布在 z 0–0.9 的整段高度上（巡检产出的地图同处只有 70 个）。这不是位姿漂移（同一轮实测逐帧误差最大 6 mm），而是**机器人贴近低矮家具、以掠射角观察时深度误差被摊开成一片体积**——探索为了覆盖会开得比巡检更靠近。要修需要传感器层面的处理（掠射角深度剔除，或对障碍分类设最小观测距离），这一轮没做。

另外顺带记录一条没被证伪的空白：抽查巡检位姿时相机自滤除掩码标记 0/76800 像素，且把手臂摆到前方也无法让它标记任何像素——**目前没有证据说明自滤除在正常工作，也没有证据说明它失效**（那个位姿确实没有自身结构入镜）。这是一个需要专门验证的已知空白。


### 更强的 SLAM：先把误差量出来，再把它改下去

"从零一次扫全"要成立，位姿必须准。但在动手之前，没有任何东西能说明当前 SLAM 到底差多少——所以先做了一个**可复现的评测夹具**：`scripts/evaluate_slam.py`。

#### 评测夹具：仿真里唯一能拿到真值的地方

仿真器是唯一能同时给出"机器人真实位姿"和"传感器数据"的地方。夹具让机器人在装修家庭里走一条注册路线，把真实渲染的 RGB-D 喂给 `DenseSLAM`，然后报两个数：

- **damage（无漂移）**：里程计精确时，优化后的轨迹偏离真值多少。**一个打不过里程计的 SLAM，至少不能破坏它**——这正是仿真里悄悄发生的事。
- **recovery（注入漂移）**：往里程计通道注入 4% 尺度误差，看深度配准能纠正回多少。这才是硬件上真正需要的性质，而且**看地图是看不出来的**。

#### 升级内容

| 改动 | 为什么 |
| --- | --- |
| **点面 ICP**（表面法向） | 室内几乎全是平面。点到点 ICP 的对应点会沿平面滑动，把规则的体素格读成亚体素位移，于是地图整体横移。平面唯一约束的方向就是法向，现在解的就是它。 |
| **对局部子图配准**（最近 8 个关键帧） | 只对上一帧配准，每个测量都相对于一个带噪表面；对几米范围的子图配准，条件数更好、偏差小得多。 |
| **信息矩阵加权**（不是标量 sigma） | 一面墙只约束一个方向。把"没观测到的方向"直接扔掉太浪费，当成全约束又会悄悄转动地图——现在按 Jacobian 的 Gram 矩阵加权，**观测到几个方向就贡献几个方向**。 |
| **拒识带原因** | `register_depth` 现在把拒绝理由写进记录（`no_overlap` / `underconstrained` / `large_residual` …）。"没有测量"不足以排查一次漂移的扫描。 |
| **回环允许反向重访** | 走廊是走进去再走回来的，回到同一位置时**朝向相反**。原来的朝向门限把这类回环全部拒掉，而两条平行墙恰好约束不了沿走廊方向——这正是走廊漂移修不回来的原因。 |

顺带修掉一个新引入的缺陷：信息矩阵是 ndarray，被写进了会话 JSON，导致保存地图时抛 `Object of type ndarray is not JSON serializable`。现在记录时会转成普通列表，并有测试锁住"会话文档必须可序列化"。

#### 实测（仿真真值，`--route room` / `--route house`）

| 指标 | 升级前 | 升级后 |
| --- | --- | --- |
| 接受的配准（room / house） | 2 / 6 | **42 / 61** |
| 无漂移 ATE | 12 mm / 4 mm | **0.6 mm / 2.6 mm** |
| 无漂移最大偏差 | — | **1.6 mm / 4.8 mm** |
| 注入 4% 漂移：ATE（room） | 87 mm（里程计 85 mm） | **43 mm**（里程计 85 mm） |
| 注入 4% 漂移：ATE（house） | 139 mm（里程计 138 mm） | **123 mm**（里程计 138 mm） |

无漂移时几乎零损伤（毫米级）；注入漂移时房间路线**纠正掉一半**，走廊为主的路线只纠正一成——原因见下。

#### 两条必须写清楚的边界

1. **走廊里几何上就定不了位**。两面平行墙的法向互相平行，沿走廊方向没有任何约束；实测一次 61%→77% 的探索里，走廊段的尺度误差无法靠几何回环消除。真实系统靠视觉词袋/纹理特征解决，本仓库的深度-only 后端做不到。这是能力边界，不是调参问题。
2. **基线相机的自滤除在巡检位姿下标记了 0 个像素**。抽查起始位姿：76800 个像素中自滤除掩码为 0，云里也确认没有自身结构（最近点在前方 0.48 m）。所以那个位姿没有自身遮挡可滤——但这意味着**自滤除是否有效从未被真正验证过**，一旦手臂进入视野就会把机器人自己写进地图。这一项留作已知风险。

#### 从零一次扫全：现状与还差什么

装上更强的 SLAM 之后重跑"从零探索"（`--mode explore --max-travel-m 70 --max-legs 4`）：

- 地图质量明显变好：**墙面轴对齐**，南墙在 `y=-2.42`、西墙在 `x=-4.4`、走廊在 `x=±0.8`、客厅隔墙在 `y=0.98`，与commissioned 场景一致。
- 覆盖仍然**只到南半部分**：机器人走到走廊 `y≈5` 就报 `no_reachable_frontier`，浴室与厨房北侧未覆盖。

排查剩下这一步，找到两个具体原因，都还没修：

1. **地图里有贴着机器人起始位姿的伪障碍**。成品地图在 (0.19, −1.24) 附近有 985 个障碍高度（z 0.04–0.70）的点，而抽查起始位姿的单帧点云最近点在前方 0.48 m——也就是说这些点来自**别的位姿**（很可能是机器人自身结构进入了视野）。路由器的起始位姿净空检查要求脚下 `point_clear(0.32 m)`，一个伪栅格就足以报 `LOCALIZATION_NOT_CLEAR`。实测：**同一套 SLAM 下巡检底图能通过家居任务验收（3 个任务全 SUCCEEDED，0 重试 0 重置），而探索补扫后的地图在第一步就失败**——差别不在位姿精度，而在这一个伪障碍。
2. **前向相机的自滤除没有被真正验证过**。抽查巡检位姿：76800 像素中自滤除掩码标记 **0** 个，点云里也确认没有自身结构（最近点 0.48 m）。这个位姿确实没有自身遮挡可滤，但反过来说，**自滤除是否有效从未被证伪**——一旦手臂进入视野，机器人就会把自己写进地图，正是上面第 1 条的形状。

结论：**"从零一次扫全"目前做到的是"从零一次扫出高质量但部分覆盖的地图"**；把它变成导航级、全覆盖，还差"自滤除验证 + 探索在 `no_reachable_frontier` 上的推进策略"这两件事，都在这一轮被定位到了具体位置。

#### 附带发现：规划净空比认证净空更严格会把走廊变成禁区

排查"探索为什么停在 61%"时量到：一张 20,868 个自由栅格的成品地图，规划器实际可走的只有 4,149 个——因为规划净空取了 `footprint + 0.08 + 0.06 = 0.46 m`，而轨迹认证用的半径是 `footprint + 0.08 = 0.40 m`。**规划器比认证更保守，于是它刚开过的走廊对自己来说变成了不可通行，连起始栅格都不可规划**，`no_reachable_frontier` 就是这么来的。现在两者取同一个半径。


### 自动探索建图：让机器人自己去找没扫到的地方

**固定巡检路线走的是注册航点，它不可能知道房间里有什么，所以总会漏。** 漏了之后只能靠人工发现、人工规划、人工执行续建。新增 `mapping.start { mode: "explore" }`：机器人自己判断下一个未知区域在哪里。

#### 算法：frontier 探索 + 代价主导的选点

`robot/gateway/tangying_robot_gateway/exploration.py` 是纯函数模块——输入占据栅格，输出决策——所以策略能在合成地图上测试，不需要机器人：

- **frontier**：已知自由栅格中与未知空间相邻的那些（Yamauchi 1997 的经典定义）。墙不是 frontier。
- **可通行**：自由 ∧ 距任何已观测障碍 ≥ `footprint_radius + 0.08 + 0.06`。规划净空要比驱动净空略大，但不能大太多——大太多会把机器人困在驱动已经允许它到达的地方。
- **选点**：先按**行驶代价**排序，只在代价接近（`nearest × 1.25 + 2 m`）的候选之间用信息量决胜。早期版本用 `gain / (cost + 0.5)`，结果是机器人在房间里**来回横扫**：每个位姿都会把它刚离开的那一端重新排到第一，永远走不到远端的角落。
- **路径**：八连通 A*，显式禁止贴着障碍角的对角步。
- **滚动时域**：每走一步就重新观测、重新规划——地图是驱动自己改变的。
- **不切角**：前视点必须直线可达。机器人是"朝一个点开"，不是"沿折线开"，取太远的前视点会让它切掉路线本来要保留的净空。

#### 三个只有真跑才会暴露的坑

1. **相机盲区形成"永久 frontier"**。前向相机看不到脚下和刚走过的地面，机器人周围因此永远有一圈未知，而这一圈离它最近、每一帧都会被选中——机器人对着自己的脚印开。现在把"已证明看不见的空间"标出来：地图里它们仍是未知，规划器知道驱动解决不了它们。
2. **相机近界与自身足迹之间的空隙**。相机从底盘中心前方约 0.5 m 才看到地面，轨迹净空只标到 0.4 m，于是**一开始机器人站在一座孤岛上**，规划器无路可走，直接宣布"没有未知区域"。现在自身足迹（∩ 净空）并入可行区域，A* 也允许"起点本身在净空阴影里"——它可以待在那儿，但每一步离开都必须合法。
3. **一条腿跑不出结果 ≠ 扫完了**。`explore_target` 返回 None 有两种含义：地图扫完了，或者规划器到不了剩下的未知。早期版本两者都写成 `complete`，把 41% 的地图报告成"探索完成"。现在分开：`complete` 与 `no_reachable_frontier`，并把 frontier 栅格数写进状态供核对。

#### 400 帧预算：自动分段续建

一次会话最多 400 帧，满屋扫描远超这个数。探索因此**自己分段**：接近上限时先发布这一段的地图，再**从刚发布的地图继续**（复用已有的续建机制）。每一段都会把基础地图的**导航栅格**并进规划用图，所以第二段不是从空白重新开始。

#### 反直觉的实测结论：少转圈，地图才准

给探索加了"到达未知边缘就原地转一圈"的覆盖动作之后，**整张地图漂移**：墙面在占据栅格里变成斜线，与已知良好地图的重合率只有 10%。原因是原地旋转是深度 ICP 信息最少的一种运动——同一面墙、同一位置、连续多帧。把定期环视默认关掉之后，同一套流程产出的地图**墙面重新轴对齐**（重合率 41%，而这 41% 的分母里包含大量新扫描区域）。**运动以平移为主时 SLAM 才准；覆盖靠开过去，不靠停下来看。**

#### 实测结果

两次真机（仿真）验收：

| 场景 | 命令 | 结果 |
| --- | --- | --- |
| 从零自动探索 | `--mode explore --max-travel-m 60 --max-legs 4` | 2 段、21 m、141,625 点，探明 69%，停止原因 `no_reachable_frontier`（浴室与厨房北侧当时不可达），**墙面轴对齐** |
| 在巡检底图上补扫 | `--mode explore --base-map-id scan-72eccee3bbf9 --max-travel-m 30 --max-legs 2` | 1 段、29.5 m、169,680 点，探明 77%，停止原因 `frame_budget` |

第二次产出的地图（`scan-062569d530b8`）**通过家居自然语言任务验收**：

```
passed: true      physicalRetries: 0      worldResets: 0
巡检卧室和卫生间，最后回到客厅    SUCCEEDED  7 步 / 16 张验证图像
从客厅出发，去厨房确认一下环境    SUCCEEDED  5 步 / 12 张验证图像
从厨房出发，回到客厅            SUCCEEDED  5 步 / 12 张验证图像
```

#### 一条必须说清楚的边界

**单独跑探索产出的是"勘测图"，不是"导航级地图"**：长时间探索的位姿精度低于按注册路线走的巡检，实测在餐桌附近有约 20 cm 的局部偏差，足以让机器人起始位姿的净空校验失败。所以推荐用法是**先巡检建立底图，再用探索补扫**——探索继承底图的坐标系、点云与导航栅格，只在未知区域新增证据，底图已探明的区域保持原样。要把"从零一次扫全"做到导航级，需要更强的位姿图/回环后端，或探索结束后的重定位与重观测回合，这两项都还没做。

### 续建建图（第六步：续建会把老地图的"已知自由空间"丢掉，已修）

第五步的验收不是"点云够不够多"，而是**任务还能不能跑**。用新地图跑家居巡检，第一步就失败：

```
subtask 1: skill navigation.navigate failed: NO_KNOWN_PATH 当前地图没有连接到目标工作区的通路，请补扫连接区域。
```

**卧室连不上了**，尽管点云里卧室一个点都没少。原因在于占据栅格不是点云的纯函数：

- 一次勘测的"自由空间"有两个来源——点云里的**地面点**，以及**本会话经过净空校验的行驶轨迹**。前向相机看不到自己脚下，所以走廊、门洞、停靠点附近的大片自由空间其实来自后者。
- 续建的 `_build` 用**合并后的点云**重建栅格，而 `_observed_travel` 的净空校验器只认**当前进程**记录过的行驶。基础地图那条轨迹虽然在合并后的轨迹里，却拿不出净空证据。
- 结果：**点云并集是对的，栅格并集不是**——老地图靠轨迹证据标出的自由空间变回"未知"，规划器就找不到通路。

实测对比：原图 `scan-6f2feb8d93b2` 里卧室停靠点是 `free`，续建后的图里变成 `unknown`；两图在这两个点周围的点云几乎一致（8 个地面点 vs 8 个地面点）。全图统计，**原图有 1,534 个自由栅格在续建后消失**（约 3.8 m²，沿原巡检路线的 0.8 m 宽带状区域）。

#### 修法：续建时把基础地图的导航栅格并进来

`_build` 现在会读回基础地图**已发布的** nav2 产物（`navigation/map.pgm` + `navigation/map.yaml`），并把它与本次会话的栅格求并：

- **自由与占用都取并集**，只有两边都不知道的栅格才是未知——每一张栅格都是证据，一张勘测开过去的格子就是自由，一张勘测看到墙的格子就是占用。
- 两张栅格本来就在同一坐标系（锚点是继承的），所以这就是一次叠加，不需要重采样。
- 栅格不可读或格点不一致时，**保留本次勘测自己的证据**并继续，而不是让整次续建失败。

新增的三块能力都在 `navigation_map.py`，各有测试：`read_nav2_grid`（`nav2_artifacts` 的逆）、`merge_grids`（叠加语义）、以及 `_base_grid` 的读取与容错。

**为什么读"已发布的产物"而不是重算**：基础地图是那次勘测的**已验证记录**，重算等于对同一个问题给出第二个答案——而这次的差异恰恰就出在"重算"上。

**新增 6 项测试**：

- `test_nav2_grid_round_trips_through_its_published_artifacts`：往返一致，并用一个**行不相同**的栅格验证方向没有被巧合掩盖；
- `test_unreadable_navigation_artifacts_are_reported_not_guessed`：截断、非 PGM、坏 JSON 都报错而不是猜；
- `test_merged_grid_keeps_each_surveys_evidence_and_drops_neither`：这正是巡检失败的那条性质；
- `test_merge_is_an_overlay_where_obstacles_beat_free_space`；
- `test_merge_refuses_grids_that_do_not_share_a_lattice`；
- `test_merged_origin_lands_each_grid_on_the_same_lattice`；
- `test_a_continuation_inherits_the_base_maps_free_space`：构造一张**有自由栅格却没有地面点**的基础地图——正是重建栅格无法找回的那种证据；
- `test_a_base_map_without_navigation_artifacts_does_not_block_continuation`。

**同时暴露的一条产品级教训**：续建的验收标准不能只看"地图变大了"。真正该问的是**这张地图还能不能把机器人带到每个已注册工作区**，因为栅格既承载几何也承载"能不能走"。

#### 验收：用新地图重跑家居自然语言任务

修好后重新续建了一次（对象是修正前的成品图，路线覆盖走廊 → 卧室 → 浴室），再用新地图跑 `scripts/run_home_task_suite.py`：

```
巡检卧室和卫生间，最后回到客厅        SUCCEEDED  7 步确认，16 张验证图像，3 段地图导航
从客厅出发，去厨房确认一下环境        SUCCEEDED  5 步确认，12 张验证图像，2 段地图导航
从厨房出发，回到客厅                SUCCEEDED  5 步确认，12 张验证图像，2 段地图导航
```

`passed: true`，`physicalRetries: 0`，`worldResets: 0`。**同一批任务在修正前第一步就 `NO_KNOWN_PATH` 失败**，所以这次修复是"从不可用到可用"，不是"从可用到更好"。

修复后地图与原图的自由空间对比（同一批关键点）：

| 位置 | 原巡检图 | 修正前 | 修正后 |
| --- | --- | --- | --- |
| 卧室停靠点 | free | unknown | free |
| 卧室中部 | free | unknown | free |
| 卧室南侧 | unknown | unknown | free |
| 浴室停靠点 | free | free | free |
| 走廊 | free | free | free |
| 客厅起点 | free | unknown | free |

### 续建建图（第五步：用续建功能把地图右侧真正补齐）

第四步修好"有界移动"之后，续建才第一次按预期工作。用它对同一张地图连续续建，把第一次巡检漏掉的区域补齐：

| 地图 | 点数 | 覆盖情况 |
| --- | ---: | --- |
| `scan-6f2feb8d93b2`（原始巡检） | 63,083 | 厨房 `y>2.5` 处 `x>3.5` 全空；客厅 `y<1` 处 `x>1.5` 全空 |
| `scan-cf7a1d78d382`（续建） | 113,201 | 厨房右侧补到东墙 `x≈4.4`；客厅补到 `x≈4.4` 与南墙 |
| `scan-72eccee3bbf9`（最终） | 124,159 | 再加上卧室与浴室的补扫；导航栅格继承基础地图的自由空间 |

跑法：`mapping.start { baseMapId }` 起一次续建 → 一串 `mapping.move` → `mapping.finish`，产物是并集。中间几次续建的产物（`scan-ec5a617b4f79`、`scan-15d694d406ce`、`scan-3427ea0f8262`、`scan-6362682eda69`、`scan-cf7a1d78d382`）已移动到 `artifacts/maps-archive/`，目录里只留一张完整地图。

#### 一条实用的经验：巡航用"读位姿 → 修正 → 再走"的闭环

开环脚本（按步数推算位姿）在这套系统上不可靠：中途一次 0.2 m 的步进就让位姿偏了十几度，后面沿 `y≈0.4` 东进时撞上客厅隔墙的净空边界而被安全层拦下。**改成闭环后一次通过**：每一步都重新读位姿、重新对准目标方向、再走一小段。新增的 `scripts/` 里没有留这个脚本，因为它是运维手段而不是产品功能；可复用的部分是这条结论。

**另外一个实测约束**：控制台 `/v1/world` 与 `/v1/navigation/map` 的位姿**投影会滞后驱动约一秒**。刚发完移动就读，可能读到移动前的位姿——用它们做闭环时要"读到两次一致再采信"，否则会照着一个过期航向规划下一步。

#### 仍然未知的区域，以及为什么

最终地图的占据栅格是 22,175 自由 / 3,959 占用 / 13,686 未知（另有房屋轮廓以外的空白）。剩余未知**不是漏扫，是物理或传感器上做不到**：

- **客厅西端**（`x < -2.2`）**机器人开不进去**：沙发 `x∈[-1.80,-0.70] y∈[-2.00,0.20]` 与南墙 `y=-2.42` 之间只剩 0.42 m，小于 0.80 m 的整机净空需求，这是唯一通路。西墙本身在远处能看到，墙后地面被沙发完全遮挡。
- **卧室中部**：巡检只在 `(-2.05, 3.35)` 停一次并左右各转 0.35 rad；前向相机看不到自己脚下，所以那一片保持未知。
- **高于整机包络的顶面**（厨房岛台、操作台台面）永远看不到：基座相机在 `z=0.195 m`、略微下倾，看不见高于约 0.4 m 的水平面。占据栅格里岛台内部也因此是"未知"，个别杂散地面点会让它显示为自由——规划路径仍被运行时的净空与扫掠检查挡住，但**栅格本身不该被当作完整的障碍真值**。

### 续建建图（第四步：`mapping.move` 的"有界"此前并不成立）

**在用续建功能补齐地图右侧时，实测抓到一个真实缺陷：一次 `mapping.move { "action": "forward", "distanceM": 0.2 }` 让机器人跑了将近 4 米。**

现象是扫描突然 `failed`，消息为 `reference driver model predicts contact during bounded base pulse`，而**机器人的实际位姿离发起移动的位置已经很远**。查代码得到原因：

- 映射会话期间，服务层用 `_service_owner.enabled` 告诉运行时"这次移动由操作员发起，不要走活动地图的栅格路径"——这是对的，因为建图必须能进入地图没有覆盖的区域；
- 但**绕过栅格路径之后落到了"家庭路线分解器"上**：它会为了"先出侧房、再走走廊、再横向进入目标房间"而插入中间点。判定条件是 `|当前 x| > 0.6 且 x 有变化`，**再加上 y 也有变化**——而真实的有界步进因为航向不可能正好轴对齐，总会带一点 y 分量。于是 `exit_side_first` 插入了一个 `x = 0` 的中间路点。
- 结果：**请求 0.2 m、执行 3.9 m**，而且回程在走廊口被安全层挡住，扫描因此失败。

这不是安全层的问题（它正确地拦住了后续的撞墙动作），而是**"有界移动"名不副实**：回执描述的运动和实际发生的运动不是同一个。

修法是让"有界"成为请求自带的属性，而不是靠几何去猜——运行时无法从几何上区分"0.2 m 的挪动"和"跨房间的巡检目标"：

- `mapping.move` → `_manual_move` → `_move_and_sample(bounded=True)` → 服务层 `move(goal, cancel, bounded=True)` → 运行时的 `navigation.navigate(goal, cancel, route=False)`；
- `mode: "survey"` 的巡检目标走 `bounded=False`，**仍然使用家庭路线分解**，行为不变；
- `route=False` 只是不做路线分解，**所有安全检查照旧**：新鲜 RGB-D 复核、0.40 m 净空包络、扫掠碰撞检查一个都不少。

**新增 3 项测试**（其中 2 项在去掉修复后会失败，已实测确认）：

- `test_bounded_operator_step_owns_its_path_and_is_never_re_routed`：在厨房位姿上，同样一个带微小 y 分量的 0.45 m 步进，`route=True` 会插入 `(0, y)` 中间点（测试把这个旧行为**断言下来**作为对照），`route=False` 的所有分段都落在起点与目标之间；
- `test_bounded_move_reaches_the_controller_as_a_direct_step`：从服务层 `move(bounded=...)` 一路验证到控制器收到的 `route` 参数，并确认 `_service_owner` 的标志位用完即复位；
- `test_manual_move_declares_itself_bounded_and_survey_does_not`：手动步进声明 `bounded=True`，巡检目标声明 `bounded=False`。

### 续建建图（第三步：修掉一个真实缺陷——锚点没有作用到几何上）

**上一轮我提交的合并有一个真实缺陷，这一轮查出来并修掉了。**

`DenseSLAM` 的所有位姿都在**驱动世界坐标系**里（第一帧由里程计播种，后续由它复合），因此 `cloud()` 返回的是**世界坐标**；而地图坐标是另一回事，锚点正是跨过这道边界的变换。**原代码只把锚点作用在轨迹上，没有作用在点云几何上。**

- 对**首次扫描**，锚点接近单位阵，所以这个遗漏**看不见**；
- 对**续建**，锚点继承自基础地图、是真实的非单位变换——不做这一步，新点云留在世界坐标系，而基础地图的点在**地图坐标系**，两者相差整整一个锚点。

已在 `_build` 中把锚点应用到几何上（`transform(cloud.xyz, anchor)`）。这同时修正了首次扫描中一个更小的问题：锚点由**优化后**位姿推导，而点云用的是同一批位姿，只有把锚点也作用上去，几何与轨迹才严格一致。

### 关于端到端"接缝对齐"验收：仿真做不了，原因已查清

我原以为可以用仿真完成这项验收——**做不到，原因值得记下**：测试用的深度夹具在**每个位姿返回完全相同的深度图案**，这是一个**没有视差的"世界"**，相邻帧配准得到的信息是"位移为零"，因此**无法用来验证几何对齐**。要用仿真验证，需要一个随位姿变化的静态世界夹具（例如让纹理随 x 平移），那是另一项工作。

**改为验证精确的代数性质**（不需要夹具、也不依赖 ICP）：

```
transform(transform(points, world_pose), anchor) == transform(points, compose(anchor, world_pose))
```

这正是 `_build` 依赖的性质：把锚点作用在世界坐标几何上，等价于把它与该位姿复合——**也正是轨迹代码一直在做的事**。测试同时断言锚点确实移动了几何，否则"省略变换"也能通过——**那个遗漏当初就是这样活下来的**。

**新增 1 项测试**，替换掉一个本来打算用 ICP 做端到端对齐、但因夹具无信息而失效的版本。

**仍未完成**：跨会话回环（载入基础地图关键帧以提升接缝精度）。**接缝的真实几何精度仍未经实机或有效仿真的端到端验证**——本轮的测试验证的是坐标系转换的正确性，不是两次勘测拼接后的实际误差。

### 续建建图（第二步：合并为保证，而不只是同坐标系）

第一步只做了锚点继承——新会话与基础地图**同坐标系**，但产物仍**只有本次会话的点云**。那对"全扫描"是不够的：续建两次之后，地图里只剩最后一段，之前扫过的区域看起来像丢了。

现在续建会**读入基础地图已有的点云与轨迹并合并**，产物是两次勘测的并集：

- 合并之所以只是拼接，是因为第一步的锚点继承已经把两者放进同一坐标系；
- 基础地图的几何**从已存产物读取**，不重新推导——基础地图是那次勘测的**已验证记录**，在这里重新算一遍等于对同一个问题给出第二个答案；
- 基础地图的轨迹一并并入，合并后的地图能看到两段行程；
- **基础地图点云损坏时会明确报错而不是静默跳过**：manifest 的完整性校验只覆盖哈希与大小，不覆盖字节能否解码；静默跳过会交付一张"缺少已勘测区域"的地图，却报告成功。

**新增 2 项测试**：合并会读入基础地图的几何与轨迹、损坏的基础点云会抛错而非被跳过。

**尚未完成**：跨会话回环（载入基础地图关键帧以提升接缝精度），以及**端到端的接缝对齐验收**——它需要一次真实扫描。单元测试覆盖了锚点、合并与失败路径，**没有覆盖合并后的几何是否真的对齐**。

### 续建建图（第一步：锚点继承）

用户要求"从未知地区开始扫描未覆盖的地图"，不再重复扫已有部分。实现第一步：**`mapping.start` 支持 `baseMapId`**，在该地图的坐标系里继续扩展。

**关键在于锚点复用，而不是重新推导。** `_build` 原本总是从最后一次采集推导 `mapFromWorld`（`compose(last.pose, inverse_odom(last.odometry))`）——续建时若沿用这一逻辑，新点云会相对**本次会话的第一帧**定位，于是**落在旧地图旁边而不是里面**。现在续建路径直接复用基础地图的锚点。

**为什么这样就够了（仿真内）**：锚点是驱动世界坐标系上的刚体变换，而该坐标系**跨会话共享**——运行时用 `worldFrameRevision` 钉住它（它是编译模型的哈希，见 `sim/mujoco/tangying_sim/workflow_services.py::static_world_revision`），`_load_map` 加载时也会校验一致。因此**机器人可以从任意位置开始——包括基础地图从未见过的区域——新点云依然落在正确位置，既不需要重叠视野，也不需要重走旧路**。

**真机上的差别（必须说清）**：真机的 `base_pose` 来自里程计，而里程计每次会话从原点重启，锚点无法直接继承——真机需要**重定位**（识别"我在已建地图的哪里"）。那是 RTAB-Map 的本职，也就是把建图 provider 换成 RTAB-Map 的那条路（服务契约不变、`map_manifest.SOURCES` 已含 `rtabmap`、`DenseSLAM` 的 docstring 明说可替换 provider）。**本步只在仿真内成立。**

**实现要点**：

- 锚点在 `start()` 里、机器人移动之前解析——基础地图选错时**在操作者还站在旁边时报错**，而不是在一整趟扫描跑完之后。
- `_continuation_anchor()` 复用启用地图时的校验（机器人、标定版本、世界坐标系），但**不执行启用**——扫描不应静默改变机器人正在导航的那张图。
- 新地图的 provenance 记录 `baseMapId`，**谱系进入产物**。
- 帧上限本就是按会话计的（每次 `mapping.start` 新建 `DenseSLAM`），因此无需改动。

**新增 3 项测试**：锚点读取正确、世界坐标系不一致时报 `CONTINUATION_FRAME_MISMATCH`、无位姿会话的地图报 `CONTINUATION_UNAVAILABLE`（拒绝而非开始一趟无法拼接的扫描）。

**尚未完成**：载入基础地图关键帧以便**跨会话回环**（接缝精度），以及真机所需的重定位。**端到端的"接缝对齐"验收需要一次真实扫描**——单元测试只覆盖了锚点读取与校验，没有覆盖合并后的几何对齐。

### 文档一致性修复（v0.6.0 之后）

对 v0.6.0 的文档与代码做了一次交叉审查（两个独立审查，覆盖文档结构、路由契约、CLI 契约与前端模块）。`tests/docs/test_production_docs.py` 一直是绿的，但**它结构上看不到下面这些**：路由检查只扫 `console/server.go` 与 `fleet/server.go`，因此 `console/maps.go`、`console/evidence.go` 注册的路由从不被校验；它也只比对路径字符串，不比对请求/响应形状。

**会直接挡住新用户的**

- **`make home-furnished` 起的端口与 README 说的不一致**：`Makefile` 调用 `furnished-home-demo.sh` 时不传端口，`sim-stack.sh` 默认 **8787**，而 README 与 v0.6.0 发布记录都让人打开 **8897**——按 README 走会打开一个没人监听的端口。已让 Makefile 显式传 `--sim-port 50161 --agent-port 8897`，与 `furnished-home-demo.md` 一致，**端口只在一处声明**。
- **`docs/guides/robot-service-workflow.md` 给的是旧流程**（`make home-start` / 8787 / 红杯蓝盒），与装修家庭主线（`make home-furnished` / 8897 / 陶瓷杯收纳盘）矛盾，已对齐。
- **`docs/development/dense-map-operations.md` 里的 `build_map.py --database` 命令现在直接报错退出**：脚本已要求 `--optimized-poses`（`Node.pose` 是里程计而非优化位姿），`--camera` 默认也变成 `base-rgbd`；已补上前置的 `rtabmap-export --poses --poses_format 11` 步骤。

**文档能力**

- **新增「前端模块地图」**（`docs/frontend/console-v1.md`）：v0.6.0 新增的 `web/robot_services.js`（618 行）、`web/map_explorer.js`、`web/map_keyframes.js`、`web/src/map_viewer.js` **此前在任何文档里都没有被命名过**——维护者无法从文档找到代码。现逐个列出职责与关键约束，并写明两条修改经验（新增 classic script 要同时改 `embed.go` / `index.html` / `observability_test.go`；`web/src/*.js` 是打包输入、three.js 只存在于 bundle 内）。
- 文档索引补上「注册服务工作流」入口（入口 README 早已称其为标定建图的权威指南，索引里却没有），并收录此前**零入链**的 `2026-09-13-demo-map-upgrade-plan.md` 与工作流验收基线。

**过期陈述**

- `rgbd-camera-fix.md` 中三条标记"未完成"的项目**在 v0.6.0 已全部实现**（WebGL 地图展示、观测对话框、语义导航），已据代码逐条更新，并保留原根因分析作为历史记录。
- `home-scene-expansion-plan.md` 的"现状（已核实）"写着可操作物体 1 项、实际 4 项；`home-scene-operations.md` 把彩色杯基线称作"主路径"，与索引和 README 矛盾，已改称回归基线。
- `README.md` 把 12 步工具表来源写成 `tools.json`（那里是另一套 25 个蛇形工具），实际是 `core/robotcontract/contract.go`；控制台入口写"三个"、实际五个（工作台/任务记录/整机标定/SLAM 建图/我的机器人）。
- `console-v1.md` 的"最新前端回归 137 项"实为 **368 项**；同日审计文档的 Web 325 已标注为首轮时点。
- `gazebo-house-operations.md` 引用了已在 895bfc3 删除的 `skills/manipulation/home_route.go`，改为 `edge/robotclient/semantic_routes.go`；`deploy/robot/navigation/README.md` 两个链接少一层 `../`（该目录不在链接测试扫描范围内，因此一直是坏的）。
- API 参考补上 `slam_session` / `slam_keyframes` 两个特殊角色的字节预算、`?sha256=` 固定与三个错误码；路由占位符 `{evidenceId}` 改为与注册一致的 `{evidence}`；工具层文档补上可选工具 `plan_work_area` 及其注册条件。

### 2026-09-13 装修家庭与关键帧诊断

- 新增 `make home-furnished`，固定版本 MIT-0 家具资源、暖木/瓷砖装修、陶瓷杯与收纳盘；独立任务、地图和标定目录。生成资产可重建，旧彩色杯场景保留为兼容基线。
- 保存地图提供实测 RGB/高度辅助色、房间聚焦、本机点云标记，以及可点击的关键帧和方向。详情展示同帧 RGB/深度预览、原始与优化位姿、配准/回环质量和版本标识，支持旧地图缺失提示、哈希校验及资源预算。
- 修复家庭语义物品登记、完整自然语言子句与操作房间绑定、窄通道精确间距检查、本地工具执行预算和驱动取消/到期约束。托盘检测改用当前 RGB-D 连续边段，拒绝缺边与歧义，不依赖历史位置缓存。
- 新场景通过注册服务完成标定和实际移动 SLAM，保存 205 个带图像关键帧、63,083 个 RGB 点。巡检、厨房检查和最终 12 步杯子收纳返回均有成功证据；不同运行批次和失败记录如实保留。
- 更新[装修家庭操作](docs/guides/furnished-home-demo.md)、[关键帧检查](docs/guides/slam-keyframe-inspection.md)与[实际验收记录](docs/development/2026-09-13-furnished-home-acceptance.md)。当前是受限家庭仿真验收，不代表已训练通用策略或完成实机迁移。

### 较早的开发记录

以下保留当时的调查、方案和限制；当前入口及已完成状态以以上指南和对应验收记录为准。

- 新增 [Real2Sim 方案](docs/development/real2sim-from-robot-slam.md)，回答"实机只能靠机器人 SLAM 建图，怎么做 real2sim"。**核心结论：机器人自己的 SLAM 测绘成果就是仿真场景的来源**，而不是下载数据集——那是在别人的房子里仿真。自己的测绘更好，因为①是自己家、尺寸布局真实；②由将要行动的那台机器人测绘，传感器特性与可达范围天然一致；③仿真与真机共享同一张地图。
- 方案的关键设计是**分段保真度**：地板/墙面由点云平面拟合（平面就是平面，精度远超需求）；大件家具用点云聚类凸包（只需挡路）；**可操作物体用规范模型 + 感知给出的位姿，不从点云重建**（扫描在 1m 外约 1cm 分辨率，抓取远远不够）。最后一条解释了为什么仿真物体与真机物体本来就该分开对待——前者给形状，后者给位置。
- 现实约束如实列出：玻璃/镜面处点云缺失会变成"洞"、点云无语义所以房间划分需用户确认、扫描不足以重建小物体、单次建图漂移会让长走廊弯曲、**MuJoCo 网格碰撞体是凸包所以凹形家具会被填实**（这条最容易低估）。
- **Replica 重新定位**：它不是 Real2Sim 的路径，而是"还没有真机测绘时的替身"。关键洞察——**转换器的输入是点云，Replica 场景与真机测绘都能转成点云，所以同一个转换器两边通用**：先在 Replica 上把转换器调通，真机测绘出来直接接上。
- 与现有代码的衔接已列明：读 SLAM 库、点云→地图产物、仿真测绘脚本、机器人模型与放置**都已具备**；**缺的是"点云→MuJoCo 碰撞几何"转换器**，以及房间分割与规范物体库。**Real2Sim 的工程量集中在转换器，不在获取场景数据。**

- **仿真 SLAM 建图：地图现在由机器人真的开过去采集得到**（用户要求"不要直接用完整资产，控制小车在仿真环境移动，通过 slam 完整建图"）。新增 `scripts/build_sim_map.py`：机器人在 `home_task` 家居里走完 **9 段房间路线**（客厅→走廊→厨房→走廊→卧室→浴室→卧室→走廊→客厅，回程让相机从另一侧看同一面墙），**沿途按固定间距采集真实深度帧**并逐帧反投影，位姿取运行时发布的那一份。
  - 实测：**63 个位姿、63 帧真实深度、4,793,221 点**，1.5 秒完成；落地为可校验的地图目录。**这不是合成点云**——每一点都来自机器人真正到过的位姿上的一次拍摄。这正是"地图"与"地图的图片"的区别：合成点云可以看起来正确却什么也证明不了。
  - 底盘在关节上（`slide_joint_x/y` + `hinge_joint_z`）而非自由体，且运行时把世界 x 映射到 `slide_joint_y`、世界 y 映射到 `slide_joint_x`——脚本遵循同一约定，使测绘位姿与运行时位姿是同一件事。
- **"地图太粗糙"主要是参数而非场景**：默认 63 帧 + 0.04m 体素把 480 万点压到 65,707 点。改用 `--frame-spacing-m 0.12 --voxel-m 0.012 --lod-levels 6` 后为 **593,104 点（约 9 倍）**，7.12 MB，校验通过。**同一套仿真、同一套代码，只是一个参数。**

- **标定页现在展示全部内外参，而不是一句"16 个舵机、2 个相机"**（用户反馈"不要就展示无用的文字"）。新增 `GET /v1/calibration` 提供**标定文档本身**（不只是进度摘要），页面按人找得到的方式分组渲染：

  | 分组 | 内容 |
  | --- | --- |
  | 左臂 / 右臂 | 各 6 个舵机，**舵机 ID、零点偏移、最小、最大**四个字段可编辑 |
  | 头部与底盘 | 4 个（头部旋转/俯仰、左右驱动轮） |
  | 相机 | 分辨率、内参 fx/fy、主点 cx/cy、畸变模型、安装 link、位置 xyz、姿态 rpy |

  浏览器实测：**64 个可编辑输入框**（16×4）、2 个相机卡片、3 个分组、无报错。**编辑后的保存尚未接线**（没有 PUT 路由），目前是可见可改但改动不会落盘——这一点必须如实说明，避免让人以为改了就生效。

- **修掉标定页的重复文案**（用户反馈"开始前的安全检查重复了"）。实测页面此前同时显示"标定已完成"标题、又在小结里重复一次"标定已完成：…"，并且列完最后两张步骤卡片后还要再显示"已完成的 20 步"——同一件事说三遍，而**真正该看的参数一个都没显示**。现在：完成后**不再渲染步骤卡片**，直接展示参数；标题只出现一次。

- **三维地图"缺字段"的判断是错的，真正缺的是一份资产**（已查清，[详情](docs/development/rgbd-camera-fix.md)）：

  | | 是什么 |
  | --- | --- |
  | 控制台**唯一**的三维资产 | `robocasa-handoff-v1`（RoboCasa 厨房，34MB `scene.glb`） |
  | 仿真实际运行场景 | `home-task-rgbd-mobile-manipulation-v1`（四房间家居） |

  **两者不是同一个场景——控制台里没有仿真所跑场景的三维资产。**

- **所以"把 `scene_id` 填上"是错的解法。** 让家居仿真发布 `scene_id = robocasa-handoff-v1`，三维视图**确实会亮起来，但显示的是一栋不同的房子**——一个**会说谎的数字孪生**（屏幕上是 RoboCasa 厨房，机器人实际在四房间家居里抓杯子）。这比"三维不显示"危险得多，因为**不显示至少是诚实的**。

- **控制台的校验逻辑本身是对的，不应绕过。** 它拒绝加载与权威身份不符的资产，正是防止数字孪生说谎的机制。之前把它当成 bug 来查，方向错了。

- 正确修法（明确了，未实施）：① 把 MuJoCo 家居场景导出为 GLB；② 为它建立自己的 manifest（`sceneId = HOME_TASK_SCENE_REVISION`，`modelHash` 取导出内容哈希）；③ 仿真运行时在 `/v1/world` 发布携带该身份的实体（这才是仍然需要的那层契约改动）；④ 控制台按身份选资产，不匹配时**明确降级**而非拿另一份顶上。

- **整机标定在仿真环境里真正跑起来了**（用户反馈"仿真环境中用不了"）。之前标定页永远显示"还没有开始标定"，因为没有任何会话文件、面板也只在点按钮时才取数。
  - `scripts/calibrate_guided.py` 新增 `--yes`：对**仿真机器人**逐步自动确认，于是同一个 20 步向导能在仿真里完整走完；并加了硬性守卫——**`--yes` 不能配合真机使用**（"真机标定必须有人在场逐步确认"，违反直接 exit 2）。
  - 仿真的标定基线由 MuJoCo 模型推导（`artifacts/calibration/sim-base.json`）：**16 舵机 + 2 相机**，head fx=171.4 / base fx=216.5，parent 分别为 `head_tilt_link` 与 `chassis`。
  - 实测跑完全程：`标定已完成：16 个舵机、2 个相机`，版本号 `8b394d49…`；控制台面板显示 **标定已完成 / 20 / 20 步**。
- **左侧页面打开时自动取数**：标定与 SLAM 两页承载的是实时状态（标定进度、地图覆盖率），打开就该看到当前值。原先只有点「刷新」才取数，页面看上去是空的——这会被读成"仿真做不了这件事"，而不是"还没取"。
  - 实现上**不能只靠 `hashchange`**：实测控制台切换页面时该事件不触发（hash 由 `replaceState` 设置），因此改为**监视路由变化**，每次进入取一次。
- 仍未完成：**地图管理**（新建/选择地图）与**三维稠密地图展示**（阻塞于 `/v1/world` 场景身份契约）。

- **多物体任务的"报告成功但只做一半"已修复**，根因是我自己在 intent 层引入的：我把路线只挂在第一个 transfer 上，理由是"避免重复规划同一段路程"——**但规划器是从每个 intent 自己的 `RouteRooms` 生成导航步骤的**，于是第二个 transfer 只规划出 `observe` 一步，任务在什么都没做的第二个子任务上判定成功。
  - 修法：**每个 transfer 都带完整路线**；`ReturnToStart` 只给最后一个，否则机器人会在两个物体之间先跑回家。解析层测试已按新决策重写（不再断言"路线只属于第一个"）。
  - 修复后的实测：第二个子任务**完整执行** `observe → navigate → verify_arrival → observe → resolve(blue-cup) → plan_grasp(blue-cup) → pick(blue-cup)`，然后**诚实地失败**：`GRASP_NOT_REACHED blue-cup`，任务状态为 **`RECOVERABLE_FAILURE`**。
  - **这才是正确行为**：同一个"只做一个物体"的情形，之前报 `SUCCEEDED`，现在报失败。**修复不是加了一道成功守卫，而是让计划变完整，从而让失败能够浮出来**——一个不完整的计划既做不了事、也报不出错。
- 新的、更小的问题：`GRASP_NOT_REACHED blue-cup` 是**物理可达性**问题——蓝杯在 (1.58, 3.90)，而红杯在 (1.82, 3.68) 可抓。属于摆放与臂展匹配，不是编排缺陷。
- 验证：修复后 `make home-accept`（单物体）**exit 0**；两物体请求实测**子任务 1 全程完成、子任务 2 正常开始**（此前在子任务 2 接地前就失败）。
- 过程中确认了一件事：反复跑验收会因为**仿真栈状态残留**得到不同失败（先 `WORKCELL_CALIBRATION_MISMATCH`、再 `NAV_STEP_LIMIT`），**重启栈后即 exit 0**——不是代码问题，跑验收前需重置栈。

- **「回看当时观测」不再滚动页面**（用户反馈"会莫名其妙划到页面底部"）。改为**在对话框里打开**观测面板，而不是滚动到它——实测浏览器中**打开与关闭前后 `scrollY` 始终为 0**，页面完全不动。
- 根因是**距离而非动画**：工作台文档高 11050 px、视口 800 px，而观测面板位于距按钮约 13 个屏幕处。平滑滚动一万像素，视觉上就是"一下到底"；沿途那些与本次观测无关的面板，正是用户说的"空白无效信息太多"。调 `block` 或加长动画只会让长距离穿越看起来更慢，不会让人少滚一点。
- 同时把面板**移出页面流**（去掉 `data-page-panel`、保持 `hidden`），工作台文档高度从 **11050 px 降到 10343 px**，观测内容只在需要时出现。关闭对话框后面板回到 `#local-evidence-home`，不会丢失。
- 测试按约定**重新指向新决策**：现在断言"打开观测时**不产生任何滚动调用**、对话框已打开、面板在对话框内"，并新增"关闭后回到原位"用例；另外**扩充了测试假 DOM**（补 `parentElement` 追踪与 `showModal`/`close`），因为一个记不住节点归属的假 DOM 无法测试"面板能回到原处"。

- **整机标定与 SLAM 建图各自成为左侧独立标签**（用户反馈"混在一起完全看不懂"）。侧栏现在是：工作台 / 任务记录 / **整机标定** / **SLAM 建图** / 我的机器人 / 开发诊断。
  - 工作台只留「开始使用前」就绪清单与任务输入——它回答的是"能不能开始用"；
  - 整机标定页只放标定卡片流；
  - SLAM 建图页放场景地图（占据栅格 + 覆盖率）与稠密点云两部分。
  - 理由写进代码注释：**把就绪清单、标定向导、点云查看器塞在一屏，正是首次使用者迷路的地方**；这三件事各自有开始和结束，应该各自成页。
  - 浏览器实测：#calibration / #mapping / #workspace 三页切换正确，互不串页，无报错。

- **底部相机已修正并全绿上线**：`base_depth` 从世界 z=**1.535**（比头部相机还高）的 150° 鱼眼 45° 俯视，改为底盘底部 **z=0.160、前向 15° 俯角、58° 垂直视场（Intel RealSense D435i 深度流参数）**。实测正向 `(0, 0.966, -0.259)`——它现在确实拍摄机器人前进方向的深度图，而不是俯视地面。
- 过程中修掉**自己补丁里的两个错误**：① 我声称"tabletop 逐字节不变"，却把 `camera_fovy = 58` 写成无条件赋值，连带改掉 tabletop 的 100°，**5 个测试失败中有 3 个源于此**（含两条自滤波）——按场景区分后自滤波立刻全通过；② 头部相机改 58° 会让视野变窄、原本在 x=1.58 的杯子**移出画面**，感知一个都报不出来（症状像检测器坏了，实为摆放与视野不匹配），移到 x=2.55 仍未检出，**在上下文耗尽前未定位成功**。
- 因此**头部相机保持 70° 未改**，并把补丁里改头部的部分撤销。理由写进文档：家居感知链（工作体积、颜色掩码、支撑面）全按 70° 调过，且 `xlerobot.xml` 与 tabletop 共用；**把两件事混在一个改动里会让两件都无法独立验证**。头部统一到 D435i 是独立的一项工作。
- 验证：`sim/mujoco/tests` + `tests/` **1004 passed / 7 skipped**，`make home-accept` exit 0，`ruff` 干净。

- **确认并记录了用户实机检查发现的相机问题**（[详情](docs/development/rgbd-camera-fix.md)，补丁在 `docs/development/patches/rgbd-d435i-cameras.patch`）：
  - **底盘相机位置错误**（实测）：`base_depth` 世界 z=**1.535**，比头部相机（1.185）**还高**，且是 150° 鱼眼 45° 俯视地面——既不在底盘底部，也不看前进方向。用户要求的"底部前向深度相机"与源码里的"front mast, 45 degrees down"是两回事。
  - **两台相机参数不一致**：头部 70°、底部 150°，是两种不同传感器。按要求统一为 **Intel RealSense D435i** 深度流参数（58° 垂直）。
  - 补丁改后实测：两者均 58°；`base_depth` 移到 z=0.160、前向 15° 俯角、朝前 `(0.966, 0, -0.259)`。tabletop 场景逐字节未动。
- **没有直接改进 main**：套用后 **5 个测试失败**，其中两条是**自滤波**（决定机器人能否把自身机身从深度图剔除，安全相关）——相机换位后机身自遮挡形状完全变了，旧参数必然失效，**这类改动必须重新推导而不是让测试变绿**。失败清单、性质与实施顺序已写入文档：先重算自滤波 → 重指向"俯视相机"测试 → 复核感知阈值 → 再跑验收。
- 如实标注一处差距：垂直 58° 与 D435i 一致，但水平 73°（帧缓冲 4:3）而非 87°（D435i 深度流为 16:9）。要拿到真实 87° 需把帧缓冲改为 16:9。

- **intent 层支持多物体自然语言任务**：请求现在解析成**意图序列**而不是只取第一个物体。实测 `从客厅出发，去厨房拿红色杯子放进蓝色收纳盒，再拿蓝色杯子放进蓝色收纳盒，然后回到客厅` → **2 个 transfer，依次 red → blue**，且**路线与返航只挂在第一个 transfer 上**（否则会为每个物体重复规划同一段路程）。
- 支持两种表达：**分句式**（`…，再拿…`）与**并列式**（`拿红色杯子和蓝色杯子放进蓝色收纳盒`，共用一个目标）。并列式需要新的 `homeObjectList` 正则——第二个物体前没有动词，允许 `和/、/及/与/还有` 代替；单物体语法仍要求显式动词。
- **物体与目标可以分处不同子句**（`拿红色杯子，放进蓝色收纳盒`）：物体先挂起，等某个子句给出目标再配对。
- 词表补齐：**`黄色`** 与 **`盘子/碟子`、`碗`** 加入物体正则与类别映射，黄盘现在可以被自然语言指代。
- 未配对的目标位置（有一条"放进…"但前面没有物体）现在会**要求澄清**而不是被静默丢弃——与前一轮修掉的静默截断是同一类问题。
- 回归：单物体家居闭环验收仍通过（`make home-accept` exit 0），`go test ./...` 全绿。

- **多物体自然语言任务实测：跑不通，且有两种不同的失败方式**（细节与证据记入[家居场景扩充方案](docs/development/home-scene-expansion-plan.md) 3.6 节）。场景侧已准备好 4 个可观测物体，**缺的是语言层**。
- **最危险的一种：指令被静默截断。** 请求"…拿红色杯子放进蓝色收纳盒，**再拿蓝色杯子放进蓝色收纳盒**，然后回到客厅"——**任务报告成功，但只搬了红杯子**：确认步骤与单物体流程完全一致，产出中只有 `object_id: red-cup`，没有任何第二次 pick/place。用户要求两件、系统做了一件还报成功，**比直接失败更糟，因为没有人会去检查**。
- 另一种：`把红色杯子和蓝色杯子都放进…` 使 `intent` 退化为 `action: home_route`（`object/source/destination` 的 category 全为空），随后以 `NAV_STEP_LIMIT: home route has no movement segments` 失败。根因是 `homeObjectAction` 正则**要求每个子句都有 拿/取/抓/拾 动词**，而"把…和…都…"以"把"开头。
- 三条可执行结论：**颜色词表已含 蓝色/绿色**（缺的不是颜色，蓝杯绿杯可被命名）；每个子句必须能独立解析，并列结构不行；**缺 黄色 与 盘子/碟子** 词，黄盘无法被指代。修法（支持并列或**明确澄清**、意图数少于子句数时必须澄清、补词表）已写入文档，**在 intent 层修好前不应对外声称支持多物体任务**。
- 回归确认：加入 4 个物体后，原单物体家居闭环验收**仍然通过**（`make home-accept` exit 0，红杯稳定 `inside:kitchen-bin`）。

- 家居任务场景扩到 **4 个可操作物体**：红杯 / 蓝杯 / 绿杯 / 黄盘，加蓝收纳盒。实测观测集与目录**完全一致**（`all advertised observed: True`），并新增黄色掩码（与橙色靠 `g` 相对 `r` 的比例分开，否则饱和黄会被朴素橙色判据误判）。
- **又抓到一个静默失败模式并加进校验：物体不能摆在收纳盒上方。** 收纳盒占据台面很大一块（x∈[1.83,2.47]、y∈[3.42,3.94]），我最初把绿杯/橙碗/黄盘都放在它上面——它们**掉进盒子里**，落到支撑面门控之下，于是**永远不被感知**。三个物体里绿杯侥幸仍被检出，蓝杯反而因此消失，症状看起来像感知 bug。现在 `objects_over_the_bin()` 在模型校验时直接拒绝这种摆放。
- 橙碗**主动移除**：它的实测直径 0.17 m 超出当时的尺寸带，放宽到 0.22 m 后仍检不出，说明橙色掩码在**渲染光照下**的实际像素与材质 rgba 假设不符（黄盘同样饱和却能检出，所以不是尺寸问题）。**我选择移除而不是留着**——向 Agent 宣告一个它找不到的物体，比少一个物体更糟；`PERCEPTION_PENDING` 机制仍在，供下次记录已知缺口时使用。
- 顺带修正上一次遗漏的一致性：模型校验现在遍历整个目录，断言每个物体都有 body 与 free joint（此前只检查红杯）。

- 家居任务场景扩到 **3 个可操作物体**（红杯 / 蓝杯 / 绿杯，外加蓝收纳盒），**全部可被 RGB-D 感知真实观测到**（实测观测集：`blue-cup`、`green-cup`、`kitchen-bin`、`red-cup`）。
- **修掉上一轮埋下的一个真问题**：上一轮加了蓝杯，它在物理场景与目录里都存在，但**感知根本不会报告它**——蓝掩码当时只用于识别大收纳盒。也就是说标称"2 个物体"，实际只有 1 个对 Agent 可用。这类"目录承诺了、感知交付不了"的缺口比物体缺失更糟：Agent 会被告知物体存在然后找不到它，故障看起来像感知 bug。
- 修法是给感知加**通用彩色小型物体检测**（蓝/绿/橙三个掩码 + 与红杯同一套"支撑面之上、紧凑尺寸带"的测量判据），并新增 `PERCEPTION_PENDING` 声明：**目录里已登记但感知尚不支持的物体必须显式列出**，且有一条测试遍历整个目录断言"要么被观测到、要么在待办清单里"。缺口因此无法再被静默引入；本轮把该清单清空到 `()`。
- 蓝色同时被收纳盒和小物体使用，靠尺寸带区分：收纳盒要求 `x 跨度 > 0.05 且 y 跨度 > 0.15`，杯级物体要求跨度在 `0.025–0.16` 之间，两者不会互相误判。

- 家居任务场景从 **1 个可操作物体扩到 2 个**（红杯 + 蓝杯），台面从 1.5×0.5 m 加宽到 **1.9×0.64 m**（放得下多物体的前提）。关键结构改动：**物体位置、目录、构建、运行时摆放现在读同一份表**（`HOME_TASK_OBJECTS` / `HOME_TASK_OBJECT_PLACEMENTS` / `HOME_TASK_WORK_VOLUME`），因此不会再出现"场景里有但目录里没登记"（或反过来）的漂移——上一次尝试正是栽在这里。
- 新增测试断言每个物体**都落在感知工作体积内**、且**彼此净空 > 5 cm**。这两种失败在运行时都是静默的：重叠的物体开局就在碰撞，而工作体积之外的物体**永远不会被感知**，读起来像感知 bug 而不是摆放错误。
- 上一轮那个"感知全瞎"的回归**定位为分步引入时的耦合，而非台面尺寸或单个物体**：实测「只加宽台面」通过、「加宽台面 + 增加 1 个物体」也通过。因此这次改为**增量引入**，8 项家居测试全绿。
- 目录测试按约定**重新指向新决策而不是放宽**：现在断言两个物体都在目录里、都有 free joint，并新增摆放校验。

- 新增[家居场景扩充实施方案](docs/development/home-scene-expansion-plan.md)。摸清现状后的关键事实：5 房间家居的**任务夹具不在 XML 里**，而是由 `rgbd_navigation.py::_extend_home_task_spec()` 在运行时构建（任务台、蓝色收纳盒、红杯子），**可操作物体只有 1 个**；感知是**颜色分割**（红/蓝两个掩码）限制在一个固定的厨房工作体积内，且**刻意不读仿真真值**。
- 方案给出四处必须同步的改动（场景构建 / 感知颜色表 / `HOME_TASK_OBJECTS` 目录 / 运行时摆放），并指出两个容易漏掉的点：现有台面只有 1.5m×0.5m，摆 15–20 个物体会互相穿透需要加长或加层；感知扩充后**必须加"干扰物零检出"用例**——灰色/棕色物体不应产生任何观测，这证明感知不是"看见什么就报什么"。
- **关于 RoboCasa365**：它确实 vendor 在 `datasets/robocasa/`，但 `robosuite` 与 `torch` 均未安装，且它使用自己的 env 栈、不跑我们的 runtime，因此**不带**我们的感知契约、工具层、安全监督、证据链与标定关联。方案建议把"能否装上并加载一个场景"作为独立可行性验证，不与家居扩充混做。

- 性能验收阻塞点**已定位到可执行结论**：本仿真配置下三维场景不激活，控制台**按设计回退到语义 Canvas**（实测 `#fleet-godview-canvas` 可见、`#fleet-godview-webgl` 隐藏、状态 `VISUAL LOADING`、`WORLD CONNECTING`）。排查中排除了两个误导项：控制台那条"图像加载失败"**其实是页面 favicon**（无害），而 `/assets/scenes/robocasa-handoff-v1/manifest.json` **返回 200**（场景资产在服务）。结论：既非资产缺失，也非地图图层引入——它不点「加载点云」也会出现，且与地图产物/路由/解码无交集。解除条件已写明：让三维场景进入 `LIVE`。

- 定位了性能验收的阻塞点：控制台打开后**不做任何操作**等待 8 秒，三维场景始终停在 `VISUAL LOADING / 正在校验本地场景资产`，同时控制台报出一条**图像加载失败**（`data:image/svg+xml,…` 资产）。判定：**这是既有问题，与地图图层无关**——不点「加载点云」也会出现；失败发生在 `registry.load()` 的场景资产校验路径上，`createFleetWorldRenderer` 既未走到 `showFleetWorldWebGL(true)` 也未抛到 `DEGRADED`，而这条路径与地图产物、路由、点云解码没有任何交集。日志已附在运维文档里。

- 性能验收（P1 第 5 步）**未完成，且当前环境测不了**，原因已查明并记录。尝试测量时得到过 60 FPS，但**该数字作废**：三维画布处于 `hidden`，页面状态为 `VISUAL LOADING — 正在校验本地场景资产` / `WORLD CONNECTING`，`fleetVisualReady` 始终为 false，**点云根本没有被绘制**——那 60 FPS 是空闲页面的帧率。不做这个区分就会把"页面空转"当成"百万点流畅"。
- 本轮真实测到的：控制台 `DOMContentLoaded` **45 ms**；100 万点地图在浏览器中**3 层共 666,181 点同时驻留且无报错**；LOD 0 构造成 `THREE.Points`（`position.count = 1011`、`itemSize = 3`、`color.normalized = true`）。这些是数据链路与几何构建的实测，**不是渲染帧率**。
- 运维文档已把"实测 / 未测 / 为何测不了"三者分开写明，并给出取得有效帧率结论的前提条件。

- 新增 `scripts/build_map.py`（`--synthetic N` 造压测地图 / `--database` 读真实 RTAB-Map 库），以及[稠密地图运维文档](docs/development/dense-map-operations.md)：构建、`TANGYING_MAP_ROOT` 部署、接口表、依赖、已知限制与排查。
- 新增 `survey_health()` 与 2 项测试：识别"这不是一次干净的建图"。实测那份 208MB 库**包含 2 张地图、约 49.9 小时跨度的多次会话、1237 个节点中 1235 个已被删除（weight < 0，仅 2 个有效）**——这正是位姿全部相同的根因。`build_map.py` 对此**拒绝导出**（退出码 2），除非显式 `--allow-unhealthy`。
- 文档中如实标注**性能指标未测**：需求里的"首屏 < 2s""百万点 > 30 FPS"**没有任何实测数据支撑**；已测的只是最粗层 1,018 点与构建耗时 4 秒，这不等于渲染帧率。

- 新增**真实 RTAB-Map 数据库导出适配器** `rtabmap_export.py`（10 项测试，其中 3 项直接跑在真实数据库上）。用导航栈 Docker 卷里那份**真实 208MB 测绘库**（仿真家居场景，1237 个节点）验证，而不是照 schema 猜测。实测发现并据此实现：
  - `Node.pose` 是 48 字节 = 12 个 float32（3×4 变换），读轨迹不需要任何库；
  - `Data.depth` 是**用 PNG 包着原始 float32 米制深度缓冲**，不是图像。必须完整 PNG 解码后把像素字节取回来；只 inflate IDAT 会拿到既非滤波前也非滤波后的字节，**当 float32 解释会得到"看起来像深度但不是"的数字**——这个错误我先犯了，靠真实数据才发现；
  - `Data.image` 是 JPEG；颜色必须**用与点相同的有效性掩码采样**，否则第一个无效像素之后所有颜色都会错位；
  - `Data.scan` 在 RGB-D 模式下为空；`Admin.opt_cloud` 为 **NULL**（RTAB-Map 只在配置开启时才持久化拼装好的点云），所以稠密地图必须由深度帧反投影重建——实测重建出 **17,927,266 点**。
- **最重要的发现**：这份真实库的 **1237 个节点只有 1 个不同的 `Node.pose` 值**。也就是说它**没有轨迹**——在这种库上反投影，会把 1237 次扫描全部堆在同一个位置，产出一份**稠密、看起来合理、但完全错误**的点云。因此 `require_distinct_poses()` 让这种情况**直接报错而不是给出点云**：没有任何下游环节能分辨那种输出是错的。有测试固定这个行为（含"集合推导式收集的是生成器对象、守卫永不触发"这个具体陷阱）。
- 相机内参不从 `Data.calibration` 取（那是 RTAB-Map 的内部序列化），而是用机器人已有的 `robot.calibration.v1` 文档——这正是当初设计标定修订号关联的用途。

- 稠密地图 P1 第四步（部分）：新增 `web/map_cloud.js`（16 项测试）与“稠密地图”面板，以及 `GET /v1/maps/{id}/cloud?lod=N` 分层路由（1 项测试）。**分层加载的完整策略已实现并测试**：分块解码、按相机距离选层、常驻层数上限与淘汰、失败时保留粗层。
- 分块解码**严格校验**：magic、版本、以及"声明点数 × 每点字节数是否等于实际字节数"。半截分块被解码成几何体看起来就像一张地图，操作者无从分辨——有测试专门断言截断分块抛错而不是被画出来。颜色缺失时返回 `null` 而不是全零，渲染端才能区分"没有颜色"和"黑色"。
- 两条加载策略写进测试：**最粗一层永不被淘汰**（细化失败时画面不能变空，这正是保留第 0 层的理由）；按距离选层必须**单调**（靠近时绝不会反而选到更粗的层）。
- 分层路由的 `lod` 会与 manifest 的 `lodLevels` 做范围校验，解析出的路径同样被限制在地图目录内。
- 浏览器实测（真实运行，非推断）：点击"加载点云"后依次取回 `?lod=4`、`?lod=0`、`?lod=3`，解码出 **LOD 0 = 1,011 点、LOD 3 = 86,116 点、LOD 4 = 216,374 点，合计 303,501 点**，无报错。管线 → manifest → 路由 → 选层 → 解码 → 上报，整条链路在真实浏览器中跑通。
- 修掉一个**只在浏览器里才会出现的 bug**：把全局 `fetch` 存成实例属性后当方法调用，`this` 变成 layer 而不是 window，浏览器报 `Illegal invocation`。所有 node 测试都注入了自己的 fetch，因此全都测不到它——现在有一条测试专门用 vm 上下文里的全局 fetch 复现这个场景。
- **three.js 几何交接已接线**（`web/src/map_cloud_points.js`，随 three.js 一起打进 bundle）：解码出的 typed array 直接变成 `THREE.BufferGeometry` 的 `position`/`color` 属性与 `PointsMaterial`。颜色属性声明为 `normalized`——uint8 颜色若不归一化，画面会亮 255 倍，看起来像一团白色，很容易被误判成解码器坏了。点云被设为不可拾取（`raycast` 置空），否则它会吞掉本该落在机器人或语义图层上的点击。
- 浏览器实测几何交接：LOD 0 解码后生成 `THREE.Points`，`position.count = 1011`（与解码点数一致）、`itemSize = 3`、`color.normalized = true`、`vertexColors = true`、拾取已禁用。三维画布不在当前标签页时，面板仍如实报告解码统计并注明"三维视图未就绪"，而不是静默什么都不显示。
- bundle 的公开接口契约已相应收紧到 `["AssetRegistry", "MapCloudPoints", "RobotModelInstance", "WebGLSceneRenderer"]`——three.js 只存在于这个 bundle 内部，地图点云要转成几何体只能经由它。

- 稠密地图 P1 第三步：新增 `GET /v1/maps`、`/v1/maps/{id}`、`/v1/maps/{id}/cloud`、`/v1/maps/{id}/artifact/{role}`（`console/maps.go`，7 项测试），已登记进 API 参考。只读——建图是流水线的事，控制台没有理由删除别人的测绘成果。
- **Range 是这条路由存在的理由**：LOD 按需加载完全依赖服务端遵守 `Range`。有一条测试专门断言分段请求返回 `206` 且 `Content-Range` 为 `bytes 100-199/10000`、响应体正好是请求的那 100 字节——若退化成返回 `200` 加整个文件，所有客户端会静默下载整份点云，LOD 设计一分钱都不值。
- **双层路径防护**：manifest 契约已拒绝逃出地图目录的 `href`，路由层**再检查一次**，因为 manifest 可能来自别处；`mapId` 与目录双重校验。有测试用真实路径穿越（`..`、`../secret.json`、`a/b`）尝试读取地图根目录之外的文件，断言既不返回 200 也不泄漏内容。
- 单个损坏的地图不拖垮整个列表（缺 manifest 的目录被跳过），空地图根返回空列表而非错误，未知地图或未声明角色返回 404 而不是"空成功"。

- 稠密地图 P1 第二步：新增转换流水线 `map_pipeline.py`（17 项测试）。输入点云与轨迹，输出一个**可被 `map_manifest` 校验通过**的地图目录：LOD 点云分层、占据图（PNG + JSON）、轨迹 GeoJSON、manifest。**整条链路在合成数据上跑通**，不需要机器人、数据库或 RTAB-Map 环境。
- 合成点云 `synthetic_home_cloud()` 刻意做成"像真实扫描"而不是均匀随机：地板/墙面/家具/噪声按比例分布，并且**故意混入 10% 重复点**——只对均匀随机点表现良好的降采样器不能作为证据。同一 seed 结果完全可复现，压测可重复。
- 点云格式：COPC 是目标（单文件 + 内建八叉树 + Range），但 COPC 意味着 LAZ，而本环境没有任何 LAS/LAZ 工具链。因此 MVP 先用**自定义分块格式**（20 字节头 + 原始 float32 位置 + uint8 颜色，Worker 用 `DataView` 即可解码），不把重依赖放进关键路径。manifest 与格式无关，日后换 COPC 只改写入端与浏览器读取端，**文件扩展名就是格式适配器的接缝**。
- 颜色用 uint8 而非 float32：颜色是显示需求，百万点从 12 字节降到 3 字节，省 8MB。
- 过程中修掉三个真 bug：分块头长度写错（20 字节写成 24，解码必然失败）、artifact 的 `href` 没有相对地图目录（导致 trajectory 校验报"文件不存在"）、**PNG 漏了 8 字节签名**（任何解码器都会拒绝）。PNG 现在用 Pillow 独立解码验证过：未知=232、可通行=252、障碍=100，三者互不相同。
- 实测（100 万点合成数据）：LOD 五层 `[1018, 6473, 38465, 129077, 348669]`，构建 4.0 秒，产物合计 7.9MB，全部角色校验通过。**最粗一层 1018 点**，正是首屏立刻可画的数量。

- 稠密地图查看器 P1 第一步：新增 `map.manifest.v1` 契约（`robot/gateway/tangying_robot_gateway/map_manifest.py`，20 项测试）。一份地图由多个文件组成（点云 LOD、占据图、轨迹），manifest 是唯一说明它们身份、坐标系、范围与内容哈希的文档；`cloud` 与 `grid` 是必需角色，`trajectory`/`robot`/`mesh` 可选。
- 两条安全约束：**`mapId` 同时是目录名与 URL 片段，因此按单段路径校验**（`../home`、`/abs/path`、`home/2026`、超长名一律拒绝）；**每个 artifact 的 `href` 必须是地图目录内的相对路径**（拒绝绝对路径、`..`、空段），避免一个 manifest 变成路径穿越。
- 沿用两处已有约定：**内容哈希寻址**（与证据系统一致，客户端可校验取到的分块属于这张地图）与**标定修订号关联**（记录这张图由哪份 `robot.calibration.v1` 采集，地图与任务证据可互相核对）。manifest 自身也有内容哈希，被编辑或截断会在加载时报 `HASH_MISMATCH`。
- `verify_artifacts()` 逐个角色返回结果而不是遇到第一个问题就抛错——调用方需要知道"栅格是好的、点云被截断了"，而不是只知道"有问题"。未知字段一律报错，避免拼错字段静默失效。

- 建图指引与地图图层接到前端：新增 `web/map_view.js` 与工作台“场景地图”面板（9 项测试）。一个数据源同时回答两个问题——RTAB-Map 发布的占据栅格既算出覆盖率，也画出地图，所以数字和图永远不会互相矛盾。栅格画在 **canvas** 上而不是 WebGL：占据图是几十万个平面格子，canvas 任意缩放都清晰、重绘零成本，且不与三维场景争同一份 GPU 预算。
- 关键视觉决策：**未走过的区域画得比可通行区域更浅**且明显更暗。把未知区域画成和自由空间一样，是半成品地图看起来像已完成的主要原因——这正是操作者最需要一眼看出来的东西，有测试专门断言两者颜色不同、且障碍最深。
- 面板给出覆盖率、已确认可通行/还没走过/障碍的格数与分辨率、图例，并在图上标出机器人当前位置；位姿落在栅格之外时不画（而不是画错位置）。地图未就绪时说明原因并指向开发诊断，**不画一张空网格冒充地图**。

- 整机标定向导接到前端：新增 `web/calibration.js` 卡片流与工作台“整机标定”面板。屏幕上依次是**当前第几步 / 共几步**、**现在要做的动作**（大字，用向导原文）、进度条与“已完成 4/20 步；还差左臂 3 个关节……”的总结；当前步骤高亮为“现在做这一步”，其后两步显示为“稍后”，已完成的收进“已完成的 N 步”折叠区——二十张卡片一次铺开对操作者是一堵墙，他只关心眼前这一步。
- 新增 `GET /v1/calibration/session`（`console/server.go`）：读取标定向导写出的进度快照。**步骤顺序、提示文案与总结都由 Python 向导产生，控制台只负责渲染**，避免出现第二份会与机器人实际行为漂移的流程定义。未开始标定时返回 `available:false` 与一句说明，不是错误；快照损坏时报错而不是猜。
- 向导每完成一步都会原子写入 `<session>.status.json`（`CalibrationWizard.status_path`），因此页面刷新即可跟上进度，不需要重启任何东西。前端是**镜像而非控制器**：它不驱动舵机，标定动作仍在操作者面前的终端里进行。
- 新增 8 项前端测试与 3 项 Go 路由测试；`/v1/calibration/session` 已登记进 API 参考（路由文档契约会拦住未登记的接口）。标定会话属于具体一台机器的测量数据，`artifacts/calibration/` 已加入忽略列表。

- 新增工作台“开始使用前”面板（`web/onboarding.js` + `app.js` 接线 + 样式，12 项测试）：把标定、相机、地图、连接、安全五项目标渲染成**按阻塞程度排序**的清单，顶部直接给出下一步该做什么，每项给“情况 / 要做什么 / 去处理”三行，状态分“需要处理 / 状态未知 / 已完成”并配色，急停永远排最前。
- 两条硬规则：**没有观测到的状态一律报“状态未知”，绝不默认正常**（有测试断言空输入永远不会被读成 ready——对物理机器人错说“可以开始”是这块屏幕唯一不能犯的错）；**不把不同问题混为一谈**（地图读不到时不说“请先连上机器人”，因为机器可能已连上、只是没有导航栈）。标定来源为 `simulation` 时明确写出“不是这台机器测出来的”。
- 面板加载**不发起任何 API 请求**，遵守控制台对 `file://` 页面的零请求契约（测试会数请求数）；地图状态由“重新检查”按钮读取，这是当前已知取舍。

- 新增建图覆盖与指引模块（`robot/gateway/tangying_robot_gateway/mapping_coverage.py`）：覆盖率、frontier 簇、逐房间覆盖、位姿最大跳跃、回环与深度有效率，并把每个问题**反解成一句可执行的话**（"客厅只覆盖了 20%，请进去走一圈"），给出最近的可去 frontier 坐标与行进指引；封死的未知区域不会被当成目的地。相机交集检查 `shared_view_volume()` 走同一份几何代码，并有一条针对**仿真模型**的断言：两机共同可见体积必须 >0.15 m³，否则应改用运动法求外参。11 项测试，全部由仿真数据驱动。
- 修正前一轮公布的一处数据错误：两台 RGB-D 的共同可见体积此前写作 16.5%，那是我手写脚本时把水平半角固定成常数（而非由 fovy 与 4:3 画幅导出）算出的；按正确画幅重算为 **6.0%（2888/48000 采样点，约 0.36 m³）**。结论方向不变（模型里确实有共同可见区域），但余量小得多，因此"最小可用交集写成模型断言"更必要。文档与 Changelog 已同步修正并留下修正记录。
- 新增[标定与建图的 ROS 方案](docs/development/calibration-slam-ros-plan.md)：内参用 `camera_calibration` + ChArUco（门禁：重投影 RMS < 0.5 px，且分区 RMS ≤ 1.0 px）、手眼用 `easy_handeye2` + ArUco 且**用留出位姿验证**（平移 < 5 mm、旋转 < 0.5°）、双 RGB-D 外参优先用**运动法**而不依赖共同视野（配准残差 < 10 mm）、里程计标定作为地基（直线 < 2%、旋转 < 2°、回环漂移 < 50 mm），并给出 RTAB-Map 的参数建议与实施顺序；所有结果统一写入同一份 `robot.calibration.v1`，精度门禁在保存前拒绝不合格结果。

- 新增[实机前置工作的前端方案](docs/development/sim2real-onboarding-frontend.md)，回答四件事：①用视锥采样实测两台 RGB-D 在当前模型中的共同可见区域（**6.0%**、2888/48000 采样点，包围盒 x∈[−0.85,0.85]、y∈[0.12,0.68]、z∈[0.02,0.79] m，约 0.36 m³；初稿写的 16.5% 来自一处把水平半角固定为常数的手算错误，已修正），纠正"没有相交区域"的前提，并给出底盘相机下移收窄、头部相机前倾 10–15°、把最小可用交集写成模型断言、以及无共同视野时用运动法求外参的部署策略；②建图覆盖指标（覆盖率、frontier 数、位姿均匀度、回环收敛、深度有效率、运动过快）与"下一个该去哪"的补拍指引；③稠密地图展示：`three@0.180.0` 已是现有依赖、`webgl_scene.js` 已是 three.js 场景、控制台已渲染观测点云，因此这是既有管线增加图层而非新建技术栈，并给出点云/栅格/轨迹/frontier 四个图层的实现与性能注意事项；④梳理九项需要前端的实机前置工作与实施顺序。文中明确标注该文是方案而非已完成功能，并列出三项必须真机确认的事实。

- 新增**面向非技术用户的引导式整机标定**（`robot/gateway/tangying_robot_gateway/calibration_wizard.py` + `scripts/calibrate_guided.py`）：共 20 步，先逐条确认四项安全检查，再对左右两臂各 6 个关节逐个提示姿态归零（含每臂夹爪），每臂一次扫掠自动采样六个关节的行程，最后头部/底盘与核对保存。每一步都写明是哪条臂、哪个关节、哪条总线上的几号舵机，不出现 `homing_offset`、`range_min` 这类名字。
- 向导的设计约束：一步一个动作；只记录实际读到的值（读数异常则该步保持未完成、不推进进度）；每完成一步写盘，支持 `s` 保存退出与 `--resume` 续做；手臂没动时给出"没有检测到移动，请扶着这条手臂慢慢移动到两个极限后重试"这类可执行提示；扫掠至少采样两次，否则单次采样永远得到 min == max 而误报失败。
- 硬件接口只有一个很小的协议（`describe` / `blocking_problems` / `read_motor_position` / `close`），真机与仿真后端实现同一份，因此整条流程可以在没有机器人的情况下完整测试；`--simulate` 可用于演练，`--list` 只打印计划、不碰硬件也不需要机器人依赖。
- 明确边界：引导流程当前**只采集舵机**；相机内参/外参需由 `--base` 提供（出厂参数、上次标定或仿真推导值），缺失时在开始前就拒绝而不是走完再报错。运行时 RPC 与控制台面板、以及真机现场验收**尚未完成**，文档中已如实标注。
- 收缩契约中一处自相矛盾的校验：原先先判"必须同时有 motors 和 cameras 之一"再逐字段要求四者齐全，前面的判断是死代码；现在只保留一条规则（舵机、相机、夹爪行程、安全上限必须齐全）。

- 新增整机标定契约 `robot.calibration.v1`（`robot/gateway/tangying_robot_gateway/calibration.py`）：一台参考机器人 = **两个 RGB-D + 两条机械臂 + 每条臂一个夹爪**，共 16 个舵机（每臂 6 个关节含夹爪，两条臂各占一条总线所以左右舵机 ID 都是 1–6）+ 头部 2 + 底盘 2。文档同时描述舵机（沿用 LeRobot `MotorCalibration` 字段，现有 `validate_calibration_data` 仍是舵机字段权威）、相机内参/畸变/外参、夹爪行程与安全上限；严格拒绝未知字段，避免真机上"拼错字段等于什么都没做"。
- 标定身份是**内容哈希**（不含时间戳）：同样数字存两次修订号不变，证据因此能声明观测是在哪份标定下采集的；保存支持 compare-and-swap，`REVISION_CONFLICT` 防止并发编辑静默覆盖别人的测量值；写入为原子替换。
- 仿真不是特例：`sim/mujoco/tangying_sim/calibration.py` 从 MuJoCo 模型推导出同一份文档（相机内参由 `cam_fovy` 与画幅算出，外参由相机世界位姿折算到父连杆），运行时**真的使用**它——每次 RGB-D 采集的 `K` 与 `camera→world` 来自标定文档，改一个数字就能在观测里看到后果。`robot_state` 新增 `calibration_revision` 与 `calibration_source`，与 `model_revision`/`scene_revision` 并列。新增 `--calibration-dir`（空则只推导不落盘）。
- 修正推导过程中的一处坐标约定错误：外参在推导时已折算为光学坐标（右/下/前），解析时又折算了第二次，导致每个采集旋转 180°、感知看不到任何实体；现在推导与渲染走同一组世界位姿量，两者逐元素一致（位置 1e-18、旋转 1e-16）。
- 新增 `docs/development/robot-calibration.md` 与 11 项契约测试。**尚未实现**：面向非技术用户的引导式标定向导、承载它的运行时 RPC 与控制台面板；真机标定未做现场验收。

- 产品聚焦到**单机器人四房间家庭场景**：README 从"多条平行路线"改为**一条家居自然语言任务闭环**（观察 → 导航 → 到达确认 → 重新观察 → 解析目标 → 规划抓取 → 拿取 → 抓取确认 → 放置 → 放置确认 → 返回 → 到达确认），把 `make home-start` / `make home-accept` 作为唯一入口；Fleet 多机器人、RoboCasa 双机与固定工位下沉到"其他路线（暂不聚焦）"，代码与测试保留。`docs/README.md` 同步改为家居闭环主线，并新增"其他路线"小节。
- 新增 Makefile 入口：`home-start` / `home-restart`（`--scene home_task`，含厨房红色杯子）、`home-accept`（命令行复现同一任务并保存每步观测）、`home-routes`（`--scene home` 纯路线）。
- 端到端验证家居闭环：`make home-accept` 通过，12 步全部有证据（4 个物理工具步骤、13 份历史观测、26 张校验图像），最终 `red-cup` 稳定处于 `inside:kitchen-bin`（3 帧、位移 < 3e-8 m）；家庭运行时 11 个工具中 10 个被这条链路覆盖（安全工具按设计不在顺利路径上）。
- 验证并记录真实 SLAM 路径：`navigation-stack.sh restart --build --mode mapping --scene home` 构建成功，`ready=true`、`mapRevision=e9726707…`、`poseSource=rtabmap_tf`；客厅导航与 `verify_arrival` 成功，客厅→厨房被 Nav2 以 `allow_unknown=false` 拒绝规划——与文档既有说明一致，属安全门禁而非回归。
- 记录建图阶段的真实缺口（`docs/guides/home-scene-operations.md`）：工具层已注册 `explore_for` / `scan_environment`，但家庭运行时不提供对应执行技能，导航桥也没有速度接口，因此**五个房间的覆盖目前需要人工受控驱动**，尚无自动探索入口；补齐它是通往实机的前置工作。
- `tests/test_repository.py` 的 README 定位断言从"以云端产品开头"改为"以单机器人家庭闭环开头，且 Fleet 内容不得出现在开头"——产品决策变更后，测试改为守住新决策并禁止旧定位回流。

- 精简发布树：删除 `.superpowers/`（8 份 agent 会话任务报告，全仓零引用，`.dockerignore` 早已把它排除出镜像）、`artifacts/promotion/`（20 份小红书推广素材，零引用）与 `artifacts/replay-verification/`（2 份无引用产物），并同步移除 `.dockerignore` 里已失效的排除项。签名验收证据（`artifacts/robocasa-harness/`、`artifacts/closure-verification/`）与数字孪生 3D 资产（`web/assets/scenes/`，代码与前端测试直接依赖）保留。
- `docs/superpowers/` 只保留 `specs/`（14 份设计决策记录），删除 `plans/`（15 份按日期的实施清单）。README、架构文档、文档索引与 `docs/distributed-agentos.md` 的链接同步改为只指向决策记录；两份契约测试改为断言决策记录存在**且**发布树里不再出现计划链接——文档链接检查扫描不到该归档，只有这条断言能防止死链回归。

- 分支管理整理：新增默认分支 `main`，指向最新且完整可发布的状态（原默认分支是 8 月 25 日的 `codex/v0.1`，比主线落后 15 个提交，这是"看不出哪个分支是最新"的根因）。发布一律用 `vX.Y.Z` 附注标签标记，不再为每个版本保留长期分支；已删除 11 条远程分支——8 条内容已完全包含在 `main`（提交仍可从 `main` 到达，发布身份由标签保留），3 条早于 v0.2 的分歧分支（`codex/v0.1`、`codex/live-task-feedback`、`codex/sim-motion-fix`，其独有文件经核对为 v0.3.0 已删除的死代码或被 main 更新证据取代的旧产物）。同时开启"合并后自动删除头分支"。
- 新增[分支与发布规范](docs/development/branching.md)：唯一的长期分支、发布标签规则、功能分支前缀与生命周期、判断分歧分支能否删除的命令，并从 README 与文档索引链接。

- 修复 CI 在 `make test` 上必然失败且不给原因的问题。根因是 `sim/mujoco/tests/test_home_scene.py` 的一次 RGB-D 采集在 CI 的软件渲染（`MUJOCO_GL=osmesa`）下卡在 `mjr_render` 里：`SceneRenderer` 用 `future.result()` **无限等待**渲染线程，而 `timeout_method = "thread"` 会转储全部线程并杀掉整个 pytest 进程——于是 CI 只报告“make test 失败”，既不指出失败的测试，也不打印摘要。现在渲染与关闭都有上限（`TANGYING_RENDER_TIMEOUT_S`，默认 60 秒），超时后渲染器标记为不可用并立刻报错而不是排队继续等；`timeout_method` 改为 `signal`，被挂起的测试单独失败并保留完整摘要。
- 修复文档链接指向被 gitignore 的产物导致 CI 必失败的问题：`docs/development/rtabmap-navigation.md` 链接了 `artifacts/acceptance/…/workcell-v2-commissioning.json`，而 `.gitignore` 排除了 `artifacts/acceptance/`，该文件只存在于本机工作区。现在文档说明该文件不随 Git 分发，并在**纯净检出**中验证链接检查通过。
- e2e 就绪预算改为可配置且更符合 CI：`TANGYING_E2E_STARTUP_TIMEOUT_S`（默认 90，原写死 20）与 `TANGYING_E2E_LIFECYCLE_TIMEOUT_S`（默认 120，原写死 35）。本地在负载下复现过同一条 `startup telemetry unavailable` 失败，是同一个预算过紧的问题；预算约束的是坏掉的栈，不是慢的栈。
- CI `test` 作业上限从 20 分钟提高到 45 分钟：软件渲染下完整套件的耗时接近或超过原上限，原上限会在报告任何结果前杀掉作业。
- 新增 `tests/e2e/test_readiness_budgets.py`（预算下限与环境变量覆盖）与渲染器卡死回归测试（超时后立即报错、后续请求快速失败、关闭不被拖住）。

- 按部署目标整理仓库：`deploy/` 现在只有三个目标目录——`cloud/`（Fleet 控制面 Compose）、`robot/`（树莓派 systemd 单元与 udev 规则、`navigation/` 导航容器栈）、`local/`（开发机 Local Agent 单元与环境模板）。原来的 `deploy/raspberry-pi`、`deploy/laptop`、`deploy/navigation` 与一个不表明目标的 `deploy/config/` 合并进对应目标，样例外配置跟着使用它的目标走；新增 [`deploy/README.md`](deploy/README.md) 说明每个文件装到哪台机器。
- 新增[部署目标与代码归属](docs/operations/deployment.md)：云端、机器人端、本地单机三个目标的进程、端口、源码目录、部署文件与启动命令，逐条列出；并说明为什么 Go/Python 包路径不按目标搬动（模块路径是内部接口，`tests/architecture` 按前缀校验依赖方向）。
- 新增一键启动 `scripts/start-all.sh` 与 `make up` / `make down` / `make stack-status` / `make stack-logs`：默认只起仿真与本地控制台（无需 Docker、无需硬件），`--with-cloud`、`--with-navigation`、`--with-fleet-sim`、`--demo` 按需加入云端、导航与双机演示。脚本只按顺序调用各目标已有的生命周期脚本并汇总健康状态，`down` 只停 `up` 记录过的组件；新增 `check` 只校验前置条件、不启动任何进程。
- 新增 `tests/install/test_start_all.py`：校验 `deploy/` 目标目录与文档一致、顶层目录都被分类、导航 Compose 的构建上下文在移动后仍指向仓库根目录（少一层就会解析到 `deploy/` 并导致镜像构建找不到 ROS 工作区）、一键脚本只做编排而不重复实现生命周期，以及 `check`/`down` 的行为。
- 导航栈移动到 `deploy/robot/navigation/` 后同步修正 Compose 的 `context: ../../..`、Dockerfile 的 `COPY` 路径与 `.dockerignore` 规则；安装脚本、导航脚本、Sim2Real 校验与相关文档同步更新。

- 修复“动作与结果”“任务步骤”内容全部挤在左侧、右侧大片留白的问题：任务进展面板本来就横跨整行，但每条记录仍是单列文本。现在每条记录分两列——左边是执行了什么（步骤标题、说明、目标、参数），右边是它的证据与「回看当时观测」按钮，按钮因此在面板里对齐成一列，便于逐条扫描；宽度不足 860px 时自动折回上下排列。
- 修复点击「回看当时观测」瞬间跳到页面底部的问题：处理函数在平滑滚动之后又调用了一次不带 `preventScroll` 的 `focus()`，浏览器会立即滚动到焦点元素，直接取消了动画。现在先以 `preventScroll` 聚焦、再平滑滚动，并在落点面板上短暂高亮，长距离滚动结束时知道停在哪里。回放面板恰好在滚动途中重绘时会把目标推走，因此滚动停下后会再校正一次位置。
- 工作台视觉与交互刷新：把 `web/styles.css` 顶部整理成设计令牌（墨色层级、三级描边、下沉表面、强调色组、抬升阴影、圆角、动效时长与缓动），组件改用令牌而不是各自写色值；面板与卡片改为细描边加低对比阴影，统计块从灰块棋盘改为「标签 + 数值」的分层卡片，任务记录行改为带可复制编号徽章的卡片，回放步骤与证据卡补齐状态色与悬停反馈。
- 修复路由切换后停留在上一页滚动位置的问题：切换入口回到顶部，重新进入当前页面不滚动；受减少动画设置约束。
- 修复路由切换时页面标题出现整块焦点方框的问题（`tabindex="-1"` 的标题不再显示焦点环，交互控件保留 `:focus-visible` 描边）。
- 手机端：任务编号不再在状态徽章旁折断，改为整行显示；底部栏的“开发模式”不再折行。
- 新增两份回归测试：路由切换的滚动行为（含重新进入当前页面不滚动）与「回看当时观测」的平滑滚动与落点标记。

## v0.5.0 - 2026-09-11

- 把“任务全过程回放”补成任何时候都能完整复盘；详见[发布记录](docs/releases/v0.5.0.md)。
- 补齐“任何时候都能复盘”的入口：任务记录此前只列出最近 50 条且不显示编号，更早的任务（例如需要追查的历史失败）在界面上完全无法到达。现在每条记录都显示可复制的任务编号；新增“按任务编号回放”输入框，可直接打开列表窗口之外的任意任务，编号不存在时明确提示“找不到任务编号”以区别于“服务暂时无法读取”；新增“只看”筛选（全部／失败或中断／已取消／成功／进行中）；超出 50 条由“显示更多”继续展开。回放标题也列出任务编号，便于把截图对回日志。
- 回放的空缺处改为说明原因而不是留白：执行链路为空时按任务状态区分“任务尚未开始执行”“在调用任何工具之前被取消”“失败发生在任务分解或目标绑定阶段”等结论（此前对等待批准的任务也会报成分解失败，把排查引向不存在的缺陷）；没有任务说明记录时说明是记录缺失，不再显示 `—`。
- 终态任务的说明记录返回 404 后不再每轮轮询重复请求同一个不会成功的地址；点“重新读取”仍可强制重新请求。
- 修复浏览器验收脚本的三处误判：单击滚动到底部不会触发视口之外的 `loading="lazy"` 缩略图，脚本会因健康图片超时而失败（改为逐步滚动再等待）；用 `REPLAY_TASK` 指定任务时按列表可见文本查找，窗口之外的任务永远选不中（改为通过编号查找框打开，并断言回放标题就是该编号）；把“必须有工具步骤”当作通用契约，导致分解阶段就失败、确实没有任何工具调用的任务被判失败（改为要求面板解释这种空缺，需要强制断言时用 `REPLAY_EXPECT_STEPS=1`）。
- 修复 `page.waitForFunction(fn, { timeout })` 的误用：第二个参数是传给页面函数的实参而不是选项对象，两处等待因此一直使用 30 秒默认值，超时配置从未生效。

## v0.4.0 - 2026-09-11

- 新增标准机器人工具层（27 个工具，25 个默认提供给 LLM）与工作台“任务全过程回放”；详见[发布记录](docs/releases/v0.4.0.md)与[工具层 ADR](docs/superpowers/specs/2026-09-11-standard-robot-tool-layer-adr.md)。

- 新增工作台“任务全过程回放”：把一次自然语言任务的事件、任务说明、历史观测与恢复状态对齐成一份可读档案。按发生顺序列出每个工具步骤（中文名、原始工具名、状态、耗时、派发次数、是否改变世界、调用参数、命令编号），在写步骤下直接给出确认它的那次采集（同帧彩色与深度、采集时间、相对命令的时间偏移、来源、采集编号、字节数与 RGB SHA-256）。面板同时做一致性检查并列出不一致项：写工具无证据、证据早于命令、引用的采集不在历史中、采集登记的步骤与引用步骤不符、成功但写步骤未确认、任务结束却无任何工具活动、存在未知终态需要现场核对。
- 回放实现分为纯逻辑（`web/task_trace.js` 的 `buildTaskTrace` 对齐与判定）与渲染（DOM 构造 `renderTaskTraceNodes`、供测试断言的 `renderTaskTrace`），按显示内容变化才重建，任务运行时每 250 ms 节流更新、进入终态立即更新；只读，不创建、批准或取消任务。
- 新增 `scripts/check-task-replay.cjs` 浏览器验收：断言面板可见、模块已发布、步骤与事件已列出、每张证据缩略图真实解码、页面无 JS 错误。
- 把文档站点的内部链接检查从 `docs/production` 扩展到全部现行文档（历史归档 `docs/superpowers` 除外），立即发现并修正了一处指向不存在页面的链接。
- 修复 `test_web` 覆盖不到的接线缺陷：新脚本未加入 `web/embed.go` 的 `//go:embed` 列表会被内嵌控制台漏掉；`task_trace.js` 曾含 ESM `export`，在严格 CSP 下作为 classic script 解析失败并静默禁用整个模块。

## v0.3.0 - 2026-09-11

- 新增 `core/closedloop`：`Track` 状态机（派发 → 成功待证据 → 已验证 / 重试 / 升级）、失败分类（瞬时、感知、规划、权限、资源、参数、未知终态、致命）、有界重试与确定性退避，以及 `Gate` 完成门禁。未知物理终态永不自动重试，只能进入对账。
- 新增 `mutates_world` 契约字段，贯穿 `core/skills`、`skills/manipulation`、`robot/gateway`、`robot.proto`、MuJoCo 运行时与 Go 客户端。写工具声明与只读工具分离，`emergency_stop` 作为物理但非场景写的例外有显式说明。
- Go Agent 在通用执行路径上强制闭环：任何写工具返回成功后，必须附上命令派发之后采集、带观测标识的新鲜证据，否则该步骤保持 `STARTED`、任务进入可恢复失败，并拒绝以成功文案收尾。此前的后置验证只覆盖参考清单中的三个 `verify_*` 步骤，适配器新声明的写工具可以只凭返回码记为完成。
- 时间新鲜度按运行时实际精度判定：派发时刻截断到毫秒，证据与派发落在同一毫秒视为命令后，前一个毫秒仍被拒绝；判据由 `DispatchPrecision` 单点定义，`Gate` 与 `Track` 共用。
- 新增回归测试：缺证据的写必须失败关闭且步骤留在 `STARTED`、只读工具不受门禁影响、adapter 通过能力声明新增的写工具同样受约束、未知终态不可重试、退避与重试上限可断言。`sim/mujoco/tests/test_world_mutation_contract.py` 校验运行时能力声明与规范工具集一致。
- 修复相机路径从未发布 `verification_confidence`：判定写在服务实例上，而对外状态来自世界对象，控制台与证据读取端在相机路径上一直看到 0.0，确定性路径正常；现在写入 world 并在场景捕获时发布。
- 旧真值调试运行时不为观测提供标识，因此其写工具稳定失败关闭。`scripts/demo.sh` 与 `tests/e2e` 的物理任务改在相机工作台（`--perception rgbd --scene tabletop`）运行，`scripts/demo.sh` 同时显式使用 `simulation` 安全 profile。真值模式保留只读调试用途。
- 清理无引用实现：删除 `core/toolcatalog` 与 `controlplane`（全仓无生产引用，仅历史计划文档提到）；修正 `docs/distributed-agentos.md` 中指向已删除包的边界描述。浏览器回归输出 `artifacts/ui-v1/` 改为忽略目录并在文档中说明重新采集方式。
- 决策记录见[闭环与语义升级 ADR](docs/superpowers/specs/2026-09-10-closed-loop-semantic-upgrade-adr.md)，其中说明语义地图与主动感知搜索为何不在本版实现，以及后续接入点。

## v0.2.0 - 2026-09-09

- 新增四房间家庭 MuJoCo 场景（客厅、走廊、厨房、卧室、卫生间），家庭路线自然语言解析、逐房间导航检查点和基于底盘 RGB-D/位姿的 `verify_arrival` 证据；`--scene home` 已贯通仿真、Compose、RTAB-Map/Nav2 launch，并使用独立家庭地图路径。家庭路线与实机放行边界见[家庭场景指南](docs/guides/home-scene-operations.md)。
- 降低双机器人 Fleet 调试预览的重复阴影绘制成本：保留主光源投影和全部照明，补光不再重复投影复杂网格。图像尺寸、原始观测时间及 1 秒新鲜度门槛保持不变；单机器人 RGB-D 和共享模型资源不受该工厂配置影响。
- 移动仿真初始化在操作位后方约 65 厘米；通过双机载 RGB-D、RTAB-Map / Nav2 实际接近桌面，再重新观测、抓取、放置和确认环境变化。固定工位入口继续直接就位。
- 导航工具预算为 60 秒，抓取／放置仍各 15 秒；在实际派发时生成受调用方截止时间约束的有界 deadline，长导航和安全暂停不会耗掉后续步骤的预算。未知物理结果仍禁止自动重放。
- 导航桥持久保存失效当时的就绪阻断项、输入年龄和观测时间，故障恢复后不以当前健康状态覆盖历史原因。
- 区分 Nav2 实际移动到达与当前位置确认。第二个物体任务在新鲜 RTAB-Map 定位和独立里程计均满足 15 毫米／0.04 弧度时继续；保存独立目标身份、完成来源和原始定位时间，不重复发起底盘运动或借用旧回执。
- 统一 ROS 通信实现配置，提供 Cyclone DDS 与显式 Fast DDS 回退；保持相机、定位和速度的新鲜度限制。
- 为参考工位加入实际渲染的非重复地面纹理，解决收臂后纯色地面无法建立视觉签名的问题；增加近目标连续距离评分并细化角速度采样，解决同一栅格内缺少距离梯度及末端转向量化导致的停滞。保留 25 毫米感知地图、未知区域拒绝、完整足迹检查和 1.5 秒碰撞预测。
- 修复 MuJoCo 3.12 枚举与 NumPy 关节类型比较不对称导致严格自身过滤失败的问题；生产依赖锁定已验收的 3.11.0，并增加 3.12 兼容检查。
- 固定 gRPC、Protobuf、NumPy 和 Pydantic 的发布基线，避免重新安装时的依赖漂移；生成协议代码必须与仓库一致。
- 修复 Robot Edge 的 LeRobot／Protobuf 依赖冲突，使用共同的 Protobuf 6 基线并保持协议描述不变；显式安装 Feetech SDK，固定兼容 NumPy 的 OpenCV，增加 Linux ARM64 真实依赖解析门禁。
- 新增 `run_navigation_acceptance.py`，自动保存完整任务、原始观测、图像哈希、实际位移和三帧放置验证；支持在导航工具边界暂停后继续。
- 统一源码、安装器、CLI 和内置 Runtime 的 `0.2.0` 版本身份；首次源码构建未生成安装回执时也可查询版本。
- 修复导航地图尚未读取时误报未就绪，以及 Local 子任务总进度与实际工具事件脱节；完成展示必须有对应的放置验证证据。
- 修复签名验收接收器在并发重复上传时提前关闭连接的问题：以有界流式读取处理仍在发送的重复请求，再返回冲突，不重复接收或改写已保留证据。
- `make setup` 按前端锁文件安装依赖，修复干净工作区缺少 Three.js 而无法运行前端测试的问题。
- 修复 Linux / macOS 前台会话关闭后的进程清理：同一进程已退出但尚未回收时清除旧记录，继续拒绝向身份不匹配的活进程发信号；覆盖 HUP、启动中断与清理期间的再次中断。
- 补齐 ROS CI 的独立协议依赖；真实相机几何测试先丢弃冷启动渲染，再重新采集，原有观测新鲜度检查保持不变。

## 未发布 · V1 集成候选

以下保留原“未发布”升级记录，相关源码改动纳入 v0.2.0；当时的测试数字与历史签名包不自动代表本次发布。历史 rc 记录保持原始版本，V1 范围与软件包版本分别管理。

- 修复固定工位入口可能启动错误世界：`make rgbd-start` / `rgbd-restart` 显式声明 `--scene tabletop`；直接调用 `scripts/sim-stack.sh` 而省略 `--scene` 时沿用记录场景会打印 `reusing recorded scene ...` 及切换命令，`status` 现在报告实际运行场景（运行时观测为准，未出帧时标注来自记录配置）。此前家庭路线后重开栈会让固定工位任务在绑定阶段失败而看不出原因。
- 改善绑定失败诊断：`grounding absent`（无匹配）与 `grounding ambiguous`（多个候选）分开报告，消息附带机器人、adapter、观测数量、可见实体列表和场景切换命令，现场从“场景不对”和“相机/识别不对”中直接分辨；绑定失败前不产生物理动作或证据。
- 自然语言接受指示代词终点：“右边那个盒子”“这个箱子”等说法解析为同一已配置容器，不改变容器集合；无方位的指示代词仍不做左右假设。
- 修复 `scripts/sim-stack.sh status` 只报告进程健康、不报告场景的问题，并补充对应回归测试与[任务找不到物体](docs/install/troubleshooting.md)排查步骤。

- 引入可选 RTAB-Map + Nav2 导航：双 RGB-D 原始流、采集时刻 TF/里程计、持久地图、定位状态、命令幂等与速度租约；导航前收臂，到位后重新观察，保留统一工具与 MCP 边界。
- 修复机器人自身污染地图：独立机器人 CAD 和同帧关节反馈逐像素匹配自身表面，只在导航输入中移除，不把遮挡当作空闲，不改用户原始画面。
- 修复不合理的动作确认与历史回看：归档工具实际验证帧，失败同样可回看；自由释放后检查不同帧中的支撑与位置稳定；抓取 IK 禁止隐式移动底盘。
- 统一彩色图、深度图、点云的固定显示区域；显示实际成功呈现帧的 FPS 与采集年龄，预解码后替换画面，非工作台页面暂停相机预览刷新，保留任务和安全更新。

- 单机器人 V1 新增机载 RGB-D 反投影与参考工位识别，动作后重新观察确认抓取/放置；彩色、深度和局部点云不使用全知场景补全。旧真值模拟仍显式保留为开发调试。
- 修复点云抽样漏掉桌面小物体及统一单色难辨认的问题：XYZ 与同像素 RGB 对齐，物体掩码保留实测采样、参考工位使用 4096 点；优化点大小、工作区取景与物品标签。逐点颜色贯穿 Python/Go 合同、历史保存及前端，旧无色记录继续兼容，畸形颜色和同帧改色会被拒绝。
- 新增工具边界暂停、同任务重启后显式继续、已完成物理动作去重与未知结果阻断；只读步骤重新观测，拒绝通过更改版本或安全标签绕过核对。
- 保存任务/步骤关联的同帧 RGB、深度预览与规范重建，保留哈希和原始时间；工具完成状态关联成功保存的观测证据，用户可回看历史。
- 新增 ROS 2 RGB-D 消息桥，校验原始时间、对齐、深度单位、内参和采集时刻 TF；默认实机输入仅观察。本轮已在官方 Jazzy Linux 容器验证真实 gRPC→DDS 双相机、PointCloud2、TF/odom；实际 RTAB/Nav2 验收另行记录，不宣称客户实机控制或现场生产验收已通过。


- 新增异构机器人 SDK：版本化机械结构/传感器/动作能力清单、规范三维感知、可信本地插件和 CLI；不同关节名称与单位可通过同一 Runtime 接入，旧 XLeRobot 限制继续保留。
- 新增 Python/Go 双端感知验证及跨语言七步任务测试，保留原始来源/采集时刻，拒绝旧帧、错误坐标、缺失合同和设备配置漂移；World 不再将所有实体标为仿真真值。
- 新增官方 SDK 的 MCP stdio 桥接，统一提供设备/能力/观测/任务/停止工具，创建任务保持待审批；补充适配器开发与 MCP 接入文档。
- 加固通用 Runtime 的工具结果校验与不可变快照；物理执行后异常或非法结果持久急停，感知等待期间发生停止则不再进入动作 handler。MCP 支持显式私有 CA，保留证书和主机名检查。
- 修复三项 MuJoCo 交接回归：按当前几何/持有状态输出 inside/held_by 关系，保留严格起点校验。
- 修复历史签名验收包因前端升级无法重验：验证签名和完整哈希后使用历史资源清单，新候选及基线提升仍严格匹配当前源码。
- 修复本地演示只等待 HTTP 就绪而过早审批任务的问题：先验证 Runtime 与新鲜场景，恢复失败及时退出并清理自身进程；补齐世界测试就绪条件与渲染线程同步。

- 统一“躺营”品牌与明亮中文工作台，分离用户操作和开发诊断；保留三维、简洁、机器人画面、全局地图四种展示。
- 改进中英文自然语言解析：中文机器人编号、礼貌用语、常用别称、同句代词以及独立起点/终点/颜色绑定；完整已知指令优先确定性解析。
- 拒绝已识别的否定、条件及不完整理解，修复任务终点修改丢弃约束的问题；执行前验证指定起点与实际观测关系。
- 修复任务进展同版本不刷新、迟到响应覆盖和完成状态显示；开发预览统一模型/API 来源并保留 WebSocket 同源校验所需 Host。
- 新增可复现的 13 项自然语言仿真评测；记录反向搬运与完成后重新授权尚未实现的边界，不把定向交接当作通用家务能力。
- 完善 Sim2Real 私有接入包、阶段证据检查、锁定驱动兼容、显式使能与本地恢复；新增单主 World 检查点与部署持久卷支持。
- 对齐用户指南、源码地图、API/数据契约、开发预览、验证与实机交付说明。具体实现、证据日期及未完成事项见 [V1 当前状态](docs/production/v1-release-status.md)。

## v0.2.0-rc.2 - 2026-08-24

- 新增 VLA、模仿学习、强化学习和确定性仿真共用的 PolicyManifest/Observation/Inference/ActionChunk 契约，策略只在 Edge 运动前执行，原始动作不进入云端或用户界面。
- 新增 Go HTTP/确定性 Policy Provider、Python 框架无关 sidecar，以及 robot model、calibration、artifact、manifest revision 的 fail-closed 兼容性校验。
- 新增六类策略与执行恢复：观测等待、策略重试、策略阻断、执行对账、安全恢复和安全停止；未知物理终态绝不自动重放。
- 用户端现在以通俗语言展示自然语言理解、任务 revision、每台机器人、工具、控制方法、环境确认和恢复过程，同时保留折叠的专业证据。
- 中文双机器人方块传递已在真实 RoboCasa/MuJoCo、双 XLeRobot、云端 Fleet、双 Edge/Runtime 和受控浏览器中闭环通过；签名 `round4` 证据包含 23/23 项检查和四个唯一策略推理证据。
- 修复 GLTF 内嵌纹理被 CSP 拦截导致模型材质缺失，以及直接操作相机后内部跟随状态与工具栏显示不一致的问题。
- 扩充生产文档，覆盖策略工具对接、sim2real 晋级、异常排查、接口契约、安全边界和发布证据。

## v0.2.0-rc.1 - 2026-08-23

- 新增运行中任务更新：自然语言修改生成不可变 TaskRevision，经用户预览确认后在 Harness 安全点切换；旧 revision、旧命令和重复提交均失败关闭。
- 新增面向普通用户的任务体验轨道，以通俗语言显示系统理解、编号步骤、机器人、工具调用、环境证据、异常恢复和最终结果，专业字段默认折叠。
- 新增完整 RoboCasa WebGL 数字孪生：厨房、两台完整 XLeRobot、权威姿态/关节、路径、标签、资源 custody 与 Canvas 安全降级。
- 新增可移植、签名、单次上传的浏览器验收证据；本次中文双机器人交接和 revision 1→2 更新通过 22/22 项检查。
- 新增生产交付手册，覆盖系统架构、快速上手、全部接口、数据契约、配置安全、异常运维、仿真到实机、测试验收和部署容量。

- 云端成为联网机器人主要产品形态；Local Brain 保留为无网络部署，两者共用 `world.snapshot.v1`、工具目录、Observation Registry 与 Robot Runtime 契约。
- 新增单逻辑红色方块的双 MuJoCo 机器人交接：交接区世界证据、单调资源 fencing、`BLOCK_AVAILABLE/BLOCK_DELIVERED` 领域事件、1 个真实进程断连恢复与 8 个确定性故障边界。
- 新增权威实时 WorldHub 与交互式 3D Console：一次性 WebSocket 票据、游标重放/缺口重同步、左键平移、右键旋转、指针锚定缩放和来源新鲜度/资源归属展示。
- 实机 XLeRobot Runtime 现在在运动前验证 robot/catalog/world/resource/fencing 身份；缺少环境观测或放置验证器时 manipulation fail-closed。

- 新增 Fleet 云端一键 Docker 部署：mysql + redis + fleet-control-plane + nginx（HTTPS 控制台 + 8444 mTLS gRPC 透传），8080 仅 expose 于 Docker 内网、不发布到宿主机。
- 新增 edge-worker：从 Redis Stream 直连或 HTTP 长轮询拉取任务，连接 Robot Runtime，上报状态/事件/遥测。
- 新增机器人公网接入 mTLS gRPC 网关（FleetGateway）：Register/Link 双向流、断线指数退避重连、心跳与设备租约（过期自动离线）、服务器命令下行（急停/取消）。
- 新增云端协调器：意图级多机器人任务图、事件驱动跨机器人刷新与重新投递、意图声明租约超时自动回收。
- 新增多机器人全局地图融合（占用栅格 + 轨迹 + 实体）与云端 Console（登录/设备/任务/遥测/地图）。
- 新增双 MuJoCo 仿真场景：`--robot-id`/`--xml` 参数与世界偏移 r2 场景生成脚本（`scripts/gen_scene_variant.py`）。
- 新增认证边界：操作员 Bearer token（HMAC，24h）、每机器人独立且绑定 `X-Robot-ID` 的设备凭据（`fleet/auth`）、nginx 客户端 IP 白名单（`FLEET_ALLOWED_CIDRS`）。
- 新增实时上帝视角（God View）：Robot Runtime 场景帧经 edge-worker 500ms 周期上行（HTTP base64 / mTLS gRPC 原始字节），`/v1/scene/frames` 提供双机实时画面；`/v1/maps/global` 扩展 held/placements 语义；`/v1/world` 输出机器可读世界状态供 harness agent 编排。
- 仿真可观测性：`--human-speed` 墙钟节流让技能动画以可观看速度执行（默认 0 不影响验收）；世界锁内滚动快照（~25Hz）使观察无阻塞，执行期间画面/物体/held 持续可见。
- Added pure distributed AgentOS brain/isolation design: controlplane.Brain, edge/runtime.Router, per-step RobotID, and event-driven GraphRuntime node refresh while keeping robot runtime unaware of command origin.
- Added Alibaba Cloud Fleet control plane: Go HTTP API, MySQL task repository, Redis cache/stream queue, Docker Compose one-click deployment and deploy-alicloud.sh.

- Added pure distributed AgentOS brain/isolation design: , , per-step , and event-driven  node refresh while keeping robot runtime unaware of command origin.

- Added explicit Agent / Robot Runtime / Middleware / ROS 2 / real-time / hardware boundaries, with executable dependency tests that prevent concrete infrastructure and transport SDKs from entering Agent core packages.
- Added vendor-neutral Middleware ports for task/execution state, bounded queues, events, cache, locks and traces; moved the default WAL SQLite store under `middleware/sqlite` and injected the in-memory queue at the composition root.
- Replaced task-graph-aware robot execution with semantic Runtime commands and capability names; protobuf/gRPC mapping now stays in `edge/robotclient` and the Python Runtime service boundary.
- Decoupled Safety, XLeRobot direct, and ROS 2 backends from generated protobuf types using transport-neutral Runtime models, while keeping high-rate camera/LiDAR/IMU/joint data robot-local.
- Preserved the layered architecture specification, implementation plan, and middleware adapter guide as versioned development design assets; PostgreSQL, Redis, and Kafka remain optional future adapters rather than default dependencies.
- Replaced the hosted control plane with one laptop Local Agent process serving Console/API, LLM orchestration, task execution and SQLite persistence; removed the cloud binary, PostgreSQL store, Compose stack and cloud installer role.
- Simplified laptop-to-Raspberry-Pi operation to direct mTLS gRPC initiated by the laptop, while the Pi keeps only the bounded command/E-stop safety journal.
- Preserved the approved local-first architecture specification and delivery plan as durable design assets linked from the current architecture documentation.
- Added LLM self-orchestration: the planner chooses and orders skills from the registered catalog, with deterministic fallback, self-consistency voting and `/v1/orchestration/metrics` quality scoring.
- Added a user-facing Robot Agent Console: natural-language task creation, live task/audit views, Robot Runtime and sensor/semantic telemetry, a MuJoCo top-down scene renderer and orchestration metrics.
- Added a Local Agent telemetry bridge (`POST /v1/telemetry` / `GET /v1/telemetry`) so simulation and future real XLeRobot sensor state are observable in the same console.
- Added an explicit XLeRobot production go/no-go gate (`robot-agent production-check robot-pi`) requiring providers and recorded 30-trial safety evidence.
- Added a pluggable task Agent: deterministic parser plus optional OpenAI-compatible function calling with deterministic fallback.
- Added the `fetch` tool ("把红色杯子拿过来") and a front `delivery_tray` in MuJoCo for closed-loop fetch simulation.
- Added compound one-sentence task sequences ("先放 A，再把 B 拿过来") with deterministic parsing, multi-tool OpenAI planning, per-subtask skill graphs and resumable execution.
- Expanded MuJoCo to all advertised objects (red/blue/green cups, bottles and blocks), both storage bins and the delivery tray, plus an 18-goal object/destination acceptance matrix.
- Added `make sim2real-check`, `make deploy-robot-pi` and `scripts/robot-pi-quick-deploy.sh` for repeatable simulation acceptance and fast Raspberry Pi direct-edge installation.
- Added a ROS2-free `XLeRobotDirectBackend` and `tangying_robot_gateway.run_direct_edge` so the first real XLeRobot release no longer requires ROS2.
- Added explicit entity, policy and verifier provider hooks that fail closed until real perception and policy are installed.
- Added a ROS2-free Raspberry Pi systemd template and the V1 Agent / Sim2Real contract document.
- Added the `edge/runtime` Robot Runtime boundary: structured `CapabilityInfo`, runtime availability checks before every task, per-command deadline enforcement, cancel and emergency-stop client methods.
- Added low-rate `SemanticState` to observations so Agent code sees activity and safety status instead of raw sensor streams.
- Hardened `SafetySupervisor` with command identity checks, lease bounds, bounded action-chunk key/value validation and controlled cancellation that remains distinct from the E-stop latch.
- Added structured capability descriptors to MuJoCo, XLeRobot direct and ROS 2 backends; ROS 2 read-only skills now stay on the gateway side of the ROS boundary.
- Hardened XLeRobot for physical experiments: configurable `max_relative_target` / action-chunk length, thread-safe fail-closed driver, local stop latch, provider exception mapping, graceful service shutdown, and a no-motion XLeRobot preflight.
- Added the XLeRobot experiment runbook for first physical motion, E-stop drills, provider contracts and post-stop service restart.

## v0.1.0-rc.2 - 2026-08-17

- Added one role-based installer for simulation, cloud, laptop Local Agent, and Raspberry Pi Robot Edge.
- Added the `robot-agent` lifecycle, configuration, diagnosis, pairing, and simulation-demo CLI.
- Added a bounded full-process MuJoCo demo and loopback-safe cloud defaults.
- Added laptop-to-Pi P-256 mTLS pairing with local-only CA custody and explicit trust-root rotation.
- Replaced the prototype Raspberry Pi units with hardened XLeRobot and Robot Edge services, localhost ROS discovery, stable dialout udev aliases, and no-motion preflight.
- Pinned XLeRobot and LeRobot integration, added an explicit interactive calibration tool, and fail closed without the exact calibration file or policy action chunks.
- Added endpoint runbooks for fresh installation, startup, upgrades, recovery, and the no-STM32 XLeRobot topology.

This release candidate has automated simulation evidence only. Stable `v0.1.0` still requires the physical emergency stop, network interruption checks, local perception/policy integration, and 30 hardware trials in `docs/safety-checklist.md`.

## v0.1.0-rc.1 - 2026-08-17

- Extracted a reusable distributed Agent Core from the Tangying video production architecture.
- Added natural-language tabletop pick-and-place intent parsing and manipulation skill graphs.
- Added cloud orchestration, Local Agent execution, Robot Gateway contracts, and operator controls.
- Added MuJoCo simulation, Raspberry Pi ROS 2 packages, Safety Supervisor, and XLeRobot adapter.
- Added contract, restart, safety, simulator, API, and full-process end-to-end tests.

This release candidate has automated simulation evidence only. Stable `v0.1.0` requires the physical emergency stop, network interruption checks, and 30 hardware trials described in `docs/safety-checklist.md`.
