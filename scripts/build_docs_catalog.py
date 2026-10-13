#!/usr/bin/env python3
"""Build the path-preserving documentation catalog without changing source documents."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUTPUT = DOCS / "catalog.md"
GROUPS = {
    "navigation": ("导航与维护", "四条路线选择阅读顺序；本目录保留每份资料的原路径。"),
    "guides": ("使用指南", "查操作与功能；旧仿真案例按页面注明的引擎和版本阅读。"),
    "architecture": ("架构与协议", "查原理、组件边界和协议；设计设想及历史快照不等于当前交付。"),
    "development": ("开发参考与演进记录", "先读开发导航；带日期记录回答当时如何设计、实现和修复。"),
    "operations": ("部署与运维流程", "查角色、启动、故障、升级及放行条件。"),
    "install": ("分角色安装", "按目标机器选择；旧 Cloud 控制面文档保留为历史安装资料。"),
    "production": ("运行手册、接口与验收快照", "参考文档与历史审计并存；不能把旧状态页当作当前整仓认证。"),
    "sim2real": ("实体机器人接入", "软件接口、离线前置检查和现场物理验收分别核对。"),
    "frontend": ("工作台与交互", "查用户流程、开发诊断及界面资料。"),
    "experiments": ("实验、预登记与复验", "区分研究对照、工程案例和工具说明；结论以原报告的环境、版本和限制为准。"),
    "releases": ("软件发布记录", "记录指定版本的发布身份，不覆盖后续分支改动。"),
    "superpowers": ("原始设计档案", "按原日期理解决策背景；计划项不作为已实现或已验收的证明。"),
}


def title(path: Path) -> str:
    match = re.search(r"^#\s+(.+)$", path.read_text(), re.MULTILINE)
    text = match.group(1).strip() if match else path.stem
    return text.replace("|", "／").replace("[", "［").replace("]", "］").replace("`", "")


def kind(path: Path) -> str:
    relative = path.relative_to(DOCS)
    name = path.name
    group = relative.parts[0]
    if name == "README.md" or len(relative.parts) == 1:
        return "导航 / 维护"
    if "实验报告已归档" in title(path):
        return "迁移指针（保留旧引用）"
    if relative.as_posix() in ("install/cloud.md", "guides/quickstart.md"):
        return "历史安装 / 回归入口"
    if group == "superpowers":
        return "原始设计档案"
    if group == "releases":
        return "指定版本发布记录"
    if group == "experiments":
        if name.endswith("-protocol.md") and "system-evaluation" in name:
            return "实验协议 / 预登记"
        return "验收工具说明" if name == "long-horizon-harness.md" else "实验 / 验收记录"
    if group == "development":
        if re.match(r"\d{4}-\d{2}-\d{2}-", name):
            return "实施 / 设计 / 复盘（按文内版本）"
        return "开发参考 / 方案（核对文内状态）"
    if group == "production" and any(s in name for s in ("acceptance", "readiness", "assessment", "status", "release-evidence")):
        return "验收 / 就绪快照"
    if group == "architecture":
        return "架构 / 协议 / 设计参考"
    return "操作 / 接口参考"


def render() -> str:
    files = sorted(p for p in DOCS.rglob("*") if p.is_file() and p != OUTPUT
                   and not any(part.startswith(".") for part in p.relative_to(DOCS).parts))
    pages = [p for p in files if p.suffix == ".md"]
    attachments = [p for p in files if p.suffix != ".md"]
    groups: dict[str, list[Path]] = {key: [] for key in GROUPS}
    for page in pages:
        parts = page.relative_to(DOCS).parts
        group = "navigation" if len(parts) == 1 or parts[0] in ("learning", "research") else parts[0]
        if group not in groups:
            raise ValueError(f"add a catalog category for {page.relative_to(DOCS)}")
        groups[group].append(page)
    lines = ["# 全量分类目录", "", "[文档导航](README.md) · [学习](learning/README.md) · [开发](development/README.md) · [运维](operations/README.md) · [研究](research/README.md)", "",
             "本页由 `python3 scripts/build_docs_catalog.py` 从原文件标题和路径生成。分类表示用途，不自动认证内容仍然有效；以文内版本、源码和对应证据判断。新增资料后重新生成，使用 `--check` 检查是否遗漏。", "",
             f"覆盖 `docs/` 中除本页外的 **{len(pages)} 份 Markdown** 与 **{len(attachments)} 份配套文件**；书籍通过[书籍目录](../book/README.md)进入，未重复列出历史版次和生成合订本。", "",
             "## 分类总览", "", "| 分类 | 文档数 | 阅读原则 |", "| --- | ---: | --- |"]
    for key, (heading, description) in GROUPS.items():
        lines.append(f"| [{heading}](#catalog-{key}) | {len(groups[key])} | {description} |")
    for key, (heading, description) in GROUPS.items():
        lines.extend(["", f'<a id="catalog-{key}"></a>', "", f"## {heading}", "", description, "", "| 文档 | 文件 | 类型 |", "| --- | --- | --- |"])
        for path in groups[key]:
            rel = path.relative_to(DOCS).as_posix()
            lines.append(f"| [{title(path)}]({rel}) | `{rel}` | {kind(path)} |")
    lines.extend(["", "## 配套数据与插图", "", "JSON/CSV 是指定实验或文档的摘要/附件，不能单独代替原报告；图片用于解释或展示，物理完成依据仍须查原始观测及 manifest。", "", "| 文件 | 格式 | 对应目录 |", "| --- | --- | --- |"])
    for path in attachments:
        rel = path.relative_to(DOCS).as_posix()
        lines.append(f"| [{path.name}]({rel}) | {path.suffix.lstrip('.').upper()} | `{path.parent.relative_to(DOCS)}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check committed catalog without writing")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != expected:
            print("Documentation catalog is stale; run python3 scripts/build_docs_catalog.py")
            return 1
        print("Documentation catalog is current")
        return 0
    OUTPUT.write_text(expected)
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
