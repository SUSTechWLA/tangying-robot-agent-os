# 躺营自然语言 Agent 评测与改进

2026-09-05，在当前主工作区编译的 Fleet、Edge 和真实 RoboCasa Runtime 进程上完成 13 项检查，全部符合预期。5 条正向指令完成仿真动作，6 条不支持或含糊的指令在创建时被拒绝，2 条场景条件不成立的指令在执行前失败且物体未移动。

这里的“13/13”是**理解、执行或拒绝符合预期**，不是 13 次成功搬运，也不是开放域语言理解准确率。使用确定性语义动作与仿真世界，没有调用线上大模型、训练新权重或连接实机；不构成真实抓取成功率、生产放行或实机性能证明。

## 实测结果

| 用例 | 自然语言输入 | 修复前 | 修复后 |
| --- | --- | --- | --- |
| canonical | 让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区 | 完成 | 完成，1.05 秒 |
| polite | 请帮我让一号机器人把红色方块放到交接区。 | 422，未理解 | 完成，0.53 秒 |
| colloquial | 麻烦 1 号机器人将红色积木移到交接点。 | 422，未理解 | 完成，0.53 秒 |
| pronoun | 先让一号机器人把红色方块放到交接区，然后让二号机器人把它放到右侧目标区。 | 422，未理解 | 双机交接完成，1.04 秒 |
| english | Robot 1, please move the red block to the handoff zone. | 422，未理解 | 完成，0.53 秒 |
| negation_zh | 把红色方块放到交接区是不允许的 | 错误生成肯定动作 | 422，要求澄清 |
| negation_en | Do not put the red block into the right bin | 错误生成肯定动作 | 422，要求澄清 |
| conditional | If the person leaves, put the red block into the right bin | 条件被忽略 | 422，不创建无条件动作 |
| multiple_objects | 把红色和蓝色方块放到交接区 | 随意选择颜色 | 422，要求每一步明确一个物体 |
| unknown_object | 让1号机器人把蓝色方块放到交接区 | 场景不存在蓝方块，执行前失败 | 仍在执行前失败，红方块未移动 |
| unknown_destination | 让1号机器人把红色方块放进冰箱 | 冰箱被误认成收纳箱 | 422，说明暂不支持家电操作 |
| unsupported | 帮我做晚饭 | 422 | 422 |
| source_mismatch | 让1号机器人把红色方块从右侧目标区放到交接区 | 本轮新增；旧绑定器未检查起点，回归测试已复现 | 执行前失败，红方块保持在左侧起点 |

对相同的前 12 项用例，修复前为 3/12，修复后为 12/12。基线中所有错误解析均在批准之前取消，没有执行否定或歧义任务。耗时是批准到读取终态的本次观测，排除编译、场景重启和解析时间；样本量不足以报告延迟分位数。

每条可执行用例先重启本评测拥有的 Runtime 与两个 Edge，等待新观测和新的世界版本，确认红方块回到左侧起点。成功同时检查任务 `SUCCEEDED`、目标区域关系、观测 `FRESH`、世界版本推进、意图数量，以及每个意图的 Harness `SATISFIED` 和非空证据 ID。

## 已修复的问题

- **完整解析、准确绑定。** `agent/intent/parser.go` 对动作、物体、起点和终点分别解析；中文机器人编号、礼貌用语、常用别称、中英文与同句后续代词均有回归测试。物体颜色不再被终点颜色覆盖。
- **不丢弃约束。** 已识别的否定、条件、停止表达，以及只理解了部分步骤的请求，返回可澄清的错误。`agent/agent.go` 优先保留完整确定性结果，阻止可选模型把明确的双机任务改写成其他动作。
- **检查起点。** `edge/robotclient/client.go` 要求起点唯一且物体观测关系相符；位置错误、未知、仍被持有、起点缺失或多义都失败。取到首次观测后取消订阅，避免遗留观测流。
- **任务更新也保留约束。** `tasks/service.go` 使用完整、有限的终点修改语法。“不要放到右侧目标区”“最后放到右侧目标区，然后关闭电源”等不会再被截取成肯定更新，也不会写入新版本。
- **执行反馈持续刷新。** HTTP Experience 没有事件游标时，同一版本仍需刷新步骤；前端现在接收完整快照，并丢弃先前并发请求的迟到响应。带游标的数据仍检查顺序。任务完成后，步骤与主状态统一显示完成，不再因版本仍为 `ACTIVE` 而显示正在执行。
- **预览实时连接。** 预览转发 WebSocket 保留浏览器访问的 Host，使后端可以正确验证同源 Origin；仍拒绝外部 Origin。修复原来 API 能读到新观测、场景推送却被拒绝的问题。

