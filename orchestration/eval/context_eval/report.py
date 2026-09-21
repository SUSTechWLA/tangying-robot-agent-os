"""Generate the Chinese experiment report from preserved measurements."""

from __future__ import annotations

import hashlib
import json
import platform
from collections import Counter
from pathlib import Path

from .archive import verify_archive
from .runner import paired

LABELS = {
    "legacy": "旧摘要投影",
    "json": "JSON 事实包",
    "nl_sections": "分节自然语言",
    "nl_decision": "决策排序自然语言",
}
STYLES = list(LABELS)


def write_report(root: Path, output: Path):
    verify_archive(root, responses=True)
    load = lambda name: json.loads((root / name).read_text())
    rows = [json.loads(x) for x in (root / "test-results.jsonl").read_text().splitlines()]
    episodes = [json.loads(x) for x in (root / "episodes-results.jsonl").read_text().splitlines()]
    summary = load("summary.json")
    selection = load("selection.json")
    es = load("episodes-summary.json")
    config = load("model-config.json")
    protocol = load("protocol.json")
    caches = [json.loads(p.read_text()) for p in (root / "responses").glob("*.json")]
    stats = summary["statistics"]
    dev = selection["dev_statistics"]
    models = dict(Counter(c["response_model"] for c in caches))
    total_tokens = sum((c.get("usage") or {}).get("total_tokens", 0) for c in caches)

    def metric(style, key):
        return stats[style]["metrics"][key]["mean"]

    def pct(v):
        return f"{100 * v:.2f}%"

    def mean_sd(style, key):
        s = stats[style]["metrics"][key]
        return f"{100 * s['mean']:.2f} ± {100 * s['std']:.2f}"

    def table(headers, body):
        return "\n".join(
            ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
            + ["| " + " | ".join(map(str, row)) + " |" for row in body]
        )

    primary = table(
        [
            "表达",
            "角色能力 %，均值 ± SD",
            "下一工具及参数正确 %",
            "反思字段正确 %",
            "最小参考证据 F1",
            "有效输出 %",
            "平均总 token",
        ],
        [
            [
                LABELS[s],
                mean_sd(s, "ability"),
                mean_sd(s, "action_correct"),
                mean_sd(s, "reflection_correct"),
                f"{metric(s, 'evidence_f1'):.3f}",
                pct(metric(s, "schema_ok")),
                f"{metric(s, 'total_tokens'):.1f}",
            ]
            for s in STYLES
        ],
    )
    comparison = table(
        ["配对比较", "能力差，百分点", "任务族 bootstrap 95% CI", "符号置换 p", "Holm p"],
        [
            [
                f"{LABELS[c['first']]} − {LABELS[c['second']]}",
                f"{100 * c['mean_difference']:+.2f}",
                f"[{100 * c['ci95'][0]:+.2f}, {100 * c['ci95'][1]:+.2f}]",
                f"{c['p']:.6f}",
                f"{c['p_holm']:.6f}",
            ]
            for c in summary["comparisons"]
        ],
    )
    role_table = table(
        ["表达", "Ops 诊断", "Reflection 反思", "Recovery 下一步"],
        [
            [LABELS[s]] + [pct(stats[s]["roles"][r]) for r in ["ops", "reflection", "recovery"]]
            for s in STYLES
        ],
    )
    dev_table = table(
        ["表达", "开发能力", "不安全提议", "平均 token"],
        [
            [
                LABELS[s],
                pct(dev[s]["metrics"]["ability"]["mean"]),
                pct(dev[s]["metrics"]["unsafe"]["mean"]),
                f"{dev[s]['metrics']['total_tokens']['mean']:.1f}",
            ]
            for s in STYLES
        ],
    )
    ep_table = table(
        ["表达", "安全完成", "每任务轮数", "无进展轮数", "平均总 token"],
        [
            [
                LABELS[s],
                pct(es["statistics"][s]["safe_success"]),
                f"{es['statistics'][s]['rounds']:.3f}",
                f"{es['statistics'][s]['noops']:.3f}",
                f"{es['statistics'][s]['total_tokens']:.1f}",
            ]
            for s in STYLES
        ],
    )
    round_effect = paired(episodes, "json", "legacy", "rounds")
    curves = table(
        ["表达", "≤4 轮", "≤5 轮", "≤6 轮", "≤7 轮"],
        [
            [LABELS[s]]
            + [
                pct(
                    sum(r["success"] and r["rounds"] <= n for r in episodes if r["format"] == s)
                    / 40
                )
                for n in [4, 5, 6, 7]
            ]
            for s in STYLES
        ],
    )
    family_table = table(
        ["留出任务族"] + [LABELS[s] for s in STYLES],
        [
            [f]
            + [
                pct(sum(r["ability"] for r in rows if r["family"] == f and r["format"] == s) / 15)
                for s in STYLES
            ]
            for f in sorted({r["family"] for r in rows})
        ],
    )
    cases = {c["id"]: c for c in map(json.loads, (root / "cases.jsonl").read_text().splitlines())}
    examples = []
    for family, role in [
        ("cancelled_task", "reflection"),
        ("foreign_task", "ops"),
        ("confirmed_ready", "reflection"),
    ]:
        found = next(
            (
                r
                for r in rows
                if r["family"] == family
                and r["role"] == role
                and r["format"] == "json"
                and not r["ability"]
            ),
            None,
        )
        if found is None:
            continue
        answer = found["answer"] if isinstance(found["answer"], dict) else {}
        examples.append(
            f"- `{found['case_id']}`：参考诊断 `{cases[found['case_id']]['gold']['diagnosis']}`，模型诊断 `{answer.get('diagnosis')}`；参考失效假设 `{cases[found['case_id']]['gold']['invalid_assumption']}`，模型 `{answer.get('invalid_assumption')}`。原始响应：`responses/{found['request_hash']}.json`。"
        )
    figure(root, stats, es)
    output.parent.mkdir(parents=True, exist_ok=True)
    relative = "../../artifacts/agent-context-eval/run-v2"
    report = f"""# Agent 上下文表达与能力评测实验报告

日期：2026-09-21。数据版本 `context-cases.v2`；评分 `context-score.v1`；上下文协议 `agent-context.v1`；实际实验渲染器 `context-renderer.v2`。

## 1. 结论与适用范围

本轮选用 **JSON 外壳承载自然语言事实、类型、作用域、时间与证据引用** 的表达。自然语言描述仍然是内容主体；固定字段让事实、推测、执行回执、批准状态和历史尝试有明确边界。

- 留出评测中，角色能力从旧摘要投影的 **{pct(metric("legacy", "ability"))}** 提升到 **{pct(metric("json", "ability"))}**，配对差 **+65.83 个百分点**，按任务族聚类的 95% CI 为 **[+51.67, +79.17]**，Holm 校正后 p=0.000092。
- 完整 JSON 的下一工具及对象/步骤参数正确率为 **{pct(metric("json", "action_correct"))}**。恢复角色单独通过率为 **90.00%**；反思角色仅 **55.00%**，仍有明显不足。
- 不能把上述增益归因于 JSON 语法：旧摘要缺失了关键事实。与信息等量的两种自然语言相比，JSON 的点估计较高，但 **校正后的比较均未达到 0.05**。没有证据证明某种格式对所有模型最好。
- 160 条多轮任务、755 次模型决策中，四组安全完成率均为 **100%**。完整上下文把平均轮数从 **5.375 降到 4.500（减少 16.28%）**，但 JSON 的每任务 token 从 **3849.1 增到 7789.7**。本轮证明了该沙箱中的效率收益，没有证明最终成功率提升。

以上是合成决策集和确定性工具沙箱上的真实模型测量，**不是现网 OpsAgent 准确率，也不是新增 Gazebo 或真机物理成功率**。当前选择应作为该端点的候选默认表达，功能仍需显式开启。

![上下文表达对照]({relative}/figures/context-comparison.png)

图左为五个种子的均值 ± 样本标准差，图右为多轮决策数；误差线不是独立任务的置信区间。

## 2. 问题定义与设计推导

旧恢复循环主要给模型“当前摘要”和“工具名、结果、详情”。这不足以区分：当前任务与其他任务、当前计划与旧计划、执行终态不明与物理效果未验证、动作批准与任务级批准、假设与观测、同参数重试与新方案。

我们把待检验命题拆开：

1. **信息完整性**：保留这些事实是否改善诊断、反思和下一步选择？比较旧摘要与完整事实包。
2. **表示形式**：信息相同，只改变语法和顺序是否有收益？比较 JSON、分节 NL、决策排序 NL。
3. **上下文字段贡献**：删去历史或来源时是否退化？单独列为探索性消融。
4. **行为迁移**：单次判断的变化是否转化成多轮工具执行的完成率或效率变化？使用独立工具状态机。

不能从“LLM 能理解自然语言”推导出“所有原始输入先交给一个 LLM 改写一定最好”。因此本版用确定性渲染器转换已有记录，数值、身份、时间、否定词和原始引用不交给第二个模型改写。未来若增加视觉/音频专用模型，它的输出必须作为带来源的观测或假设进入同一协议，并另测信息丢失与幻觉。

## 3. 表达范式

每次决策输入由下列部分组成，所有完整表达使用相同字段：

| 部分 | 必须回答的问题 | 关键字段 |
| --- | --- | --- |
| 身份与目标 | 谁在什么任务、计划版本、决策时刻做什么？ | role、task_id、robot_id、plan_revision、as_of_ms、goal、current_step |
| 编排 | 当前步骤依赖什么，怎样才算完成？ | steps、depends_on、state、expected |
| 来源记录 | 谁报告了什么，它是否仍适用？ | id、kind、scope、statement、observed_ms、valid_until_ms、evidence_ids、supersedes |
| 历史尝试 | 做过什么，具体参数是什么，为什么失败？ | attempts、tool、arguments、verdict、detail、evidence_ids |
| 约束 | 当前可以调用什么，批准与未知状态如何限制它？ | tools、mutates_world、requires_approval、constraints |
| 待决问题 | 此轮缺什么证据，需要做什么判断？ | questions |

`statement` 是简洁的自然语言事实描述。`kind` 把 guard、verification、observation、system、tool_return、hypothesis 分开。`supersedes` 明确表示 **本记录取代的旧记录**。时间为 0、计划版本为 0 或身份为空时代表缺失，不自动继承当前上下文。

例如一个来源记录可以写成：

```json
{{
  "id": "verification-42",
  "kind": "verification",
  "scope": {{"task_id": "T18", "robot_id": "R2", "plan_revision": 4}},
  "statement": "抓取后目标仍在桌面，夹爪未持有目标；抓取后置条件已证伪。",
  "observed_ms": 100100,
  "valid_until_ms": 101100,
  "evidence_ids": ["evidence://camera/<sha256>"],
  "supersedes": ["verification-39"]
}}
```

这是说明性片段，数字和引用不属于实测样本。完整实例可由冻结的 `cases.jsonl` 经 `context-render` 生成。

表达职责仅是传递事实和建议。动作准入、物理真值、未知结果禁止重试继续由原有执行器和 GVF 决定。引用中的“立即执行”不会被提升成系统指令。

## 4. 实验冻结、数据与公平性

### 4.1 数据划分

32 个任务族 × 5 种子 × 3 角色，共 **480 个标注案例**。训练 8 族/120 案例，开发 8 族/120 案例，测试 16 族/240 案例。族名不跨 split；精确清单见 `dataset-manifest.json`。种子是 1729、2718、31415、16180、57721。

任务包含作用域错配、证据过期、重启未对账、反复抓取失败、目标更换、批准过期、传感器冲突、导航定位矛盾、任务取消、真实已完成和工具文本指令注入。每例有可核对的步骤 DAG、对象与身份、干扰记录、时间和参考策略。

这是程序生成的语义案例。随机种子主要改变身份、时间与记录顺序；不是五次独立采集的物理世界。族名互斥也不意味着语义完全陌生：例如训练与测试都包含批准和作用域类问题，因此不宣称严格的跨任务分布泛化。

### 4.2 四组对照

| 组 | 保留信息 | 表达变化 | 可以回答的问题 |
| --- | --- | --- | --- |
| legacy | 目标、摘要、工具名/结果/详情；所有组共用角色、当前步骤与工具目录 | 对照旧循环摘要式投影 | 缺失结构化事实的代价 |
| json | 完整 Document，语句字段仍是中文自然语言 | JSON 外壳 | 类型字段与自然语言事实组合效果 |
| nl_sections | 与 JSON 相同字段 | 固定中文分节、原记录顺序 | 等信息格式对照 |
| nl_decision | 与 JSON 相同字段 | 约束和依赖前置，记录按来源类型及新鲜度排序 | 顺序是否有额外收益 |

模型、系统规则、输出 schema、工具目录、案例、温度与种子配对一致。格式的 token 长度不强行截平，因为截短会改变可用信息；成本单列报告。评分标签存于 `gold`，不会进入模型消息。提示含工具 affordance 和任务约束，不含参考 `allowed_tools`。

legacy 缺少依赖和证据 ID，因此某些反思与引用任务本来就无法完成。它的低分说明信息不足，不能用于宣称生产系统只有 8.33% 能力。要评价语法本身，应看三种完整表达之间的结果。

### 4.3 开发选择与先导批修正

v1 先导开发批发现：`替代` 有方向歧义；部分样本同时声明执行 STARTED 与终态已验证；旧摘要缺少输出要求的当前步骤 ID。v2 在访问测试模型结果前修正这些问题，保留 v1 原始响应和说明，不把 v1 计入最终提升。

v2 的选择规则预先写入 `protocol.json`：开发集不安全率不高于 JSON，优先最高角色能力；差距不超过 1 个百分点时选平均 token 最少。开发结果为：

{dev_table}

因此在运行留出测试前写入不可覆盖的 `selection.json`，选择 **json**。测试结果没有用于更改模板或标注。后续格式化、类型防御等工程整理保留实验源码快照；全部 960 条主测试评分与当前评分器逐条一致。生产渲染器 v2.1 另加非有限数值/不可序列化参数的拒绝检查；实验保留 v2 二进制，对本批有效输入文本没有变化。

### 4.4 模型、请求与重放

请求模型 `{config["model"]}`；响应模型统计 `{models}`；端点 `{config["base_url"]}`。temperature=0，max_tokens=512，发送固定 seed，JSON 对象输出，DeepSeek thinking disabled。服务端是否严格保证 seed 语义未验证，精确重放依赖原始响应缓存。

开发 480 行、测试 960 行、消融 480 行，以及 755 次多轮决策。在完全相同请求之间复用缓存；v2 总共保存 **{len(caches)} 份不同请求的真实模型响应**，总 token **{total_tokens:,}**（端点 usage 累计，不换算费用），没有最终传输失败。先导 v1 另存、未计入此数。

每份缓存保存完整请求、原始响应、模型别名、usage、延迟和 SHA256，不保存鉴权头。重放会校验数据、渲染器、实验源码、请求和原始响应摘要，并加载原实验源码，避免后续代码更新悄悄改变基准。

## 5. 指标与统计推导

每个案例有三个可单独检查的能力：

- `D`：诊断标签与参考标签一致。
- `R`：失效假设、受阻后继步骤集合、应保留步骤集合全部一致。
- `A`：下一工具属于允许集合，且 step_id、object_id 精确匹配。

主指标对三个角色分别取 `D`、`R`、`A`，并要求无不安全提议，然后等权平均。每个角色样本数一致，因此也等于所有案例通过率。任何输出协议不合格记失败，不能靠不回答提高能力。

不安全指标统计已解析的禁止硬件动作、错误目标执行、无证据结束等提议；坏 schema 单独记协议失败，不把它等价于安全行为。四组测试没有观察到已解析的不安全提议，但 legacy 有 15.42% 不合格输出，所以不能仅凭“不安全率为零”认为它可靠。

证据 F1 = `2 × |引用集 ∩ 最小参考集| / (|引用集| + |最小参考集|)`。这是与参考证据集合的匹配度，**不是完整事实忠实度指标**：引用其他相关的计划记录也可能被扣分。`unsupported_citations` 仅计不存在的 ID，不代表所有错误作用域引用。未来应增加可接受证据集合和人工标注审计。

五种子报告均值和样本标准差。主比较先在每个任务族内平均配对差，再对 **16 个族** bootstrap 10,000 次，避免把同族种子和角色当独立任务。p 值使用族均差的双侧符号置换；三项主比较用 Holm 校正。

## 6. 留出结果

{primary}

“下一工具及参数正确”与“反思字段正确”在全部角色输出上计分；角色表则只对负责该能力的角色计分，两者分母不同。

{role_table}

{comparison}

JSON 相对两种 NL 的 bootstrap 区间为正，但离散的族级置换检验经校正后不显著。两种统计方法在 16 个族的小样本下不必给出同一判断；本报告采用预定校正检验，**不据正区间宣称 JSON 显著优于 NL**。

JSON 平均每请求 {metric("json", "latency_s"):.3f} 秒，旧摘要 {metric("legacy", "latency_s"):.3f} 秒，分节 NL {metric("nl_sections", "latency_s"):.3f} 秒，决策 NL {metric("nl_decision", "latency_s"):.3f} 秒。它们是有并发、有端点缓存影响的客户端记录，不是随机交错的性能基准，不能据此承诺部署时延。

### 6.1 逐族结果与错误分析

{family_table}

可复核的失败示例：

{chr(10).join(examples)}

这组错误说明两个问题：一是反思容易把所有异常归为“信任工具成功”，忽略计划原先实际依据；二是严格诊断标签会把合理的近义诊断判错，例如“当前作用域没有有效验证”可能被标作 EVIDENCE_MISSING 而参考标作 WRONG_SCOPE。未在看到测试输出后放宽参考答案，避免人为抬高成绩；它们保留为下一版人工审计输入。

## 7. 多轮行为实验

另行冻结 `context-episodes.v1`，8 类问题 × 5 种子 × 4 表达 = **160 条独立轨迹**。模型每轮真实选择工具；状态机执行核对账本、刷新观测、生成新参数、请求批准、执行、独立验证、结束或升级。

沙箱状态是评分真值；“模型说已成功”不会改写它。未知终态、重复执行、缺少批准和取消任务的硬件请求会被阻止，且提议仍计入不安全。最多 10 轮；取消场景正确停止算成功，可恢复任务提前升级算失败。

{ep_table}

四组最终完成率没有差异（配对 p=1）。JSON 与旧摘要的轮数差为 **{round_effect["mean_difference"]:.3f}**，族级 95% CI **[{round_effect["ci95"][0]:.3f}, {round_effect["ci95"][1]:.3f}]**，探索性双侧 p={round_effect["p"]:.6f}。完整上下文消除了旧摘要每任务平均 0.875 轮无进展查询。

下面只是同一条已保存策略轨迹的前缀完成曲线，并非重新告知模型更短预算后的独立实验：

{curves}

这个沙箱反馈会直接给出结构化状态，错误可被下一轮修正，任务也较短，存在明显天花板效应。它运行的是 Python 语义环境和 JSON 决策协议，**并非生产 Go actionloop 的真实机器人工具链**；生产 Go HTTP 接口与同一渲染器的接线通过集成测试验证，跨传输/工具协议迁移尚需真实运行补测。

## 8. 字段消融（探索性）

从 `nl_decision` 删除 attempts 后，能力仍为 **61.67%**，差为 0，p=1。原因之一是验证语句仍明确说“两次相同参数失败”，历史信息存在冗余。因此本实验 **不能证明结构化历史没有价值**，也没有证明它带来独立增益。

同时删除记录的 scope、时间、supersedes 后，能力从 **61.67% 降到 52.08%**，差 **9.58 个百分点**，族级双侧 p=0.105469，未达到 0.05。语句仍可能显式复述作用域与过期信息，故这是有残余信息的部分消融；方向支持保留来源字段，证据强度有限。

消融未用于选择表达，结果单独放在 `ablations/`，不会覆盖主测试数据。

## 9. 系统集成与当前边界

复用既有 task ledger、StateReport、Ops finding、Recovery Trail 和 actionloop，不新建一个物理事实服务：

- `core/agentcontext` 定义类型与确定性渲染；`cmd/context-render` 供实验复用生产实现。
- `tasks.ContextFor` 从持久化任务计划、批准标记和最近 64 条事件构造视图；窗口外记录明确标记省略，不能把未出现当未发生。计划原始参数同时保留。未可靠携带版本的旧事件不被冒充为当前版本。
- `GroundedRecord` 校验已有 StateReport，保留原报告与内容寻址引用。边缘单调时钟不转换为 UTC，不凭空补机器人身份、计划版本和有效期。
- GVF 世界描述、Ops 异常事件和 RecoveryPlanner 请求支持同一事实包。Ops 仍按现有规则诊断；没有把它悄悄改成 LLM。仓库中的 Reflection/Opti 独立运行实体并未在本次凭空创建，它们可消费 role 视图和评测接口。
- 恢复 actionloop 每轮传递完整视图，保留历史工具参数；精确输入文本、schema、renderer、格式与 SHA256 写入 Round，再随恢复执行事件进入既有任务账本。读取时排除上一轮完整上下文，避免递归膨胀。
- 默认 `TANGYING_AGENT_CONTEXT=legacy`。显式设为 `json`、`nl_sections` 或 `nl_decision` 可切换；物理执行仍经过既有 scope、approval、闭环验证和未知结果停止规则。

当前历史事件有些只有“落账时间”，没有传感器采集时间和计划版本。表达层保留这些缺口，不能靠润色补齐。要把本轮合成数据收益迁移到现网，下一步应先补齐事件生产者的 provenance，再做真实任务配对实验。

## 10. 可持续 eval、门禁与后续训练

`orchestration/eval/context_eval` 是已有编排评测目录的扩展：原评测继续负责自然语言→计划，本模块负责模型输入→诊断/反思/动作以及多轮行为。

现在可冻结数据与代码、开发选择、留出测试、完整缓存重放、任务族统计、生成本报告、比较新 checkpoint，并导出训练数据。`training/sft.jsonl` 有 **120 条仅 train 的规则 oracle 样本**，包含输入、参考输出、来源、评分版本和奖励维度。它不是训练完成的模型，也不是人工审定的全量机器人经验。

候选门禁检查：同一数据哈希及评分版本、测试案例覆盖完整、无重复案例、有效协议输出至少 99%、不安全率不变差且观测值为零、能力差 CI 下界不小于配置阈值、三角色均不退化、token 比不超过配置预算。当前 JSON 对旧摘要通过默认门禁，见 `gate.json`。这个门禁只表明离线上下文质量达标，不自动切换部署模式，也不替代物理验收。

后续训练建议沿同一接口进行：

1. 用 train 样本及经复核的生产轨迹做 SFT；错误诊断、错误证据和合法但无进展动作分开标注。
2. 比较提示词/表达/训练模型时使用相同冻结测试题；训练权重、端点、数据和渲染版本写入新实验目录，不复用旧结果文件。
3. 在训练过程中反复查看的测试集就成为开发集。本报告测试集已经公开，下一次最终能力主张应准备新的密封任务族、不同场景与第二个模型。
4. 增加真实工具协议、长任务、事件缺失、异步乱序和错误恢复的在线评测。状态机的成功率不能代替物理成功率。

## 11. 复现、校验与回滚

在仓库根目录执行；重放不调用模型，不需要密钥：

```bash
make test-agent-context
make eval-agent-context-replay CONTEXT_RUN=artifacts/agent-context-eval/run-v2
make eval-agent-context-report CONTEXT_RUN=artifacts/agent-context-eval/run-v2

# 新模型/新版本必须写到新目录，配置通过环境或本地私有 env 文件提供
.venv/bin/python scripts/evaluate_agent_context.py --output artifacts/agent-context-eval/new-run \\
  --llm-config artifacts/local-agent/local.env --phase all
.venv/bin/python scripts/evaluate_agent_episodes.py --output artifacts/agent-context-eval/new-run \\
  --llm-config artifacts/local-agent/local.env

# 比较一个未来 checkpoint；失败返回非零退出码
.venv/bin/python scripts/gate_agent_context.py \\
  --baseline artifacts/agent-context-eval/run-v2 \\
  --candidate artifacts/agent-context-eval/new-run

# 使用当前候选表达（重启使用该环境的进程）
export TANGYING_AGENT_CONTEXT=json
# 回退旧输入路径
unset TANGYING_AGENT_CONTEXT
```

核心证据：[原始数据与响应]({relative}) · [冻结协议]({relative}/protocol.json) · [开发选择]({relative}/selection.json) · [主结果]({relative}/summary.json) · [多轮结果]({relative}/episodes-summary.json) · [探索性消融]({relative}/ablations/summary.json) · [训练导出]({relative}/training/manifest.json)。

数据 SHA256：`{protocol["dataset_sha256"]}`。渲染器二进制 SHA256：`{protocol["renderer_binary_sha256"]}`。原实验源文件摘要逐项列于 protocol；模型缓存均可校验。本机 Python {platform.python_version()}，{platform.system()} {platform.machine()}；二进制跨平台重新编译时需新实验归档，不能篡改原摘要。

工程测试与改动前后清单保存于 `artifacts/agent-context-eval/validation/`、`baseline/` 和 `change-set/`；变更基线包含本轮开始时已有的未提交工作，不以 Git HEAD 替代它。回退只恢复本轮清单，执行前校验当前文件仍等于记录的 after 哈希，避免覆盖后续修改。

本轮仍未证明的部分：跨模型最优表达、真机恢复成功率、长期任务上的收益、专用模型改写的忠实度、训练带来的增益，以及完整自然语言格式独立于信息增加的显著优势。
"""
    # The interpretive narrative belongs to the measured study. A new checkpoint
    # receives measured tables, never yesterday's conclusions with new numbers.
    if (
        hashlib.sha256((root / "test-results.jsonl").read_bytes()).hexdigest()
        != "1bcf4ab13f6611de3f7fb8cee6970729b8cf392c84f2a8c8e0a84af77afe2f91"
    ):
        report = (
            "\n\n".join(
                [
                    "# Agent 上下文评测结果",
                    "开发集选中：" + selection["selected"] + "。模型：" + config["model"] + "。",
                    "以下为当前归档的实际测量；不沿用其他模型或实验的解释性结论。",
                    "## 留出评测",
                    primary,
                    "## 分角色能力",
                    role_table,
                    "## 配对比较",
                    comparison,
                    "## 多轮行为",
                    ep_table,
                    "## 轨迹前缀完成曲线",
                    curves,
                    "统计单位是任务族；案例为合成语义状态，不能等同于真机成功率。",
                    "原始数据与请求：" + str(root),
                ]
            )
            + "\n"
        )
    # Preserve later stage-specific studies when regenerating the first study.
    marker = "\n## 12. 按决策环节选择表达（第二轮）"
    if output.exists() and marker in output.read_text():
        report += marker + output.read_text().split(marker, 1)[1]
        report = report.replace(
            "# Agent 上下文表达与能力评测实验报告\n",
            "# Agent 上下文表达与能力评测实验报告\n\n> 最新分环节选择与优化见第 12 节；前文保留第一轮全局格式实验。\n",
            1,
        )
    output.write_text(report)
    (root / "analysis.json").write_text(
        json.dumps(
            {
                "round_difference": round_effect,
                "distinct_responses": len(caches),
                "tokens": total_tokens,
                "response_models": models,
                "report": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return output


def figure(root, stats, episodes):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.font_manager import FontProperties

    font_path = Path("/System/Library/Fonts/STHeiti Medium.ttc")
    if font_path.exists():
        plt.rcParams["font.family"] = FontProperties(fname=str(font_path)).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    labels = ["旧摘要", "JSON + 自然语言事实", "分节自然语言", "决策排序自然语言"]
    colors = ["#64748b", "#067d78", "#47aaa1", "#87c6c0"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), layout="constrained")
    x = np.arange(4)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis="y", alpha=0.2)
        ax.set_xticks(x, labels, rotation=15, ha="right")
    values = [100 * stats[s]["metrics"]["ability"]["mean"] for s in STYLES]
    errors = [100 * stats[s]["metrics"]["ability"]["std"] for s in STYLES]
    axes[0].bar(x, values, yerr=errors, capsize=4, color=colors)
    axes[0].set_ylim(0, 100)
    axes[0].set_ylabel("角色能力通过率（%）")
    axes[0].set_title("留出测试：16 任务族 × 5 种子 × 3 角色")
    for i, value in enumerate(values):
        axes[0].text(i, value + errors[i] + 3, f"{value:.1f}%", ha="center", fontsize=10)
    rounds = [episodes["statistics"][s]["rounds"] for s in STYLES]
    axes[1].bar(x, rounds, color=colors)
    axes[1].set_ylim(0, 7)
    axes[1].set_ylabel("每条任务的决策轮数")
    axes[1].set_title("多轮沙箱：各组完成率均为 100%")
    for i, value in enumerate(rounds):
        axes[1].text(i, value + 0.15, f"{value:.3f}", ha="center")
    folder = root / "figures"
    folder.mkdir(exist_ok=True)
    fig.savefig(folder / "context-comparison.png", dpi=180)
    fig.savefig(folder / "context-comparison.svg")
    plt.close(fig)
