"""把第二轮环节级结果追加到既有报告，不覆盖第一轮实验。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .archive import verify_archive
from .runner import paired
from .stage_cases import LABELS, STAGES
from .stage_runner import statistics

FORMATS = {
    "json": "JSON",
    "nl_sections": "分节自然语言",
    "nl_decision": "决策排序自然语言",
    "hybrid": "无损混合",
    "annotated": "元数据标注混合",
}
MARKER = "\n## 12. 按决策环节选择表达（第二轮）"


def replace_stage_section(previous, content):
    """Regenerating round two must preserve later experiment chapters."""
    before, found, tail = previous.partition(MARKER)
    later = re.search(r"\n## (?:1[3-9]|[2-9][0-9])\. ", tail) if found else None
    suffix = tail[later.start():] if later else ""
    return before.rstrip() + "\n" + content.rstrip() + "\n" + suffix


def table(headers, rows):
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        + ["| " + " | ".join(map(str, row)) + " |" for row in rows]
    )


def append_report(root, output):
    verify_archive(root, responses=True)
    load = lambda n: json.loads((root / n).read_text())
    selection = load("selection.json")
    summary = load("summary.json")
    release = load("release-policy.json")
    original = json.loads((root.parent / "run/selection.json").read_text())
    rows = list(map(json.loads, (root / "test-results.jsonl").read_text().splitlines()))
    format_rows = [
        {**r, "format": "format_only_policy"}
        for r in rows
        if r["format"] == original["stages"][r["stage"]]
    ]
    format_summary = statistics(format_rows)["format_only_policy"]
    format_comparison = paired(rows + format_rows, "format_only_policy", "json")
    stages = summary["per_stage"]
    stats = summary["statistics"]

    def metric(style, key):
        return stats[style]["metrics"][key]["mean"]

    def pct(v):
        return f"{100 * v:.2f}%"

    matrix = table(
        ["环节"] + [FORMATS[f] for f in FORMATS],
        [
            [LABELS[s]] + [pct(stages[s][f]["metrics"]["ability"]["mean"]) for f in FORMATS]
            for s in STAGES
        ],
    )
    chosen = table(
        ["环节", "开发集锁定候选", "留出候选/JSON", "候选 token/JSON", "门禁", "最终内置表达"],
        [
            [
                LABELS[s],
                FORMATS[selection["stages"][s]],
                pct(stages[s][selection["stages"][s]]["metrics"]["ability"]["mean"])
                + " / "
                + pct(stages[s]["json"]["metrics"]["ability"]["mean"]),
                f"{stages[s][selection['stages'][s]]['metrics']['total_tokens']['mean'] / stages[s]['json']['metrics']['total_tokens']['mean']:.3f}",
                "通过" if summary["gates"][s]["passed"] else "未通过，保留基线",
                FORMATS[release["stages"][s]],
            ]
            for s in STAGES
        ],
    )
    dev = table(
        ["环节"] + [FORMATS[f] for f in FORMATS],
        [
            [LABELS[s]]
            + [
                pct(selection["dev_statistics"][s][f]["metrics"]["ability"]["mean"])
                for f in FORMATS
            ]
            for s in STAGES
        ],
    )
    global_rows = []
    for name, label in [
        ("json", "全部 JSON"),
        ("best_uniform", "开发集最佳统一格式：" + FORMATS[selection["uniform"]]),
        ("stage_policy", "开发集分环节策略（含增强）"),
    ]:
        global_rows.append(
            [
                label,
                pct(metric(name, "ability")),
                pct(metric(name, "unsafe")),
                pct(metric(name, "schema_ok")),
                f"{metric(name, 'total_tokens'):.1f}",
            ]
        )
    fs = format_summary["metrics"]
    global_rows.insert(
        1,
        [
            "仅按环节选格式（不含元数据增强）",
            pct(fs["ability"]["mean"]),
            pct(fs["unsafe"]["mean"]),
            pct(fs["schema_ok"]["mean"]),
            f"{fs['total_tokens']['mean']:.1f}",
        ],
    )
    overall = table(
        ["策略", "完整字段通过率", "危险提议率", "有效输出率", "每次总 token"], global_rows
    )
    comparisons = table(
        ["比较", "差值 pp", "族级 95% CI（pp）", "置换 p", "Holm p"],
        [
            [
                c["first"] + " − " + c["second"],
                f"{100 * c['mean_difference']:+.2f}",
                f"[{100 * c['ci95'][0]:+.2f}, {100 * c['ci95'][1]:+.2f}]",
                f"{c['p']:.6f}",
                f"{c['p_holm']:.6f}",
            ]
            for c in summary["comparisons"]
        ],
    )
    stage_compare = table(
        ["环节", "开发候选 − JSON（pp）", "95% CI（pp）", "Holm p"],
        [
            [
                LABELS[c["stage"]],
                f"{100 * c['mean_difference']:+.2f}",
                f"[{100 * c['ci95'][0]:+.2f}, {100 * c['ci95'][1]:+.2f}]",
                f"{c['p_holm']:.6f}",
            ]
            for c in summary["stage_comparisons"]
        ],
    )
    caches = [json.loads(p.read_text()) for p in (root / "responses").glob("*.json")]
    count = len(caches)
    response_models = sorted({r["response_model"] for r in caches if r.get("response_model")})
    tokens = sum((r.get("usage") or {}).get("total_tokens", 0) for r in caches)
    signed = (metric("stage_policy", "ability") - metric("json", "ability")) * 100
    cost = (metric("stage_policy", "total_tokens") / metric("json", "total_tokens") - 1) * 100
    significant = next(c for c in summary["comparisons"] if c["second"] == "json")["p_holm"] < 0.05
    confirmation_root = root.parent / "confirmation"
    confirmation = json.loads((confirmation_root / "summary.json").read_text())
    confirmation_table = table(
        ["固定策略", "新种子通过率", "危险提议率", "平均总 token"],
        [
            [
                label,
                pct(confirmation["statistics"][key]["ability"]),
                pct(confirmation["statistics"][key]["unsafe"]),
                f"{confirmation['statistics'][key]['total_tokens']:.1f}",
            ]
            for key, label in [
                ("json", "全部 JSON"),
                ("best_uniform", "全部标注混合"),
                ("released", "最终内置组合"),
            ]
        ],
    )
    confirmation_stages = table(
        ["环节", "全 JSON", "最终组合"],
        [
            [
                LABELS[stage],
                pct(confirmation["stages"][stage]["json"]),
                pct(confirmation["stages"][stage]["released"]),
            ]
            for stage in STAGES
        ],
    )
    confirmation_comparisons = table(
        ["比较", "差值 pp", "95% CI（pp）", "Holm p"],
        [
            [
                c["first"] + " − " + c["second"],
                f"{100 * c['mean_difference']:+.2f}",
                f"[{100 * c['ci95'][0]:+.2f}, {100 * c['ci95'][1]:+.2f}]",
                f"{c['p_holm']:.6f}",
            ]
            for c in confirmation["comparisons"]
        ],
    )
    confirmation_responses = len(list((confirmation_root / "responses").glob("*.json")))
    cases = {c["id"]: c for c in map(json.loads, (root / "cases.jsonl").read_text().splitlines())}
    examples = []
    for stage in ["ops", "handoff", "verification", "reflection", "recovery"]:
        selected = selection["stages"][stage]
        row = next(
            (
                r
                for r in rows
                if r["stage"] == stage and r["format"] == selected and not r["ability"]
            ),
            None,
        )
        if row:
            failed = [k for k, v in row["fields"].items() if not v]
            examples.append(
                f"- `{row['case_id']}`，候选 `{selected}` 的错误字段为 `{', '.join(failed)}`。参考输出 `{json.dumps(cases[row['case_id']]['gold'], ensure_ascii=False)}`；模型输出 `{json.dumps(row['answer'], ensure_ascii=False)}`。原始响应 `responses/{row['request_hash']}.json`。"
            )
    make_figure(root, stages, selection, summary)
    prefix = "../../artifacts/agent-context-eval/stage-routing-v1/optimized-run"
    content = f"""{MARKER}

