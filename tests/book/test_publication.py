"""Publication regressions: missing text, stale exports and broken packaged navigation."""

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from copy import deepcopy
from html.parser import HTMLParser
from zipfile import ZipFile

import pytest

from scripts import build_book as book


def snapshot_files(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_every_chapter_and_appendix_is_in_the_publication():
    manifest, documents = book.load_manifest()
    assert len(documents) == 31  # three front sections, 19 chapters, nine appendices
    assert manifest["edition"] == "3.2.0"
    assert manifest["source_snapshot"] == "63af99305fd8204c0a54d86145cb94d89b754326"
    assert all("research/" not in doc["relative"] for doc in documents)
    subprocess.run(
        [sys.executable, str(book.ROOT / "scripts/build_book.py"), "--check"],
        cwd=book.ROOT,
        check=True,
        capture_output=True,
    )


def test_missing_chapter_cannot_silently_disappear(tmp_path):
    root = tmp_path / "book"
    (root / "chapters").mkdir(parents=True)
    (root / "chapters/one.md").write_text("# One\n")
    (root / "chapters/two.md").write_text("# Two\n")
    (root / "edition.json").write_text(
        json.dumps(
            {
                "date": "2026-09-26",
                "source_snapshot": "a" * 40,
                "sources": ["chapters/one.md"],
            }
        )
    )
    with pytest.raises(ValueError, match="include every publication source"):
        book.load_manifest(root)


def test_documentation_links_advance_without_repinning_implementation():
    manifest, documents = book.load_manifest()
    # Keep a historical pair where documentation advanced independently of code.
    manifest["source_snapshot"] = "821659dfd6782927d02924916ced5474a8142d74"
    manifest["documentation_snapshot"] = subprocess.check_output(
        ["git", "rev-parse", "d88158ad"], cwd=book.ROOT, text=True
    ).strip()
    doc = documents[0]
    docs_url = book.resolve_link("../../docs/production/architecture.md", doc, documents, manifest, "html")
    code_url = book.resolve_link("../../internal/edgeagent/host.go", doc, documents, manifest, "html")
    assert f"/{manifest['documentation_snapshot']}/docs/" in docs_url
    assert f"/{manifest['source_snapshot']}/internal/" in code_url
    # The new edition archive exists only in the documentation snapshot. Checking
    # a union of both trees would silently allow a link to the wrong commit.
    archive = "../editions/2.1.0/README.md"
    assert f"/{manifest['documentation_snapshot']}/book/" in book.resolve_link(
        archive, doc, documents, manifest, "html"
    )
    legacy = {k: v for k, v in manifest.items() if k != "documentation_snapshot"}
    assert f"/{legacy['source_snapshot']}/docs/" in book.resolve_link(
        "../../docs/production/architecture.md", doc, documents, legacy, "html"
    )
    with pytest.raises(ValueError, match="not in frozen snapshot"):
        book.resolve_link(archive, doc, documents, legacy, "html")


@pytest.mark.parametrize("snapshot", ["main", "d88158ad"])
def test_documentation_snapshot_cannot_float_with_a_branch(tmp_path, snapshot):
    manifest, _ = book.load_manifest()
    manifest["documentation_snapshot"] = snapshot
    (tmp_path / "edition.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="documentation_snapshot must be a complete Git commit"):
        book.load_manifest(tmp_path)


def test_broken_fences_and_links_fail_before_release():
    with pytest.raises(ValueError, match="unclosed"):
        book.headings("# Title\n```python\nx = 1\n")
    manifest, documents = book.load_manifest()
    with pytest.raises(ValueError, match="broken local link"):
        book.resolve_link("missing-chapter.md", documents[0], documents, manifest, "epub")
    with pytest.raises(ValueError, match="anchor"):
        book.resolve_link("#nonexistent-section", documents[0], documents, manifest, "epub")
    with pytest.raises(ValueError, match="scheme"):
        book.resolve_link("javascript:alert(1)", documents[0], documents, manifest, "epub")


def test_exports_are_deterministic_and_all_epub_links_resolve(tmp_path):
    pytest.importorskip("markdown_it")
    manifest, documents = book.load_manifest()
    first, second = tmp_path / "first", tmp_path / "second"
    book.build(first, manifest, documents)
    book.build(second, manifest, documents)
    assert snapshot_files(first) == snapshot_files(second)
    book.validate_epub(first / "book.epub")
    with ZipFile(first / "book.epub") as archive:
        pages = [p for p in archive.namelist() if p.endswith(".xhtml")]
        assert len(pages) == 33  # 31 sections, navigation, license
        assert b"Copyright (c) 2026 SUSTechWLA" in archive.read("EPUB/license.xhtml")
        assert not any("research" in p for p in pages)
    html = (first / "index.html").read_text()
    assert "<script" not in html
    assert "&lt;details&gt;" not in html
    assert 'href="#part-31"' in html
    assert "第 17 章" in html and "## 17.10" not in html


def test_epub_validator_rejects_missing_internal_resource(tmp_path):
    path = tmp_path / "broken.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "page.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            '<body><a href="absent.xhtml">next</a></body></html>',
        )
    with pytest.raises(ValueError, match="missing EPUB resource"):
        book.validate_epub(path)


@pytest.mark.parametrize("change", ["edition", "source", "unknown", "symlink"])
def test_release_refuses_to_overwrite_a_different_or_unidentified_publication(tmp_path, change):
    pytest.importorskip("markdown_it")
    manifest, documents = book.load_manifest()
    output = tmp_path / "published"
    book.build(output, manifest, documents)
    original = snapshot_files(output)
    revised = deepcopy(manifest)
    revised_docs = deepcopy(documents)
    target = output
    if change == "edition":
        revised["edition"] = "different-edition"
    elif change == "source":
        revised_docs[0]["text"] += "\nChanged source.\n"
    elif change == "unknown":
        (output / "provenance.json").unlink()
        original.pop("provenance.json")
    else:
        target = tmp_path / "link"
        target.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="identity|provenance|symlink"):
        book.build(target, revised, revised_docs)
    assert snapshot_files(output) == original


