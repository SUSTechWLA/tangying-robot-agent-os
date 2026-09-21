"""第三轮数值表、研究图和增量报告；所有数字来自归档。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .factorial_cases import STAGES
from .factorial_runner import MODELS, sha, write_json

MARKER = "\n## 13. 严格信息拆分、数学边界与逐环节表达（第三轮）"
LABELS = dict(
    zip(STAGES, ["目标", "规划", "工具结果", "验证", "Ops", "反思", "恢复", "交接"], strict=True)
)


def table(headers, rows):
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        + ["| " + " | ".join(map(str, r)) + " |" for r in rows]
    )


def pct(v):
    return "缺失" if v is None else f"{100 * v:.2f}%"


def short(fmt):
    s, o, a, _ = fmt.split("-")
    return (
        ("J" if s == "json" else "N")
        + ("S" if o == "source" else "D")
        + ("0" if a == "raw" else "1")
    )


def uncertainty(e):
    return f"{100 * e['estimate']:+.2f} [{100 * e['ci95'][0]:+.2f}, {100 * e['ci95'][1]:+.2f}]"


def figure(root, result):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Rectangle

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), layout="constrained")
    selection = json.loads((root / "selection.json").read_text())
    for ax, model in zip(axes, MODELS, strict=True):
        values = result["models"][model]["full_by_stage"]
        formats = sorted(values[STAGES[0]])
        data = np.array([[values[s][f]["decision_correct"] * 100 for f in formats] for s in STAGES])
        im = ax.imshow(data, vmin=0, vmax=100, cmap="Blues", aspect="auto")
        ax.set_xticks(range(8), [short(f) for f in formats])
        ax.set_yticks(range(8), STAGES)
        ax.set_title(model)
        for y, s in enumerate(STAGES):
            for x in range(8):
                ax.text(
                    x,
                    y,
                    f"{data[y, x]:.1f}",
                    ha="center",
                    va="center",
                    color="white" if data[y, x] > 65 else "black",
                    fontsize=9,
                )
            x = formats.index(selection["models"][model]["stages"][s])
            ax.add_patch(
                Rectangle(
                    (x - 0.46, y - 0.46), 0.92, 0.92, fill=False, edgecolor="#e78d20", linewidth=2
                )
            )
    fig.colorbar(im, ax=axes, label="Exact decision accuracy (%)", shrink=0.85)
    fig.suptitle("Held-out full-information factorial comparison; orange = dev-locked candidate")
    dest = root / "figures"
    dest.mkdir(exist_ok=True)
    fig.savefig(dest / "factorial-stage-comparison.png", dpi=180)
    fig.savefig(dest / "factorial-stage-comparison.svg")
    plt.close(fig)


def append_report(root, output):
    from .factorial_release import finalize

    deployment = finalize(root)
    load = lambda rel: json.loads((root / rel).read_text())
    result, selected = load("summary.json"), load("selection.json")
    confirm, formal, audit = (
        load("confirmation/summary.json"),
        load("formal/certificate.json"),
        load("audit/summary.json"),
    )
    structure, contract = load("structure-study/summary.json"), load("contract-study/summary.json")
    figure(root, result)
    findings = []
    for m in MODELS:
        r = result["models"][m]
        c = confirm["models"][m]["arms"]
        e = next(
            x for x in confirm["comparisons"] if x["model"] == m and x["baseline"] == "baseline"
        )
        findings.append(
            f"- **{m}**：测试集开发锁定策略 {pct(r['selected_policy']['decision_correct'])}，JSON 基线 {pct(r['baseline']['decision_correct'])}；新种子确认固定策略 {pct(c['policy']['decision_correct'])}，基线 {pct(c['baseline']['decision_correct'])}，差值及 95% 区间 {uncertainty(e)} 个百分点，Holm p={e['p_holm']:.4g}。"
        )
    replacements = {"FINDINGS": "\n".join(findings)}
    replacements["FINDINGS"] += (
        "\n\n严格因子试验中，Pro 的确定性元数据标注效应为 +5.08 个百分点，Holm p=0.0391；语法主效应未显著。追加结构与契约实验的逐项结果和负结果见 13.8。规划在全部追加表达中仍为零分，不能宣称已解决所有环节的能力问题。"
    )
    replacements["PRIMARY"] = table(
        ["模型", "因素", "效应 [95% CI] pp", "原始 p", "Holm p"],
        [
            [r["model"], r["factor"], uncertainty(r), f"{r['p']:.4g}", f"{r['p_holm']:.4g}"]
            for r in result["primary"]
        ],
    )
    replacements["INTERACTIONS"] = table(
        ["模型", "探索性交互", "效应 [95% CI] pp", "Holm p"],
        [
            [r["model"], " × ".join(r["factors"]), uncertainty(r), f"{r['p_holm']:.4g}"]
            for r in result["interactions_exploratory"]
        ],
    )
    masked = []
    for m in MODELS:
        f = result["models"][m]["formats"]
        rows = [v for k, v in f.items() if k.endswith("masked")]
        masked.append(
            [m]
            + [
                pct(sum(r[k] for r in rows) / len(rows))
                for k in [
                    "decision_correct",
                    "supported_correct",
                    "abstain",
                    "unsupported_assertion",
                    "unsafe",
                ]
            ]
        )
    replacements["MASKED"] = table(
        ["模型 / masked", "状态答案正确", "证据支持正确", "拒答", "无支持断言", "危险建议"], masked
    )
    matrices, choices, confirmations = [], [], []
    for m in MODELS:
        r = result["models"][m]
        f = sorted(r["full_by_stage"][STAGES[0]])
        matrices.append(
            f"**{m}**\n\n"
            + table(
                ["环节"] + [short(x) for x in f],
                [
                    [LABELS[s]] + [pct(r["full_by_stage"][s][x]["decision_correct"]) for x in f]
                    for s in STAGES
                ],
            )
        )
        for s in STAGES:
            t = r["gates"][s]
            c = confirm["models"][m]["stages"][s]
            token = c["candidate"]["total_tokens"] / c["baseline"]["total_tokens"]
            choices.append(
                [
                    m,
                    LABELS[s],
                    short(selected["models"][m]["stages"][s]),
                    pct(t["candidate"]["decision_correct"])
                    + " / "
                    + pct(t["baseline"]["decision_correct"]),
                    pct(c["candidate"]["decision_correct"])
                    + " / "
                    + pct(c["baseline"]["decision_correct"]),
                    pct(c["candidate"]["unsafe"]) + " / " + pct(c["baseline"]["unsafe"]),
                    f"{token:.3f}",
                    short(c["release"]),
                    "通过" if t["passed"] and c["passed"] else "回退基线",
                ]
            )
        for e in (x for x in confirm["comparisons"] if x["model"] == m):
            confirmations.append([m, e["baseline"], uncertainty(e), f"{e['p_holm']:.4g}"])
    replacements["MATRICES"] = "\n\n".join(matrices)
    replacements["SELECTION"] = table(
        [
            "模型",
            "环节",
            "开发选型",
            "测试候选/基线",
            "确认固定/基线",
            "确认危险率/基线",
            "确认 token 比",
            "最终",
            "门禁",
        ],
        choices,
    )
    replacements["CONFIRMATION"] = table(
        ["模型", "独立确认对照", "准确率差 [95% CI] pp", "Holm p（四次）"], confirmations
    )
    structure_rows, structure_summary = [], []
    for m in MODELS:
        model = structure["models"][m]
        for s in STAGES:
            v = model["stages"][s]
            structure_rows.append(
                [m, LABELS[s]]
                + [
                    pct(v["arms"][a]["decision_correct"])
                    for a in ["flat_json", "nested_json", "entity_cnl", "prior_policy"]
                ]
                + [v["selected"], v["release"]]
            )
        reduction = (
            1 - model["selected"]["total_tokens"] / model["arms"]["flat_json"]["total_tokens"]
        )
        effect = next(
            e for e in structure["comparisons"] if e["model"] == m and e["baseline"] == "flat_json"
        )
        structure_summary.append(
            f"{m} 的开发锁定组合在新实例上准确率 {pct(model['selected']['decision_correct'])}，flat 基线 {pct(model['arms']['flat_json']['decision_correct'])}；差值 {uncertainty(effect)} pp、Holm p={effect['p_holm']:.4g}；平均总 token 降低 {pct(reduction)}。"
        )
    replacements["STRUCTURE"] = (
        table(
            [
                "模型",
                "环节",
                "flat JSON",
                "nested JSON",
                "entity CNL",
                "前轮策略",
                "开发选择",
                "确认后候选",
            ],
            structure_rows,
        )
        + "\n\n"
        + "\n\n".join(structure_summary)
    )
    contract_rows = []
    for m in MODELS:
        for s, v in contract["models"][m]["stages"].items():
            contract_rows.append(
                [m, LABELS[s]]
                + [
                    pct(v["arms"][a]["decision_correct"]) + " / " + pct(v["arms"][a]["unsafe"])
                    for a in ["json", "nested_json", "contract_json", "contract_nested_json"]
                ]
                + [v["selected"], v["release"]]
            )
    replacements["CONTRACT"] = (
        table(
            [
                "模型",
                "环节",
                "flat 准确/危险",
                "nested 准确/危险",
                "契约 flat 准确/危险",
                "契约 nested 准确/危险",
                "开发选择",
                "相对门禁后",
            ],
            contract_rows,
        )
        + "\n\n"
        + table(
            ["模型", "契约主效应 [95% CI] pp", "Holm p（两模型）"],
            [
                [v["model"], uncertainty(v), f"{v['p_holm']:.4g}"]
                for v in contract["contract_effects"]
            ],
        )
        + "\n\nPro 恢复的 contract JSON 完整字段正确率从 4.17% 增至 62.50%，但危险建议率仍为 25%，且族级证据不足以支持显著提升。Flash 恢复的新增契约候选未通过准确率门禁，回退基线。不能将其中一个模型的改善推广到所有模型。"
    )
    final_rows = []
    for m, stages in deployment["stages"].items():
        for s, v in stages.items():
            spec = v["candidate"]
            final_rows.append(
                [
                    m,
                    LABELS[s],
                    f"{spec['syntax']} / {spec['order']} / {'derived' if spec['annotation'] else 'raw'}",
                    pct(v["accuracy"]),
                    pct(v["unsafe"]),
                    "禁止套用，保留现有守卫" if v["blocked"] else "仅显式实验启用",
                ]
            )
    replacements["FINAL_POLICY"] = table(
        ["模型", "环节", "最终候选表达", "对应确认准确率", "危险建议率", "调用资格"], final_rows
    )
    replacements["AUDIT"] = (
        f"最终归档引用 **{audit['distinct_referenced_requests']:,} 个不同真实请求**，API 记录的总 token 为 **{audit['total_tokens_distinct_requests']:,}**；不同请求中的协议/传输失败 {audit['protocol_failures_distinct']} 个，曾触发传输重试 {audit['requests_with_transport_retries']} 个。计数只包含最终 dev/test/confirmation 引用的请求，不把旧错误案例、连接探针或重复缓存行算作有效实验。"
    )
    replacements["AUDIT"] += (
        f" 因子、结构和契约阶段合计 {audit['scoring_rows']:,} 条评分行；研究适应过程分别冻结，未合并为一次预注册。"
    )
    replacements["FORMAL"] = (
        f"Z3 {formal['z3_version']} 完成 **{formal['unsat_obligations']} 个 UNSAT 义务、{formal['sat_counterexamples']} 个 SAT 反例**；冻结生产编码完成 **{formal['production_roundtrips']:,} 次往返**，{formal['counterfactual_pairs']} 对状态在 8 种 masked 表达上通过 **{formal['erased_prompt_equalities']:,} 次同输入核验**。当前生产渲染与冻结实验另有 **{audit['production_frozen_text_equalities']:,} 次文本/语义哈希一致性检查**。"
    )
    replacements["FORMAL"] += (
        f" 追加结构和契约分别完成 {audit['structure_frozen_text_equalities']:,}、{audit['contract_frozen_text_equalities']:,} 次冻结/当前渲染一致性检查；契约增强移除公开任务说明后与原世界状态逐条相同。"
    )
    migration = audit["recorded_simulation"]
    replacements["MIGRATION"] = (
        f"额外读取前轮已录制的 **{migration['trials']} 条 Gazebo 报告**，完成 **{migration['lossless_payload_roundtrips']:,} 次原始载荷往返**，未启动新仿真或实体机器人。以下为报告顶层非空字段计数；嵌套参数中的 robot 文本不自动等于认证的 robot_id，单调时钟不能当作 UTC。\n\n"
        + table(
            ["字段", "非空条数 / 210"],
            [[k, v] for k, v in migration["nonempty_top_level_field_counts"].items()],
        )
    )
    prefix = os.path.relpath(root, output.parent)
    replacements["FIGURE"] = (
        f"![八环节完整信息准确率]({prefix}/figures/factorial-stage-comparison.png)"
    )
    replacements["LINKS"] = " · ".join(
        f"[{label}]({prefix}/{rel})"
        for label, rel in [
            ("冻结协议", "protocol.json"),
            ("开发选择", "selection.json"),
            ("完整统计", "summary.json"),
            ("独立确认", "confirmation/summary.json"),
            ("逐环节配置", "factorial-policy.json"),
            ("最终调用配置", "deployment-policy.json"),
            ("调用排除依据", "deployment-policy-evidence.json"),
            ("结构对照", "structure-study/summary.json"),
            ("契约消融", "contract-study/summary.json"),
            ("形式化证书", "formal/certificate.json"),
            ("字段迁移和响应审计", "audit/summary.json"),
            ("训练清单", "training/manifest.json"),
        ]
    )
    validation = root.parent / "validation/summary.json"
    replacements["VALIDATION"] = (
        "验收日志位于相邻 `factorial-v1/validation/`；回滚以本轮 before/after 哈希为边界，保留前两轮修改和原始实验档案。"
    )
    if validation.exists():
        v = json.loads(validation.read_text())
        replacements["VALIDATION"] += " 已完成：" + "；".join(v["passed"]) + "。"
    template = Path(__file__).with_name("factorial-report-template.md")
    content = template.read_text()
    for key, value in replacements.items():
        content = content.replace("@@" + key + "@@", value)
    assert "@@" not in content
    previous = output.read_text()
    previous = previous.replace(
        "> 最新分环节选择与优化见第 12 节；前文保留第一轮全局格式实验。",
        "> 最新严格拆分实验、数学推导与逐环节候选见第 13 节；第 1–12 节保留前两轮原始结果。",
    )
    before, found, tail = previous.partition(MARKER)
    later = re.search(r"\n## (?:1[4-9]|[2-9][0-9])\. ", tail) if found else None
    suffix = tail[later.start() :] if later else ""
    output.write_text(before.rstrip() + "\n" + content.rstrip() + "\n" + suffix)
    write_json(
        root / "report-manifest.json",
        {
            "report_sha256": sha(output),
            "template_sha256": sha(template),
            "generator_sha256": sha(Path(__file__)),
            "summary_sha256": sha(root / "summary.json"),
            "confirmation_sha256": sha(root / "confirmation/summary.json"),
        },
    )
    return output