本节针对“每个关键环节应独立比较表达，再定义最终方案”追加实验。**不再把第一轮的全局 JSON 选择当成整个系统的统一最优方案。** 前文 1–11 节是历史实验，以下是当前环节策略及其证据。

### 12.1 本轮结果与最终选型

在新的合成留出集上，开发集锁定的分环节候选策略完整字段通过率为 **{pct(metric("stage_policy", "ability"))}**，全 JSON 为 **{pct(metric("json", "ability"))}**，差 **{signed:+.2f} 个百分点**；主比较经 Holm 校正后{"达到" if significant else "未达到"} 0.05。每请求总 token 相对 JSON **{cost:+.2f}%**。

这不是把一份长文本任意拆成标题：每个环节有自己的输入问题、输出 schema、评分和风险项。最终内置策略逐环节如下；未过门禁时只能保留预定 JSON 基线，不看测试集重选其他赢家。

{chosen}

门禁回退属于发布决定，不把回退后重新拼出的策略分数再宣传为独立留出验证。下文策略总体分数始终指开发集锁定、未经测试集改选的候选。

![八环节表达矩阵]({prefix}/figures/stage-comparison.png)

### 12.2 八个环节分别测什么

| 环节 | 核心判断 | 评分字段 | 对应系统入口 |
| --- | --- | --- | --- |
| 目标与约束 | 保留否定、修订与歧义 | 对象、目的地、禁止对象、是否澄清 | `agent.llmPlanner` 的可选模型输入；原确定性解析优先级不变 |
| 步骤编排 | 满足依赖的执行前沿 | ready / blocked / done 集合 | `orchestration.LLMPlanner`、GVF 世界描述 |
| 工具结果 | 分清命令回执与物理后置条件 | 执行态、物理态、是否允许新尝试 | `tasks.ContextFor(task, "tool_result", now)` 视图接口 |
| 证据判读 | 范围、时效、替代关系是否适用 | 可用/排除记录、转述验证器结论 | verification 视图与 `Eligibility`；GVF 仍拥有物理结论 |
| Ops 诊断 | 找首要阻塞问题，忽略旧信号 | 诊断、优先级、调查工具 | Ops finding 的模型可读事件视图 |
| 任务反思 | 找失效假设与 DAG 影响 | 假设、受影响/可保留步骤、应避免尝试 | reflection 视图接口；未新增独立反思执行 Agent |
| 恢复决策 | 选合法、有进展的下一动作 | 工具、精确参数、工具批准要求 | RecoveryPlanner 和恢复 actionloop |
| 断点交接 | 重建当前版本清单 | 已完成、待执行、未决步骤、下一工具 | handoff 视图接口，供重启/交接消费者使用 |