def test_identical_publication_can_be_rebuilt_in_place(tmp_path):
    pytest.importorskip("markdown_it")
    manifest, documents = book.load_manifest()
    output = tmp_path / "published"
    book.build(output, manifest, documents)
    original = snapshot_files(output)
    book.build(output, manifest, documents)
    assert snapshot_files(output) == original


def test_provenance_hashes_the_loaded_text_even_if_disk_changes(monkeypatch):
    pytest.importorskip("markdown_it")
    manifest, documents = book.load_manifest()
    source = documents[0]
    read_bytes = type(source["path"]).read_bytes

    def changed_bytes(path):
        return b"Changed after load" if path == source["path"] else read_bytes(path)

    monkeypatch.setattr(type(source["path"]), "read_bytes", changed_bytes)
    provenance = book.publication_provenance(manifest, documents)
    assert provenance["sources"][source["relative"]] == hashlib.sha256(
        source["text"].encode()
    ).hexdigest()


def test_previous_edition_can_be_recovered_without_overwriting_history():
    archive = book.ROOT / "book/editions/1.0.0"
    identity = json.loads((archive / "source-manifest.json").read_text())
    manifest = json.loads((archive / "edition.json").read_text())
    assert identity["code_snapshot"] == manifest["source_snapshot"]
    assert set(identity["sources"]) == set(manifest["sources"])
    files = {"book/" + name: digest for name, digest in identity["sources"].items()}
    files["book/book.md"] = identity["combined_sha256"]
    for path, expected in files.items():
        original = subprocess.check_output(
            ["git", "show", identity["book_snapshot"] + ":" + path], cwd=book.ROOT
        )
        assert hashlib.sha256(original).hexdigest() == expected, path
    for name in ("edition.json", "RELEASE.md"):
        original = subprocess.check_output(
            ["git", "show", identity["book_snapshot"] + ":book/" + name], cwd=book.ROOT
        )
        assert (archive / name).read_bytes() == original


def test_1_1_0_release_record_matches_the_previous_snapshot():
    archive = book.ROOT / "book/editions/1.1.0"
    for name in ("edition.json", "RELEASE.md"):
        original = subprocess.check_output(
            ["git", "show", "a5e4c6fae:" + "book/" + name], cwd=book.ROOT
        )
        assert (archive / name).read_bytes() == original
    old_book = subprocess.check_output(["git", "show", "a5e4c6fae:book/book.md"], cwd=book.ROOT)
    assert hashlib.sha256(old_book).hexdigest() == (
        "ec42e5ae1fb647667838dfbf3c0fc243c9fb2ff380d83969da7c4f7cad5d2a3e"
    )


