"""Human-readable views of the exact machine-readable assessment."""

from .engine import catalog


def number(value, percent=False):
    if value is None:
        return "未测量"
    return f"{value * 100:.2f}%" if percent else f"{value:.3f}"


def score_markdown(card):
    primary, risk = [card["benchmark"][k] for k in ["primary_metric", "risk_metric"]]
    lines = [
        f"# 能力分项：{card['run_id']}",
        "",
        f"按 `{card['axis']}` 切片；主指标 `{primary}`，风险指标 `{risk}`。",
        "",
        "百分比为适用单元的描述性均值；缺测显示为缺测。均值不构成晋级或实机执行许可。",
        "",
        "| 切片 | 主指标 | 测量/适用 | 独立簇声明数 | 风险 | 风险测量/适用 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, metrics in [("整体", card["overall"]), *card["slices"].items()]:
        q, r = metrics[primary], metrics[risk]
        lines.append(
            f"| {label} | {number(q['micro_mean'], True)} | {q['measured']}/{q['eligible']} | {q['clusters']} | {number(r['micro_mean'], True)} | {r['measured']}/{r['eligible']} |"
        )
    lines.extend(
        [
            "",
            "完整指标、缺测及不适用计数见同名 JSON。",
            "",
            f"Run SHA-256：`{card['run_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def comparison_markdown(result):
    lines = [
        "# 配对实现比较",
        "",
        f"结果：**{result['status']}**。变更组件：`{', '.join(result['changed_components'])}`。",
        "",
        f"数据分区：`{result['evaluation_split']}`。FAIL 表示不满足给定门禁；INCONCLUSIVE 表示证据不足。历史导入不能作为新实验的预注册确认结果。",
        "",
        result["profile"]["purpose"],
        "",
        "| 切片 | 候选主指标（等簇权重） | 配对差值 | 簇数 | 判定 | 原因 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for row in result["slices"]:
        label = ", ".join(f"{k}={v}" for k, v in row["selector"].items()) or "整体"
        reasons = ", ".join(row["failures"] + row["uncertainties"])
        lines.append(
            f"| {label} | {number(row['primary']['cluster_macro_mean'], True)} | {number(row['paired_effect']['estimate'], True)} | {row['paired_effect']['clusters']} | {row['status']} | {reasons} |"
        )
    lines.extend(
        [
            "",
            "差值为候选减基线；bootstrap 区间仅描述，门禁使用 JSON 中更保守的界。风险上界针对每簇是否发生过风险事件。",
            "",
            f"门禁 SHA-256：`{result['profile_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def coverage_markdown(report):
    lines = [
        "# 已导入证据的能力覆盖",
        "",
        "覆盖表示有记录，不能解读为通过。这里未导入的仓库测试仍可能存在。",
        "",
        "| 能力 | 已导入评测层 |",
        "|---|---|",
    ]
    for key, row in report["capabilities"].items():
        lines.append(f"| {key} · {row['label']} | {', '.join(row['observed_tiers']) or '未接入'} |")
    lines += ["", "| 工具类 | 公开工具 | 精确名称有证据 |", "|---|---|---|"]
    for family, names in catalog()["tool_families"].items():
        measured = [n for n in names if report["exact_tools"][n]]
        lines.append(f"| {family} | {', '.join(names)} | {', '.join(measured) or '未接入'} |")
    lines.extend(
        [
            "",
            "另有 canonical action 判定证据：`"
            + "`, `".join(report["other_tool_identifiers"])
            + "`。这些不自动计作同族公开工具的完整评测。",
            "",
        ]
    )
    return "\n".join(lines)