其中“视图接口”表示可以通过同一协议取得该环节输入，**不是声称已有一个新在线 Agent 自动消费了它**。本轮真实 API 实验针对这些环节的语义决策契约；目标与规划的现有工具调用通道另外做了 HTTP 接线和原验证器不被绕过的测试。

### 12.3 对照拆分：格式与预处理不能混为一谈

比较五组：

1. **JSON**：完整类型字段，事实内容仍是自然语言。
2. **分节自然语言**：与 JSON 字段等量，保留原记录顺序。
3. **决策排序自然语言**：字段等量，将约束/依赖前置，按来源类型和时刻排序。
4. **无损混合**：作用域字典去重；参数与结构保留 JSON，事实保留自然语言，按环节调整章节顺序。能机械展开原字段，但同时包含排序和去重，不归因于语法单因素。
5. **元数据标注混合**：在第 4 组上，代码明确标注其他任务、其他机器人、旧计划、过期、未来记录、缺失时效和被替代记录，并把匹配记录前置。保留全部原文和引用。**这是确定性预处理增强，增加了推导结果，不是等信息的纯格式组。**

`Eligibility` 不读取 statement 来猜测物理真假，不选择恢复工具，不计算参考答案。它只检查元数据；类型为 hypothesis/tool_return 的内容即使范围和时间匹配，也仍然是假设或回执。过期、异域或来自未来的记录不能使当前证据失效。

证据判读本身包含作用域/时效检查，因此第 5 组把该环节的一部分运算交给了代码。该组的收益应解释为**系统分工优化**，不能解释为模型凭更好的措辞获得了新的推理能力。

### 12.4 数据、冻结与选择过程

新数据 `stage-cases.v1`，**640 案例**：train 160、dev 160、test 320。8 环节；每环节 train 4 族、dev 4 族、test 8 族，各 5 种子：104729、130363、155921、196613、262147。没有重复使用第一轮公开测试题。当前生成器的族是参数/干扰/图结构组合，分割不等于严格语义 OOD；多个族可能共享底层规则。

先完成四种格式的开发对比，锁定一份仅格式策略。根据开发失败发现模型误读过期信号和旧版本状态，再增加第 5 组，形成第二份开发策略。整个过程未调用本轮测试集。随后锁定 `selection.json` 和 `candidate-policy.json`，才运行全部留出比较。

选择规则：每环节危险提议率不高于 JSON，先最大完整字段通过率；完全同分才选平均总 token 少的表达。最佳全局统一格式也只在开发集选择，本轮是 **{FORMATS[selection["uniform"]]}**。不能看最终测试矩阵挑每一行最高的列。

开发矩阵：

{dev}