def test_1_1_1_release_record_matches_the_previous_snapshot():
    archive = book.ROOT / "book/editions/1.1.1"
    for name in ("edition.json", "RELEASE.md"):
        original = subprocess.check_output(
            ["git", "show", "fd565bc4036c6d2097b9b016d7ddd2cbf82858a0:book/" + name], cwd=book.ROOT
        )
        assert (archive / name).read_bytes() == original
    old_book = subprocess.check_output(
        ["git", "show", "fd565bc4036c6d2097b9b016d7ddd2cbf82858a0:book/book.md"], cwd=book.ROOT
    )
    assert hashlib.sha256(old_book).hexdigest() == (
        "683025aa83c8ae9200f44d4ab53783f5e16efaba7c5db55300b37dbdb340e8f5"
    )


def test_1_1_2_release_record_matches_the_previous_snapshot():
    archive = book.ROOT / "book/editions/1.1.2"
    snapshot = "d122e6571d0caf9c655052185ae89d9d68c1ed2e"
    for name in ("edition.json", "RELEASE.md"):
        original = subprocess.check_output(
            ["git", "show", f"{snapshot}:book/{name}"], cwd=book.ROOT
        )
        assert (archive / name).read_bytes() == original
    old_book = subprocess.check_output(["git", "show", f"{snapshot}:book/book.md"], cwd=book.ROOT)
    assert hashlib.sha256(old_book).hexdigest() == (
        "882bf04c76b854a6f32b14c9ce7d9e895c545f37c17aa439d800a806c070382b"
    )


def test_1_1_3_sources_are_exactly_recoverable():
    archive = book.ROOT / "book/editions/1.1.3"
    identity = json.loads((archive / "source-manifest.json").read_text())
    manifest = json.loads((archive / "edition.json").read_text())
    assert identity["edition"] == manifest["edition"] == "1.1.3"
    assert identity["code_snapshot"] == manifest["source_snapshot"]
    assert set(identity["sources"]) == set(manifest["sources"])
    files = {"book/" + name: digest for name, digest in identity["sources"].items()}
    files["book/book.md"] = identity["combined_sha256"]
    files["book/README.md"] = identity["readme_sha256"]
    for path, expected in files.items():
        original = subprocess.check_output(
            ["git", "show", identity["book_snapshot"] + ":" + path], cwd=book.ROOT
        )
        assert hashlib.sha256(original).hexdigest() == expected, path
    for name in ("edition.json", "RELEASE.md"):
        original = subprocess.check_output(
            ["git", "show", identity["book_snapshot"] + ":book/" + name], cwd=book.ROOT
        )
        assert (archive / name).read_bytes() == original


def test_1_2_0_content_and_original_generator_are_exactly_recoverable():
    archive = book.ROOT / "book/editions/1.2.0"
    identity = json.loads((archive / "source-manifest.json").read_text())
    manifest = json.loads((archive / "edition.json").read_text())
    assert identity["edition"] == manifest["edition"] == "1.2.0"
    assert identity["code_snapshot"] == manifest["source_snapshot"]
    assert len(identity["sources"]) == 27
    assert set(identity["sources"]) == set(manifest["sources"])
    files = {"book/" + name: sha for name, sha in identity["sources"].items()}
    files.update(
        {
            "book/book.md": identity["combined_sha256"],
            "book/README.md": identity["readme_sha256"],
            "scripts/build_book.py": identity["generator_sha256"],
        }
    )
    for path, expected in files.items():
        original = subprocess.check_output(
            ["git", "show", identity["book_snapshot"] + ":" + path], cwd=book.ROOT
        )
        assert hashlib.sha256(original).hexdigest() == expected, path
    for name in ("edition.json", "RELEASE.md"):
        original = subprocess.check_output(
            ["git", "show", identity["book_snapshot"] + ":book/" + name], cwd=book.ROOT
        )
        assert (archive / name).read_bytes() == original