可选模型用受控 HTTP 响应测试验证“不重写已知指令”和“不绕过否定/条件”。没有评测任何线上模型的质量或延迟。运行中合法修改“最后放到右侧蓝色垫子上”的独立 RoboCasa 测试通过，保留发送方证据，等待安全点后更新接收方任务。

## 浏览器补测与未解决的边界

通过当前工作台额外执行了同句代词双机任务（`task-90a723fb2e3efa34255fdd16`），结果成功。反馈修复后，在页面确认两步均显示“已完成 / 环境已经确认这一步完成”，三维场景保持实时同步。

随后补测“请让二号机器人把红色方块从右侧目标区放到交接区。”（`task-00029f5a7f8c2aad7850b4ab`）失败，Runtime 返回 `FENCING_TOKEN_STALE`，未抓取物体。源码确认此场景每回合的放置方向限定为 `robot-1 → handoff-zone`、`robot-2 → right-target-zone`；完成后 owner 为 environment，缺少新一轮通用授权流程。**这条额外探索用例没有算入上面的 13/13，也尚未被实现为可执行能力。**证据另存 `final/browser-probes.json`。

这意味着当前评测通过的是单回合定向交接，不支持在同一完成回合上任意往返搬运。后续应将场景方向与资源授权约束公开到能力预检，明确提示用户为何不能开始，并实现经过验证的回合重置或重新授权；不能通过放宽 fencing 校验获得表面成功。当前需要重新启动评测场景才能从初始状态再跑一次完整交接。

## 复现

先按[开发快速上手](getting-started.md)安装主 `.venv`、Go 和 RoboCasa 隔离环境。脚本使用随机本机端口，不接管现有 Docker 或实机服务；每次使用一个不存在的输出目录。

```bash
ROBOCASA_PYTHON=/absolute/path/to/tangying-robocasa/bin/python \
  .venv/bin/python scripts/evaluate_natural_language.py \
  --output artifacts/natural-language-eval/my-run

# 只复测某些用例
ROBOCASA_PYTHON=/absolute/path/to/tangying-robocasa/bin/python \
  .venv/bin/python scripts/evaluate_natural_language.py \
  --output artifacts/natural-language-eval/my-focused-run \
  --case pronoun --case source_mismatch

make test-go
ROBOCASA_PYTHON=/absolute/path/to/tangying-robocasa/bin/python \
  .venv/bin/python -m pytest -q tests/e2e/test_robocasa_task_updates.py
```

脚本显式选择 `AGENT_PROVIDER=deterministic`，不继承远程模型密钥。无 `--keep-running` 时退出即清理自有进程；添加该选项会在所有用例通过后重置到初始场景并保留本机仿真，直到 Ctrl-C。`ready.json` 记录当前 Fleet 地址和自有进程 PID；这是临时开发服务，使用测试夹具账号 `admin / admin123`，不能用作生产部署配置。

若要让当前前端使用这套仿真，先停止自己启动的旧预览进程，再将 `ready.json` 的 `baseURL` 填入：

```bash
CONSOLE_BACKEND_URL=http://127.0.0.1:YOUR_FLEET_PORT node scripts/preview-console.cjs
```

打开 `http://127.0.0.1:18130/#workspace`，确认“仿真环境”“场景已同步”，再测试任务。预览的 API 和模型资源始终来自同一个后端。已有其他检出的 Docker 服务不代表当前源码已生效；必须核对实际进程来源，不能用旧任务记录冒充本轮测试。

## 证据与验证范围

本机报告位于 `artifacts/natural-language-eval/baseline-verified/results.json` 和 `artifacts/natural-language-eval/final/results.json`。后者含本轮全部输入、解析结果、任务、世界快照、意图、Experience，以及 Fleet/Edge 二进制 SHA-256，可用 task/step/observation ID 回溯。报告不含操作员 token；整个制品目录因包含测试 mTLS 私钥已被 Git 忽略，不应打包发布。