开发 800 行，测试 1,600 行。初始四组开发的 640 条请求在优化开发中原样复用缓存，未重复计成独立测量。共保存 **{count} 份不同请求的真实模型响应**，usage 累计 **{tokens:,} token**。模型请求 `{selection["model"]["model"]}`，响应标识为 `{", ".join(response_models)}`，端点 `{selection["model"]["base_url"]}`，temperature=0、seed 固定、thinking disabled。开发和主测试各发生一次连接中断，保留已完成响应后续跑；没有补造输出或筛掉失败案例。usage 仅包含成功返回的响应，掉线请求未返回的消耗不可量化。

### 12.5 留出矩阵、策略总体结果与统计

每格是该环节 40 个案例的完整字段通过率，要求输出 schema 合格且无该环节定义的危险提议：

{matrix}

总体是八环节等权均值。与第一轮的“三角色综合分”不同，**不能把两轮百分比直接相减当作训练增益**。

{overall}

仅格式策略相对全 JSON 的补充比较：**{100 * format_comparison["mean_difference"]:+.2f} pp**，族级 95% CI **[{100 * format_comparison["ci95"][0]:+.2f}, {100 * format_comparison["ci95"][1]:+.2f}]**，未校正 p={format_comparison["p"]:.6f}。它使用增加元数据标注之前已经冻结的开发选择，不是事后从测试矩阵挑选。

两个预定总体主比较：

{comparisons}

总体统计按 64 个 `stage/family` 聚类，bootstrap 10,000 次；双侧族级符号随机置换 50,000 次，两个总体比较做 Holm 校正。每环节仅 8 个族，另做 8 项 Holm 校正：

{stage_compare}

这些区间是对当前案例生成分布的条件估计；模板重复、单模型和小开发集会限制外推。某一格点估计最高，或成本仅便宜几个 token，都不足以证明该格式在该环节普遍最佳。未来换端点应重跑开发选择和独立验收。

危险提议定义也因环节不同：编排让受阻步骤 ready、回执环节允许不合法重试、证据环节错误转述 VERIFIED、恢复误选执行/结束、交接漏掉未决动作而继续。未涉及动作授权的诊断/反思没有相同危险动作项；不能把总体危险提议率当成真机事故概率。最终物理动作仍由原执行器拦截。

### 12.5.1 固定发布组合的新种子确认

在工具结果按门禁回退 JSON 后，固定全部八项选择，再使用 **3 个从未调用过的新种子**（99991、999983、1000033）执行同一批 64 个任务族，共 **192 个实例、576 条策略结果、{confirmation_responses} 份不同请求的模型响应**。相同格式在不同策略中的同一输入复用缓存。

这是一轮固定策略的实例稳定性复测，**不是新语义任务族的泛化测试**。没有依据确认结果继续换格式或重新放宽门禁。

{confirmation_table}

{confirmation_comparisons}

{confirmation_stages}

最终组合相对全 JSON 的提升在此确认批仍成立；相对全部标注混合的通过率差异未达到显著，同时 token 较少。危险提议并非零，且略高于全部标注混合组，因此不能宣称它在全部维度支配统一策略。恢复环节在新种子上略低于 JSON，证据判读也明显波动；这些是需要扩大样本和真实任务验证的薄弱项，未通过继续调参把它们隐藏。

### 12.6 失败样例与尚未解决的问题

{chr(10).join(examples) if examples else "当前候选在此留出集中没有完整字段失败；仍受样本量和合成环境限制。"}

此轮没有新增机器人轨迹，也没有把单次判断通过率换算成端到端任务成功率。第一轮多轮沙箱结果仍单独保留。还需要真实长任务、丢失/乱序事件、第二个模型和在线工具调用迁移实验。

### 12.7 系统实现与发布规则

`Document.Stage` 明确标识消费环节，与 `Role` 分离。`Project` 统一返回实际 stage、format、policy_version、renderer_version、输入文本与 SHA256。恢复 Round 和事件视图保留这些字段，避免以后只知道开启了 stage 模式却不知道当时用了什么格式。

内置配置为 `core/agentcontext/stage-policy.json`，记录开发候选、逐环节门禁、最终表达、模型和选择/结果哈希。未标识或未校准的环节回退完整 JSON。显式指定 json/nl_sections 等仍覆盖分环节选择；默认 legacy 路径不变。

门禁在测试前确定：相对 JSON 危险提议不增加、能力下降不超过 2.5pp、有效输出至少 97.5%、token 不超过 1.25 倍。未通过时保留 JSON，不能用同一测试集另找一个更好候选。门禁判断是发布控制，**并不表示该格式显著优于 JSON，也不承诺零危险提议**。

现有部分生产事件仍缺机器人身份、计划版本或传感器 UTC 有效期。标注器将缺口显示为未知，不伪造新鲜度。合成基准大多有完整元数据，因此发布配置之前应检查真实事件覆盖；格式不能修复上游缺失的事实。

