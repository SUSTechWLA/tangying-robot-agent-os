"""Offline figure packaging and the two executable classroom counterexamples."""
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest
from scripts import build_book as book


def test_figures_are_embedded_and_identified_in_both_publications(tmp_path):
    manifest, docs = book.load_manifest()
    book.build(tmp_path, manifest, docs)
    assets = book.figures(docs)
    assert len(assets) == 22
    html = (tmp_path / 'index.html').read_text()
    provenance = json.loads((tmp_path / 'provenance.json').read_text())
    checksums = (tmp_path / 'SHA256SUMS').read_text()
    with ZipFile(tmp_path / 'book.epub') as archive:
        ns = {'o': 'http://www.idpf.org/2007/opf'}
        package = ET.fromstring(archive.read('EPUB/package.opf'))
        packaged = {e.attrib['href'] for e in package.findall('.//o:item', ns)
                    if e.attrib['media-type'] in {'image/svg+xml', 'image/png'}}
        assert packaged == set(assets)
        for name, source in assets.items():
            raw = source.read_bytes()
            assert f'src="{name}"' in html
            assert (tmp_path / name).read_bytes() == archive.read('EPUB/' + name) == raw
            sha = hashlib.sha256(raw).hexdigest()
            assert provenance['figures'][name] == sha
            assert f'{sha}  {name}\n' in checksums
    subprocess.run([sys.executable, 'scripts/build_book_figures.py', '--check'],
                   cwd=book.ROOT, capture_output=True, check=True)


@pytest.mark.parametrize('href', ['https://example.org/figure.svg', '../assets/missing.svg',
                                  '../../README.md', '../assets/02-architecture.svg?version=1'])
def test_missing_external_or_out_of_scope_figure_is_rejected(href):
    _, docs = book.load_manifest()
    with pytest.raises(ValueError, match='invalid or missing'):
        book.figure(href, docs[0])


@pytest.mark.parametrize('content', ['<script>alert(1)</script>', '<g onload="run()"/>',
                                    '<text style="fill:red">x</text>',
                                    '<path fill="url(https://example.org/a.svg)"/>'])
def test_active_svg_is_rejected_before_packaging(tmp_path, monkeypatch, content):
    monkeypatch.setattr(book, 'BOOK', tmp_path)
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets/bad.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">' + content + '</svg>')
    with pytest.raises(ValueError, match='active|external'):
        book.figure('../assets/bad.svg', {'path': tmp_path / 'chapters/ch1.md'})


def test_asset_directory_symlink_cannot_redirect_publication_writes(tmp_path):
    manifest, docs = book.load_manifest()
    output, victim = tmp_path / 'publication', tmp_path / 'outside'
    output.mkdir(); victim.mkdir()
    (output / 'assets').symlink_to(victim, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        book.build(output, manifest, docs)
    assert not list(victim.iterdir())


def test_changed_figure_cannot_overwrite_an_existing_edition(tmp_path, monkeypatch):
    manifest, docs = book.load_manifest()
    output = tmp_path / 'publication'
    book.build(output, manifest, docs)
    original = (output / 'index.html').read_bytes()
    target = next(iter(book.figures(docs).values()))
    read_bytes = type(target).read_bytes
    monkeypatch.setattr(type(target), 'read_bytes',
                        lambda p: read_bytes(p) + (b'\n' if p == target else b''))
    with pytest.raises(ValueError, match='identity changed'):
        book.build(output, manifest, docs)
    assert (output / 'index.html').read_bytes() == original


def test_lost_reply_lab_records_effects_and_refuses_to_erase_a_run(tmp_path):
    output = tmp_path / 'classroom'
    cmd = [sys.executable, 'book/labs/uncertain_outcome.py', '--output', str(output)]
    subprocess.run(cmd, cwd=book.ROOT, check=True, capture_output=True)
    raw = (output / 'summary.json').read_bytes()
    report = json.loads(raw)
    assert report['physicalExecution'] is False
    assert [(r['effects'], r['state']) for r in report['results']] == [
        (2, 'COMPLETED'), (1, 'UNKNOWN'), (1, 'COMPLETED')]
    assert subprocess.run(cmd, cwd=book.ROOT, capture_output=True).returncode != 0
    assert (output / 'summary.json').read_bytes() == raw


def test_context_lab_preserves_constraints_and_rejects_scope_and_budget_errors():
    result = subprocess.run([sys.executable, 'book/labs/context_projection.py'],
                            cwd=book.ROOT, check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report['naiveTail'] == ['c57', 'c58', 'c59']
    assert report['protectedView']['guards'] == ['do not enter D']
    assert report['protectedView']['unresolved'] == ['command-c12']
    assert report['protectedView']['completedCount'] == 59
    assert report['archiveRestoredExactly'] is True
    assert set(report['rejected']) == {'cross_robot_read', 'protected_state_over_budget'}