本轮代表任务：

- 标准双机：`task-efc7d99c0b4f393edf903ba3`
- 同句代词：`task-3233b1c9e9cf5537c92ac5c2`
- 起点不符：`task-631ee98ada8a12aea5a0e366`

验证包括 `make test-go` 全部项目 Go 包、3 项任务更新测试（其中一项启动真实 RoboCasa 进程）、13 项任务评测、137 项 Web 测试、5 项文档检查，以及新增脚本 Ruff 检查。新增反馈修复后单独复测 `go test ./web/...`。单元测试先复现旧行为失败，再验证修复。上述结果是本次未提交工作区快照，发布候选仍需独立采集签名证据和现场验收。

## 后续能力优先级

1. **场景感知的澄清与预检。** 当前不存在的物体到实体绑定阶段才失败，单向场景与回合授权约束也未在批准前提示。下一步可在批准前显示候选物体、可达区域与当前可执行方向，区分“听不懂”“找不到”和“现在做不了”，由用户明确选择。
2. **明确的跨轮上下文。** 当前只支持同句后续“它”，以及版本化任务内的终点修改。跨任务“把刚才那个拿回来”需要记录对象身份、有效期和用户确认，不能直接沿用上次颜色。
3. **能力目录驱动的任务拆解。** “两台机器人协作收拾桌面”、条件等待、多个物体批量处理，需要显式依赖、分支条件、候选计划和验证规则；继续增加字符串别称不能替代这些设计。
4. **实机能力与评测。** 接入真实感知、策略模型、抓取验证和标定，再测多次重复、不同摆放与异常恢复。当前厨房搬运的确定性仿真闭环不能证明真实 XLeRobot 已具备通用家务能力。

## Agent 全程上下文与能力评测（2026-09-21）

自然语言→任务计划评测之外，现在增加模型读到上下文之后的诊断、反思、恢复决策与多轮工具选择评测。完整实验见 [上下文表达报告](../experiments/2026-09-21-agent-context-evaluation.md)。这两层评测互补：一个正确的初始计划，仍可能因为执行时读不到版本、证据和历史参数而做错恢复。

### 表达契约与接入

`core/agentcontext.Document` 是共同事实包，包含身份与目标、步骤依赖、带作用域/时间/引用的来源记录、历史尝试、工具能力和约束。中文 `statement` 负责解释语义，字段负责保存身份、数值、来源类型和有效期。`supersedes` 表示本记录取代的旧记录；缺失值不能自动继承为当前值。

第一轮生产 Go 渲染器比较 `json`、`nl_sections`、`nl_decision`，全局选型为 `json`，即 JSON 外壳承载自然语言事实；第二轮分环节策略见下文。设置 `TANGYING_AGENT_CONTEXT=json` 后重启相应进程可固定为 JSON；不设置或设为 `legacy` 使用原路径。物理执行权限、未知结果停止和 GVF 不受这个开关授权。

接入现有入口：任务账本→`tasks.ContextFor`，GVF 世界→规划输入，Ops finding→异常事件附加视图，Recovery facts/trail→可选模型请求，恢复 actionloop→每轮精确输入快照。`Round.Context` 保存格式、schema、renderer、SHA256 和文本，并随恢复执行事件落账。读取新上下文时排除此前完整快照，避免递归膨胀。任务视图最多取最近 64 条事件，显式声明省略数量。

旧事件不一定有机器人身份、计划版本或传感器 UTC 采集时间。这些字段保留未知，不能把落账时间当采集时间、不能把边缘单调时钟当 UTC。此处提供 role 视图，并没有新增独立运行的 Reflection/Opti Agent，也没有把既有规则 OpsAgent 改为模型裁决物理事实。

### 运行与重放