### 12.8 复现、训练接口与证据

```bash
# 离线验证与重放；不调用模型
make test-agent-stages
make eval-agent-stages-replay
make eval-agent-stages-report

# 新端点/新模型：使用全新实验目录，保留原基准
.venv/bin/python scripts/evaluate_agent_stages.py \\
  --output artifacts/agent-context-eval/new-stage-run \\
  --llm-config artifacts/local-agent/local.env --phase all

# 显式启用本次内置的分环节表达（重启对应进程）
export TANGYING_AGENT_CONTEXT=stage
# 随时固定为全 JSON，或 unset 回到 legacy
export TANGYING_AGENT_CONTEXT=json
```

当前按开发集锁定的候选表达导出 **160 条 train SFT 样本**，保留环节、输入、参考输出、奖励维度和来源；工具结果样本仍对应开发候选，而非门禁回退后的发布格式。没有训练权重，dev/test 不进入训练导出。后续训练可定位到具体环节的字段错误，不再只优化一个总分。

原始证据：[冻结协议]({prefix}/protocol.json) · [开发选择]({prefix}/selection.json) · [留出结果]({prefix}/summary.json) · [发布策略]({prefix}/release-policy.json) · [训练清单]({prefix}/training/manifest.json)。第一版纯格式开发记录保留在相邻 `run/`，本轮变更和回滚基线保留于 `stage-routing-v1/baseline/` 与 `change-set/`。

工程验收：39 项 Python 评测测试、全仓 `go test ./...`、`make build` 与相关 Ruff 检查通过。3,200 次生产/冻结渲染比较完全一致，原始请求与响应哈希校验通过；重复准备归档只校验既有内容，不覆盖冻结输入。日志与核对结果见相邻 `validation/`。回滚脚本先校验全部当前文件的 after 哈希，仅恢复本轮改动，保留实验原始证据。
"""
    previous = output.read_text() if output.exists() else "# Agent 上下文表达与能力评测实验报告\n"
    note = (
        "> 最新严格拆分实验、数学推导与逐环节候选见第 13 节；第 1–12 节保留前两轮原始结果。"
        if "\n## 13. " in previous
        else "> 最新分环节选择与优化见第 12 节；前文保留第一轮全局格式实验。"
    )
    if note not in previous:
        previous = previous.replace(
            "# Agent 上下文表达与能力评测实验报告\n",
            "# Agent 上下文表达与能力评测实验报告\n\n" + note + "\n",
            1,
        )
    output.write_text(replace_stage_section(previous, content))
    analysis = {
        "format_only_comparison": format_comparison,
        "format_only_statistics": format_summary,
        "distinct_responses": count,
        "total_tokens": tokens,
        "report": str(output),
    }
    (root / "stage-analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n"
    )
    return output


def make_figure(root, stages, selection, summary):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import Rectangle

    font = Path("/System/Library/Fonts/STHeiti Medium.ttc")
    if font.exists():
        plt.rcParams["font.family"] = FontProperties(fname=str(font)).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    order = list(FORMATS)
    values = np.array(
        [[stages[s][f]["metrics"]["ability"]["mean"] * 100 for f in order] for s in STAGES]
    )
    fig, ax = plt.subplots(figsize=(11, 6.5), layout="constrained")
    im = ax.imshow(values, cmap="Blues", vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(order)), [FORMATS[f] for f in order])
    ax.set_yticks(range(len(STAGES)), [LABELS[s] for s in STAGES])
    for y, stage in enumerate(STAGES):
        for x in range(len(order)):
            ax.text(
                x,
                y,
                f"{values[y, x]:.1f}%",
                ha="center",
                va="center",
                color="white" if values[y, x] > 65 else "#152238",
            )
        x = order.index(selection["stages"][stage])
        color = "#06aa66" if summary["gates"][stage]["passed"] else "#ee6622"
        ax.add_patch(
            Rectangle((x - 0.47, y - 0.47), 0.94, 0.94, fill=False, edgecolor=color, linewidth=3)
        )
    ax.set_title(
        "八环节留出比较：边框为开发集锁定候选\n绿色通过发布门禁，橙色保留 JSON；元数据标注属于预处理增强",
        pad=16,
    )
    fig.colorbar(im, ax=ax, label="完整字段通过率（%）")
    folder = root / "figures"
    folder.mkdir(exist_ok=True)
    fig.savefig(folder / "stage-comparison.png", dpi=180)
    fig.savefig(folder / "stage-comparison.svg")
    plt.close(fig)
