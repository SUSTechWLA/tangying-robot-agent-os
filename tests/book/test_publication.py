"""Publication regressions: missing text, stale exports and broken packaged navigation."""

import json
import subprocess
import sys
from zipfile import ZipFile

import pytest

from scripts import build_book as book


def test_every_chapter_and_appendix_is_in_the_publication():
    manifest, documents = book.load_manifest()
    assert len(documents) == 24  # two front sections, 17 chapters, five appendices
    assert manifest["source_snapshot"] == "2edd1c1ff07634765c1a671b6d803c679b3b7a5f"
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
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {
        p.name: p.read_bytes() for p in second.iterdir()
    }
    book.validate_epub(first / "book.epub")
    with ZipFile(first / "book.epub") as archive:
        pages = [p for p in archive.namelist() if p.endswith(".xhtml")]
        assert len(pages) == 26  # 24 sections, navigation, license
        assert b"Copyright (c) 2026 SUSTechWLA" in archive.read("EPUB/license.xhtml")
        assert not any("research" in p for p in pages)
    html = (first / "index.html").read_text()
    assert "<script" not in html
    assert "&lt;details&gt;" not in html
    assert 'href="#part-24"' in html
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