```bash
make test-agent-context
make eval-agent-context-replay
make eval-agent-context-report

# 新实验目录；配置文件不进入归档，归档不含鉴权头
make eval-agent-context CONTEXT_OUTPUT=artifacts/agent-context-eval/new-run \
  CONTEXT_ARGS='--llm-config artifacts/local-agent/local.env'
make eval-agent-context-episodes CONTEXT_RUN=artifacts/agent-context-eval/new-run \
  CONTEXT_ARGS='--llm-config artifacts/local-agent/local.env'

# 可选字段消融；属于探索性结果，独立文件输出
.venv/bin/python scripts/ablate_agent_context.py \
  --source artifacts/agent-context-eval/new-run --llm-config artifacts/local-agent/local.env
```

`prepare` 冻结数据、提示、源码与实际 Go 渲染器；`dev` 仅在开发集选择；`test` 必须有锁定的选择文件；`replay` 只读实际模型缓存并加载冻结代码。不能在同一实验目录更换模型配置。精确重放依赖响应缓存，不依赖模型提供商未来仍返回相同文本。迁移机器时需相同平台二进制；跨平台新编译必须归档为新的实验。

通用报告脚本要求主测试和多轮结果已存在。新 checkpoint 生成数表，不会把本报告的解释性结论复制过去。使用 `--output` 指定独立报告路径，防止覆盖已有研究报告。

### 指标与候选门禁

主指标按角色等权：Ops 诊断，Reflection 失效假设与受影响/保留步骤，Recovery 下一工具和目标参数；不安全提议使该条失败。另列输出协议、参考证据匹配、token、延迟、多轮完成、误动作与无进展次数。参考证据 F1 不是完整事实忠实度；严格标签可能把合理近义诊断判错，必须保留案例审计。

训练、开发、测试按任务族分开，统计以任务族聚类。比较模型时保持数据及评分版本一致：

```bash
.venv/bin/python scripts/gate_agent_context.py \
  --baseline artifacts/agent-context-eval/run-v2 \
  --candidate artifacts/agent-context-eval/new-run \
  --output artifacts/agent-context-eval/new-run/gate.json
```

门禁验证完整配对、无角色退化、能力差置信区间下界、有效输出至少 99%、观察到的不安全率为零且不劣化、token 预算。它不会自动启用部署，也不能代替物理验证。未来修改门禁阈值必须在看候选测试结果之前确定。

### 后续训练

`--phase export-training` 只允许导出 train，生成 `training/sft.jsonl` 和来源清单；当前 120 条参考输出由规则 oracle 生成，尚未训练任何权重。每条包含可重放消息、奖励维度、来源、数据与评分版本。导出明确排除 dev/test。

新的模型或训练 checkpoint 可通过 `AGENT_MODEL`、`AGENT_BASE_URL`、`AGENT_API_KEY` 配置；使用新目录记录，运行同一 eval 和门禁。已经反复用于调参的测试集必须退为开发集，再准备新的密封最终测试集。当前数据同族模板有冗余，后续应补人工复核的多答案标注、真实轨迹、异步事件和长任务，不能只围绕现有标签优化。

## 按决策环节选择表达（第二轮）

