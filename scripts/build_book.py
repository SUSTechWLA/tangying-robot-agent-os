#!/usr/bin/env python3
"""Build the versioned Markdown, offline HTML and EPUB from authoritative chapters."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import date
from html import escape
from importlib.metadata import version
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "book"
CSS = """
:root { color-scheme: light; font-family: system-ui, 'Noto Sans CJK SC', sans-serif;
 color: #172c36; background: #faf9f5; line-height: 1.85; }
body { margin: 0 auto; max-width: 920px; padding: 32px 24px; }
a { color: #086a71; text-underline-offset: 3px; overflow-wrap: anywhere; }
h1, h2, h3, h4 { line-height: 1.4; color: #122e3d; scroll-margin-top: 20px; }
h1 { font-size: 2rem; } h2 { margin-top: 2.4em; }
section { margin: 4em 0; border-top: 1px solid #c6d6d8; padding-top: 1.8em; }
nav { background: #eef3f2; padding: 20px 28px; border-radius: 8px; }
nav ol { padding-left: 1.6em; } .edition { color: #51646b; }
.book-toc, .book-toc ol { list-style: none; } .book-toc { padding-left: 0; }
.book-toc > li { margin: .35em 0; }
.toc-group { font-weight: 650; } .section-toc { font-size: .9rem; margin: 1.5em 0; }
.section-toc ol { columns: 2; column-gap: 2em; list-style: none; }
.section-toc li { break-inside: avoid; } .back-to-toc { font-size: .85rem; }
blockquote { border-left: 3px solid #579da0; margin: 1.5em 0; padding: .2em 1.2em;
 background: #f0f4f3; } blockquote p { margin: .5em 0; }
pre { white-space: pre; overflow-x: auto; padding: 18px; border-radius: 6px;
 background: #eaf0f0; line-height: 1.55; font-size: .85rem; tab-size: 4; }
code { font-family: ui-monospace, monospace; font-size: .9em; overflow-wrap: anywhere; }
table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse;
 font-size: .9rem; margin: 1.5em 0; }
th, td { border: 1px solid #c6d6d8; padding: 9px 12px; text-align: left; }
th { background: #eaf0f0; } p, li { overflow-wrap: anywhere; }
img { display: block; max-width: 100%; height: auto; margin: 1.5em auto; }
@media (max-width: 640px) { body { padding: 18px 14px; } h1 { font-size: 1.6rem; }
 .section-toc ol { columns: 1; } }
@media print { body { max-width: none; padding: 0; background: white; }
 nav { break-after: page; } section { break-before: page; } h1, h2, h3 { break-after: avoid; }
 .section-toc { break-after: auto; } .section-toc ol { columns: 1; }
 pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 8pt; }
 table { display: table; width: 100%; font-size: 8pt; } td, th { padding: 4px; }
 a { color: inherit; } }
""".strip()
LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\n]+)\)")
IMAGE = re.compile(r"!\[([^\]\n]+)\]\(([^)\n]+)\)")


def figure(href: str, doc: dict) -> tuple[str, Path]:
    """Only ship local, passive SVG figures; no CDN or active image resources."""
    parts = urlsplit(href)
    path = (doc["path"].parent / unquote(parts.path)).resolve()
    if (parts.scheme or parts.netloc or parts.query or parts.fragment
            or not path.is_relative_to((BOOK / "assets").resolve())
            or path.suffix != ".svg" or not path.is_file()):
        raise ValueError(f"invalid or missing book figure: {href}")
    raw = path.read_text()
    if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        raise ValueError("active SVG declaration")
    root = ET.fromstring(raw)
    allowed = {"svg", "title", "desc", "defs", "marker", "path", "rect", "line", "polyline", "circle", "text", "tspan", "g"}
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("book figure must be SVG")
    for node in root.iter():
        if node.tag.split("}")[-1] not in allowed:
            raise ValueError("active or unsupported SVG element")
        for key, value in node.attrib.items():
            if key.lower().startswith("on") or key.split("}")[-1] in {"href", "style"}:
                raise ValueError("active SVG attribute")
            if "url(" in value and not re.fullmatch(r"url\(#[\w-]+\)", value):
                raise ValueError("external SVG resource")
    return "assets/" + path.name, path


def figures(docs: list[dict]) -> dict[str, Path]:
    assets = {}
    for doc in docs:
        for match in IMAGE.finditer(doc["text"]):
            name, path = figure(match[2], doc)
            if name in assets and assets[name] != path:
                raise ValueError("duplicate figure basename")
            assets[name] = path
    return dict(sorted(assets.items()))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def headings(text: str) -> list[tuple[int, int, str]]:
    result = []
    fence = None
    for line_no, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"^(?:> ?)+", "", line)
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if match:
            mark, tail = match.groups()
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not tail.strip():
                fence = None
            continue
        if fence is None and (match := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)):
            result.append((line_no, len(match[1]), match[2]))
    if fence:
        raise ValueError("unclosed Markdown code fence")
    return result


def slug(title: str) -> str:
    return re.sub(r"[^\w\-\s]", "", title.lower()).replace(" ", "-")


def load_manifest(book: Path = BOOK) -> tuple[dict, list[dict]]:
    manifest = json.loads((book / "edition.json").read_text())
    date.fromisoformat(manifest["date"])
    for key in ("source_snapshot", "documentation_snapshot"):
        if key in manifest and not re.fullmatch(r"[0-9a-f]{40}", manifest[key]):
            raise ValueError(f"{key} must be a complete Git commit")
    if "source_snapshot" not in manifest:
        raise ValueError("source_snapshot must be a complete Git commit")
    if len(manifest["sources"]) != len(set(manifest["sources"])):
        raise ValueError("duplicate source in edition.json")
    documents = []
    for index, relative in enumerate(manifest["sources"], 1):
        path = (book / relative).resolve()
        if not path.is_relative_to(book.resolve()) or "research" in Path(relative).parts:
            raise ValueError(f"source outside publication scope: {relative}")
        text = path.read_text()
        outline = headings(text)
        if not outline or outline[0][1] != 1 or sum(h[1] == 1 for h in outline) != 1:
            raise ValueError(f"expected exactly one title: {relative}")
        prefix = f"part-{index:02d}"
        anchors = {
            line: prefix if i == 0 else f"{prefix}-h{i + 1:03d}"
            for i, (line, _, _) in enumerate(outline)
        }
        documents.append(
            {
                "path": path,
                "relative": relative,
                "text": text,
                "title": outline[0][2],
                "outline": outline,
                "anchors": anchors,
                "id": prefix,
            }
        )
    expected = set(book.glob("front/*.md")) | set(book.glob("chapters/*.md"))
    expected |= set(book.glob("appendix/*.md"))
    if expected != {d["path"] for d in documents}:
        raise ValueError("manifest does not include every publication source exactly once")
    publication_groups(manifest, documents)
    return manifest, documents


def publication_groups(manifest: dict, docs: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """Parts organize navigation, but may never hide or reorder publication sources."""
    if "parts" not in manifest:
        return [(None, docs)]  # Older editions retain their flat navigation contract.
    parts = manifest["parts"]
    if not isinstance(parts, list) or not parts:
        raise ValueError("parts must be a nonempty list")
    ordered = []
    titles = set()
    groups = [(None, [d for d in docs if d["relative"].startswith("front/")])]
    by_path = {d["relative"]: d for d in docs}
    for part in parts:
        if not isinstance(part, dict):
            raise TypeError("invalid publication part")
        title, sources = part.get("title"), part.get("sources")
        if not isinstance(title, str) or not title.strip() or title in titles:
            raise ValueError("part titles must be nonempty and unique")
        if (
            not isinstance(sources, list)
            or not sources
            or not all(isinstance(s, str) for s in sources)
        ):
            raise ValueError("each part must list its sources")
        if any(s not in by_path or s.startswith("front/") for s in sources):
            raise ValueError("part contains unknown or front source")
        titles.add(title)
        ordered.extend(sources)
        groups.append((title, [by_path[s] for s in sources]))
    nonfront = [d["relative"] for d in docs if not d["relative"].startswith("front/")]
    if ordered != nonfront:
        raise ValueError("parts must cover every non-front source exactly once in source order")
    flattened = [d["relative"] for _, group in groups for d in group]
    if flattened != [d["relative"] for d in docs]:
        raise ValueError("front sources must precede the grouped publication sources")
    return groups


def navigation(manifest: dict, docs: list[dict], mode: str) -> str:
    entries = []
    for title, group in publication_groups(manifest, docs):
        links = "".join(
            '<li><a href="'
            + (d["id"] + ".xhtml" if mode == "epub" else "")
            + f'#{d["id"]}">{escape(d["title"])}</a></li>'
            for d in group
        )
        entries.append(
            f'<li><span class="toc-group">{escape(title)}</span><ol>{links}</ol></li>'
            if title
            else links
        )
    return "".join(entries)


def repo_tree(manifest: dict) -> set[str]:
    output = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", manifest["source_snapshot"]], cwd=ROOT, text=True
    )
    files = set(output.splitlines())
    for name in list(files):
        files.update(p.as_posix() + "/" for p in Path(name).parents if str(p) != ".")
    return files


def link_snapshot(manifest: dict, relative: str) -> str:
    # Code and its accompanying documentation can be reviewed in separate commits.
    # Older editions retain their original single-snapshot contract.
    if relative.startswith("docs/") or relative.endswith(".md"):
        return manifest.get("documentation_snapshot", manifest["source_snapshot"])
    return manifest["source_snapshot"]


def source_url(manifest: dict, relative: str, directory: bool = False) -> str:
    return (
        f"{manifest['repository']}/{'tree' if directory else 'blob'}/"
        f"{link_snapshot(manifest, relative)}/{quote(relative, safe='/')}"
    )


def resolve_link(href: str, doc: dict, docs: list[dict], manifest: dict, mode: str) -> str:
    parts = urlsplit(href)
    if parts.scheme:
        if parts.scheme not in {"https", "http", "mailto"}:
            raise ValueError(f"unsupported link scheme: {href}")
        return href
    if parts.netloc or parts.query:
        raise ValueError(f"unsupported local link: {href}")
    target = (doc["path"].parent / unquote(parts.path)).resolve() if parts.path else doc["path"]
    match = next((item for item in docs if item["path"] == target), None)
    if match:
        anchor = match["id"]
        if parts.fragment:
            found = [
                line
                for line, _, title in match["outline"]
                if slug(title) == unquote(parts.fragment)
                or match["anchors"][line] == parts.fragment
            ]
            if len(found) != 1:
                raise ValueError(f"missing or ambiguous chapter anchor: {href}")
            anchor = match["anchors"][found[0]]
        return (f"{match['id']}.xhtml" if mode == "epub" else "") + "#" + anchor
    if not target.is_relative_to(ROOT) or not target.exists():
        raise ValueError(f"broken local link in {doc['relative']}: {href}")
    relative = target.relative_to(ROOT).as_posix()
    tree = repo_tree_cached(manifest, relative)
    key = relative + ("/" if target.is_dir() else "")
    if key not in tree:
        raise ValueError(f"repository link is not in frozen snapshot: {relative}")
    return source_url(manifest, relative, target.is_dir()) + (
        "#" + parts.fragment if parts.fragment else ""
    )


_TREE_CACHE: dict[str, set[str]] = {}


def repo_tree_cached(manifest: dict, relative: str = "") -> set[str]:
    commit = link_snapshot(manifest, relative)
    if commit not in _TREE_CACHE:
        _TREE_CACHE[commit] = repo_tree({**manifest, "source_snapshot": commit})
    return _TREE_CACHE[commit]


def markdown(manifest: dict, docs: list[dict]) -> str:
    title = manifest["title"]
    result = [
        (
            f"# {title}\n\n{manifest['subtitle']}\n\n"
            f"数字版 {manifest['edition']} · {manifest['date']} · {manifest['rights']}\n\n"
            "<!-- GENERATED by scripts/build_book.py; edit chapter sources, not this file. -->\n\n"
            "## 目录\n"
        )
    ]
    for title, group in publication_groups(manifest, docs):
        if title:
            result.append(f"\n### {title}\n")
        result.extend(f"- [{doc['title']}](#{doc['id']})" for doc in group)
    for doc in docs:
        result.append("\n\n---\n")
        text = doc["text"]
        # Rewrite Markdown links outside code fences only.
        lines = []
        fence = None
        for number, line in enumerate(text.splitlines(), 1):
            mark = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
            if mark:
                if fence is None:
                    fence = mark[1]
                elif mark[1][0] == fence[0] and len(mark[1]) >= len(fence) and not mark[2].strip():
                    fence = None
            elif fence is None:
                line = IMAGE.sub(lambda m, doc=doc: f"![{m[1]}]({figure(m[2], doc)[0]})", line)
                line = LINK.sub(
                    lambda m, doc=doc: f"[{m[1]}]({resolve_link(m[2], doc, docs, manifest, 'md')})",
                    line,
                )
            if number in doc["anchors"]:
                lines.append(f'<a id="{doc["anchors"][number]}"></a>\n')
            lines.append(line)
        result.append("\n".join(lines).rstrip())
    return "\n".join(result).rstrip() + "\n"


def rendered(doc: dict, docs: list[dict], manifest: dict, mode: str) -> str:
    from markdown_it import MarkdownIt

    # Flatten exercise disclosures for readers without HTML details support.
    text = re.sub(r"^</?details>\s*$", "", doc["text"], flags=re.MULTILINE)
    text = re.sub(r"^<summary>(.*?)</summary>\s*$", r"**\1**", text, flags=re.MULTILINE)
    # Several sources place summary opening and closing on separate lines.
    text = text.replace("<summary>", "").replace("</summary>", "")
    engine = MarkdownIt("commonmark", {"html": False, "xhtmlOut": True})
    engine.enable(["table", "strikethrough"])
    tokens = engine.parse(text)
    # Map headings by order: disclosure normalization changes source line numbers.
    ids = iter(doc["anchors"].values())
    tree = repo_tree_cached(manifest)
    for token in tokens:
        if token.type == "heading_open":
            token.attrSet("id", next(ids))
        for child in token.children or []:
            if child.type == "image":
                child.attrSet("src", figure(child.attrGet("src"), doc)[0])
            if child.type == "link_open":
                child.attrSet(
                    "href", resolve_link(child.attrGet("href"), doc, docs, manifest, mode)
                )

    def code_inline(tokens, index, options, env):
        content = tokens[index].content
        html = f"<code>{escape(content)}</code>"
        if content in tree:
            return f'<a href="{escape(source_url(manifest, content, content.endswith("/")), quote=True)}">{html}</a>'
        return html

    engine.renderer.rules["code_inline"] = code_inline
    body = engine.renderer.render(tokens, engine.options, {})
    sections = [
        f'<li><a href="#{doc["anchors"][line]}">{escape(title)}</a></li>'
        for line, level, title in doc["outline"]
        if level == 2
    ]
    if sections:
        local_nav = (
            '<nav class="section-toc" aria-label="本章目录"><p>本章目录</p><ol>'
            + "".join(sections)
            + "</ol></nav>"
        )
        body = body.replace("</h1>", "</h1>" + local_nav, 1)
    target = "nav.xhtml#toc" if mode == "epub" else "#toc"
    return body + f'<p class="back-to-toc"><a href="{target}">返回全书目录</a></p>'


def xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN"><head><meta charset="utf-8"/>'
        f'<title>{escape(title)}</title><link rel="stylesheet" href="style.css"/>'
        f"</head><body>{body}</body></html>"
    )


def zip_write(
    archive: ZipFile, name: str, data: str, stamp: tuple, compression: int = ZIP_DEFLATED
) -> None:
    info = ZipInfo(name, stamp)
    info.compress_type = compression
    info.external_attr = 0o644 << 16
    archive.writestr(info, data.encode())


def epub(path: Path, manifest: dict, docs: list[dict]) -> None:
    stamp = (*date.fromisoformat(manifest["date"]).timetuple()[:3], 0, 0, 0)
    items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
    ]
    # Chapters link back to nav.xhtml; linked content must be a spine item.
    # Keep it non-linear so ordinary chapter reading does not begin with it.
    spine = ['<itemref idref="nav" linear="no"/>']
    pages = []
    assets = figures(docs)
    for i, name in enumerate(assets):
        items.append(f'<item id="figure-{i}" href="{name}" media-type="image/svg+xml"/>')
    nav = navigation(manifest, docs, "epub")
    for doc in docs:
        name = doc["id"] + ".xhtml"
        items.append(f'<item id="{doc["id"]}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{doc["id"]}"/>')
        pages.append((name, xhtml(doc["title"], rendered(doc, docs, manifest, "epub"))))
    # License is inside the spine so redistribution retains the complete notice.
    items.append('<item id="license" href="license.xhtml" media-type="application/xhtml+xml"/>')
    spine.append('<itemref idref="license"/>')
    pages.append(
        (
            "license.xhtml",
            xhtml(
                "MIT License",
                '<h1 id="license">MIT License</h1><pre>'
                + escape((ROOT / "LICENSE").read_text())
                + "</pre>",
            ),
        )
    )
    nav += '<li><a href="license.xhtml#license">MIT License</a></li>'
    uid = f"urn:tangying:book:{manifest['edition']}:{digest(markdown(manifest, docs).encode())}"
    package = (
        f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="book-id"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="book-id">{uid}</dc:identifier>'
        f"<dc:title>{escape(manifest['title'])}</dc:title>"
        f"<dc:creator>{escape(manifest['creator'])}</dc:creator>"
        f"<dc:language>{manifest['language']}</dc:language>"
        f"<dc:date>{manifest['date']}</dc:date>"
        f"<dc:rights>{escape(manifest['rights'])}</dc:rights>"
        f'<meta property="dcterms:modified">{manifest["date"]}T00:00:00Z</meta>'
        "</metadata><manifest>"
        + "".join(items)
        + "</manifest><spine>"
        + "".join(spine)
        + "</spine></package>"
    )
    with ZipFile(path, "w") as archive:
        zip_write(archive, "mimetype", "application/epub+zip", stamp, ZIP_STORED)
        zip_write(
            archive,
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="EPUB/package.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
            stamp,
        )
        zip_write(archive, "EPUB/package.opf", package, stamp)
        zip_write(archive, "EPUB/style.css", CSS, stamp)
        zip_write(
            archive,
            "EPUB/nav.xhtml",
            xhtml(
                "目录",
                '<nav epub:type="toc" id="toc"><h1>目录</h1><ol class="book-toc">'
                + nav
                + "</ol></nav>",
            ),
            stamp,
        )
        for name, body in pages:
            zip_write(archive, "EPUB/" + name, body, stamp)
        for name, source in assets.items():
            zip_write(archive, "EPUB/" + name, source.read_text(), stamp)
    validate_epub(path)


def validate_epub(path: Path) -> None:
    with ZipFile(path) as archive:
        first = archive.infolist()[0]
        if first.filename != "mimetype" or first.compress_type != ZIP_STORED:
            raise ValueError("EPUB mimetype must be first and uncompressed")
        if archive.read("mimetype") != b"application/epub+zip":
            raise ValueError("invalid EPUB mimetype")
        roots = {
            name: ET.fromstring(archive.read(name))
            for name in archive.namelist()
            if name.endswith((".xhtml", ".opf", ".xml"))
        }
        anchors = {}
        spine_resources = None
        for name, root in roots.items():
            ids = [node.attrib["id"] for node in root.iter() if "id" in node.attrib]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate IDs: {name}")
            anchors[name] = set(ids)
            if name.endswith(".opf"):
                ns = {"opf": "http://www.idpf.org/2007/opf"}
                items = {
                    item.attrib["id"]: (Path(name).parent / item.attrib["href"]).as_posix()
                    for item in root.findall("opf:manifest/opf:item", ns)
                }
                spine_resources = {
                    items[item.attrib["idref"]]
                    for item in root.findall("opf:spine/opf:itemref", ns)
                }
        for name, root in roots.items():
            for node in root.iter():
                for key in ("href", "src"):
                    href = node.attrib.get(key)
                    if not href or urlsplit(href).scheme:
                        continue
                    parts = urlsplit(href)
                    target = (
                        (Path(name).parent / unquote(parts.path)).as_posix() if parts.path else name
                    )
                    if target not in archive.namelist():
                        raise ValueError(f"missing EPUB resource: {name} -> {href}")
                    if (
                        spine_resources is not None
                        and name.endswith(".xhtml")
                        and target.endswith(".xhtml")
                        and target not in spine_resources
                    ):
                        raise ValueError(
                            f"EPUB content link targets non-spine item: {name} -> {href}"
                        )
                    if parts.fragment and parts.fragment not in anchors.get(target, set()):
                        raise ValueError(f"missing EPUB anchor: {name} -> {href}")


def publication_provenance(manifest: dict, docs: list[dict]) -> dict:
    return {
        "edition": manifest,
        "sources": {d["relative"]: digest(d["text"].encode()) for d in docs},
        "figures": {name: digest(path.read_bytes()) for name, path in figures(docs).items()},
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "renderer": {p: version(p) for p in ("markdown-it-py", "mdurl")},
        "requirements_sha256": digest((BOOK / "requirements.txt").read_bytes()),
        "validation": "source inventory, part coverage/order, code fences, local links, XHTML XML and EPUB resource anchors",
    }


def build(output: Path, manifest: dict, docs: list[dict]) -> None:
    provenance = publication_provenance(manifest, docs)
    assets = figures(docs)
    names = ["LICENSE", "book.epub", "book.md", "index.html", "provenance.json", "SHA256SUMS"]
    if output.is_symlink() or any((output / n).is_symlink() for n in names):
        raise ValueError("publication output must not be a symlink")
    if (output / "assets").is_symlink() or any((output / n).is_symlink() for n in assets):
        raise ValueError("publication figures must not be symlinks")
    if output.exists() and any(output.iterdir()):
        try:
            previous = json.loads((output / "provenance.json").read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("nonempty output lacks valid provenance; choose a new directory") from exc
        if previous != provenance:
            raise ValueError("publication identity changed; choose a new edition or output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name, source in assets.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    full = markdown(manifest, docs)
    (output / "book.md").write_text(full)
    nav = (
        '<nav aria-label="目录" id="toc"><h2>目录</h2><ol class="book-toc">'
        + navigation(manifest, docs, "html")
        + "</ol></nav>"
    )
    body = "".join("<section>" + rendered(d, docs, manifest, "html") + "</section>" for d in docs)
    html = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(manifest['title'])}</title><style>{CSS}</style></head><body>"
        f"<header><h1>{escape(manifest['title'])}</h1><p>{escape(manifest['subtitle'])}</p>"
        f'<p class="edition">数字版 {manifest["edition"]} · {manifest["date"]}</p></header>'
        + nav
        + "<main>"
        + body
        + "</main><footer><h2>MIT License</h2><pre>"
        + escape((ROOT / "LICENSE").read_text())
        + "</pre></footer></body></html>\n"
    )
    (output / "index.html").write_text(html)
    epub(output / "book.epub", manifest, docs)
    (output / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    (output / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n"
    )
    names = ["LICENSE", "book.epub", "book.md", "index.html", "provenance.json"]
    names.extend(assets)
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest((output / n).read_bytes())}  {n}\n" for n in names)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="check generated Markdown without renderer dependencies",
    )
    parser.add_argument("--release", action="store_true", help="also build offline HTML and EPUB")
    parser.add_argument("--output", type=Path, help="release output directory")
    args = parser.parse_args()
    if args.check and args.release:
        parser.error("choose --check or --release")
    manifest, docs = load_manifest()
    combined = markdown(manifest, docs)
    target = BOOK / "book.md"
    if args.check:
        if not target.exists() or target.read_text() != combined:
            raise SystemExit("book/book.md is stale; run python3 scripts/build_book.py")
        print(f"Book sources and generated Markdown verified: {len(docs)} sections")
        return
    target.write_text(combined)
    if args.release:
        output = args.output or ROOT / "artifacts" / "book" / manifest["edition"]
        build(output.absolute(), manifest, docs)
        print(f"Publication built and structurally verified: {output}")
    else:
        print("Generated book/book.md")


if __name__ == "__main__":
    main()