@pytest.mark.parametrize("edition", ["2.0.0", "2.1.0", "2.2.0"])
def test_textbook_sources_and_generator_are_exactly_recoverable(edition):
    archive = book.ROOT / "book/editions" / edition
    identity = json.loads((archive / "source-manifest.json").read_text())
    manifest = json.loads((archive / "edition.json").read_text())
    assert identity["edition"] == manifest["edition"] == edition
    assert identity["code_snapshot"] == manifest["source_snapshot"]
    assert len(identity["sources"]) == 31
    assert set(identity["sources"]) == set(manifest["sources"])
    files = {"book/" + name: sha for name, sha in identity["sources"].items()}
    files.update({
        "book/book.md": identity["combined_sha256"],
        "book/README.md": identity["readme_sha256"],
        "scripts/build_book.py": identity["generator_sha256"],
        "book/requirements.txt": identity["requirements_sha256"],
    })
    for path, expected in files.items():
        original = subprocess.check_output(
            ["git", "show", identity["book_snapshot"] + ":" + path], cwd=book.ROOT
        )
        assert hashlib.sha256(original).hexdigest() == expected, path
    for name in ("edition.json", "RELEASE.md"):
        original = subprocess.check_output(
            ["git", "show", identity["book_snapshot"] + ":book/" + name], cwd=book.ROOT
        )
        assert (archive / name).read_bytes() == original


@pytest.mark.parametrize("defect", ["missing", "duplicate", "reorder", "unknown", "front"])
def test_part_navigation_cannot_hide_duplicate_or_reorder_sources(defect):
    manifest, documents = book.load_manifest()
    broken = deepcopy(manifest)
    sources = broken["parts"][0]["sources"]
    if defect == "missing":
        sources.pop()
    elif defect == "duplicate":
        sources.append(sources[0])
    elif defect == "reorder":
        sources.reverse()
    elif defect == "unknown":
        sources.append("chapters/absent.md")
    else:
        sources.append(documents[0]["relative"])
    with pytest.raises(ValueError, match="source"):
        book.publication_groups(broken, documents)


def test_older_manifests_keep_flat_navigation():
    manifest, documents = book.load_manifest()
    previous = {k: v for k, v in manifest.items() if k != "parts"}
    assert book.publication_groups(previous, documents) == [(None, documents)]


def test_html_and_epub_navigation_reach_every_section(tmp_path):
    pytest.importorskip("markdown_it")
    manifest, documents = book.load_manifest()
    book.build(tmp_path, manifest, documents)

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []
            self.links = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.ids.append(attrs["id"])
            if tag == "a" and attrs.get("href", "").startswith("#"):
                self.links.append(attrs["href"][1:])

    parser = Links()
    html = (tmp_path / "index.html").read_text()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))
    assert set(parser.links) <= set(parser.ids)
    assert {d["id"] for d in documents} <= set(parser.links)
    expected_sections = {
        d["anchors"][line] for d in documents for line, level, _ in d["outline"] if level == 2
    }
    assert expected_sections <= set(parser.links)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    with ZipFile(tmp_path / "book.epub") as archive:
        package = ET.fromstring(archive.read("EPUB/package.opf"))
        opf = {"o": "http://www.idpf.org/2007/opf"}
        nav_ref = package.find("o:spine/o:itemref[@idref='nav']", opf)
        assert nav_ref is not None and nav_ref.attrib["linear"] == "no"
        nav = ET.fromstring(archive.read("EPUB/nav.xhtml"))
        labels = [s.text for s in nav.findall(".//x:span", ns)]
        assert labels == [p["title"] for p in manifest["parts"]]
        links = [a.attrib["href"] for a in nav.findall(".//x:a", ns)]
        assert links[:-1] == [f"{d['id']}.xhtml#{d['id']}" for d in documents]
        for doc in documents:
            page = ET.fromstring(archive.read(f"EPUB/{doc['id']}.xhtml"))
            links = {a.attrib["href"] for a in page.findall(".//x:a", ns)}
            assert "nav.xhtml#toc" in links
            assert {
                "#" + doc["anchors"][line] for line, level, _ in doc["outline"] if level == 2
            } <= links


def test_back_to_contents_cannot_target_a_non_spine_navigation_document(tmp_path):
    pytest.importorskip("markdown_it")
    manifest, documents = book.load_manifest()
    book.build(tmp_path / "valid", manifest, documents)
    broken = tmp_path / "non-spine.epub"
    with ZipFile(tmp_path / "valid/book.epub") as original, ZipFile(broken, "w") as output:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "EPUB/package.opf":
                data = data.replace(b'<itemref idref="nav" linear="no"/>', b"")
            output.writestr(info, data)
    with pytest.raises(ValueError, match="non-spine"):
        book.validate_epub(broken)