第一轮全局 JSON 选择已细化为八个环节。新的 640 案例将目标、编排、工具结果、证据判读、诊断、反思、恢复和交接分开评分；对照四种完整表达，并把确定性元数据标注作为单独增强组。新结果与选型见 [同一实验报告第 12 节](../experiments/2026-09-21-agent-context-evaluation.md#12-按决策环节选择表达第二轮)。

| 环节 | 当前内置表达 | 入口 |
| --- | --- | --- |
| 目标与约束 | 决策排序自然语言 | 可选 LLM 意图解析输入 |
| 步骤编排 | 决策排序自然语言 | LLMPlanner、GVF 世界视图 |
| 工具结果 | JSON | tool_result 上下文视图；NL 候选未通过留出门禁 |
| 证据判读 | 元数据标注混合 | verification 视图和元数据核对 |
| Ops 诊断 | 元数据标注混合 | Ops finding 事件视图 |
| 任务反思 | JSON | reflection 上下文视图 |
| 恢复决策 | 分节自然语言 | RecoveryPlanner、恢复 actionloop |
| 断点交接 | 元数据标注混合 | handoff 上下文视图 |

`TANGYING_AGENT_CONTEXT=stage` 启用内置配置；显式设置 `json` 等则固定格式；不设置仍走 legacy。`core/agentcontext/stage-policy.json` 记录模型、开发候选、门禁结果、最终选择及哈希。未知消费环节使用 JSON，不会自动猜成某个已校准环节。

`Document.Stage` 表示本次要做的判断，与执行 Agent 的 `Role` 分开。`agentcontext.Project(document, stage)` 返回实际格式、策略版本、渲染版本、文本与哈希；`tasks.ContextFor(task, stage, now)` 构造相应的账本视图。恢复决策快照沿用原落账路径。表中的“视图”是消费接口，不代表自动新增了一个独立 Agent。

新 `hybrid` 将重复作用域放入可机械展开的字典，保留精确参数与原始记录；`annotated` 再标注作用域、时效和替代关系。标注只根据元数据计算，不读取语句猜真假，不授予批准；工具回执和假设即使时间匹配也不会变成物理证据。缺少身份或有效期的生产记录会显示未知，不能用格式替上游补造事实。

```bash
make test-agent-stages
make eval-agent-stages-replay
make eval-agent-stages-report

# 新模型或新 checkpoint 必须使用新目录
.venv/bin/python scripts/evaluate_agent_stages.py \
  --output artifacts/agent-context-eval/new-stage-run \
  --llm-config artifacts/local-agent/local.env --phase all
```

每环节只用开发集选择格式。留出门禁失败时保留预定 JSON 基线，不据测试集重选其他赢家。当前工具结果环节按此规则回退。发布后的组合另做了同任务族的新种子确认，不冒称新的任务族泛化。

本轮按开发候选导出 160 条仅 train 的分环节 SFT 样本；工具结果样本仍对应候选格式，未改成门禁回退后的发布格式。字段级评分可区分失效假设、依赖关系、状态时效、动作参数等错误；没有训练模型权重。与第一轮三角色总分的定义不同，不能直接跨轮比较百分比。
# 第三轮：严格因子拆分与决策契约

最新研究位于 [实验报告第 13 节](../experiments/2026-09-21-agent-context-evaluation.md#13-严格信息拆分数学边界与逐环节表达第三轮)。冻结入口为 `artifacts/agent-context-eval/factorial-v1/run-v3/protocol.json`。该轮区分 JSON/CNL 语法、排列、确定性元数据标注、关键字段完整性，并追加独立新种子的层级结构对照及规划/恢复输出契约消融。追加实验是查看前轮结果后的前瞻修订，不合并冒充一次预注册。

生产者完整字段及类型见 [字段字典](decision-context-fields.md)，机器契约为 `core/agentcontext/decision-context.schema.json` 和 `decision-payload.schema.json`。未知与 false/0/空集合严格区分；源时钟和当前任务时钟不得推定相同，动作批准必须保持参数与身份绑定。扩展 payload 契约是新生产者的设计要求，现有历史适配器没有全部字段。

通过 `uv pip install -e '.[dev,context-research]'` 安装研究依赖。`make test-agent-factorial` 运行相关实现测试；`make eval-agent-factorial-replay` 只使用归档请求复算；`make eval-agent-factorial-formal` 检查抽象 SMT 契约；`make eval-agent-factorial-audit` 比较冻结/当前渲染并审计既有仿真报告；`make eval-agent-factorial-report` 再生第 13 节，保留既有章节。原始网络请求只在带明确 `--llm-config` 的新实验命令中运行，密钥不进入归档。

`TANGYING_AGENT_CONTEXT=factorial` 是显式启用的实验模式；语法配置接受 `json`、`cnl`、`nested_json`、`entity_cnl`、`contract_json`、`contract_nested_json`，后四种含义详见报告。层级表达限 source/raw；生产路径没有缺失字段干预。逐模型逐环节方案保存在 `recommended-policy.json`，可将其原始字节和**当前实际模型名**传入 `agentcontext.ProjectWithFactorPolicy`。该函数拒绝不匹配模型/环节，并在投影记录策略哈希；不会自行更改现有全局策略或执行权限。运行时当前默认保持不变。

默认训练导出仅含 train 80 条。任何后续训练必须排除 dev/test/confirmation，并冻结新的未见语义任务族。接口正确率和相对不退化门禁不能作为实体机器人的安全认证；尤其不能把零准确率与基线持平称为具备上线能力。
