"""M1 acceptance tests for stage_01_ingest.

Real Docling ingest is `slow`-marked because the first call downloads layout
and OCR models (~hundreds of MB) and takes minutes. CI runs the fallback path
unmarked; the slow tests run locally / nightly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from marrow.config import load_config
from marrow.io import read_json, write_json
from marrow.schemas.document import CanonicalDocument, ChapterCoverageAudit
from marrow.schemas.run import RunManifest
from marrow.stages import stage_01_ingest
from marrow.stages.stage_01_ingest import _heuristic_split_sections, _ingest_fallback


def _seed_manifest(working_dir: Path, book_path: Path, mode: str = "api") -> None:
    """Write the minimum manifest stage_01_ingest reads to find the book path."""
    working_dir.mkdir(parents=True, exist_ok=True)
    manifest = RunManifest(
        book_slug="test",
        book_path=str(book_path.resolve()),
        mode=mode,  # type: ignore[arg-type]
        started_at=datetime.now(UTC),
        status="in_progress",
        config={},
        marrow_version="test",
    )
    write_json(working_dir / "manifest.json", manifest)


# ---- Fallback path: lightweight, runs in CI ----


def test_fallback_extracts_chapters_from_plain_text() -> None:
    text = (
        "Chapter 1: Foundations\n"
        "All warfare is based on deception.\n\n"
        "Therefore, when capable of attacking, feign incapacity.\n\n"
        "Chapter 2: Strategy\n"
        "Supreme excellence consists in breaking the enemy's resistance.\n"
    )
    sections = _heuristic_split_sections(text, "test-book")
    assert len(sections) == 2
    assert sections[0].title.startswith("Chapter 1")
    assert sections[1].title.startswith("Chapter 2")
    assert all(s.paragraphs for s in sections)


def test_fallback_returns_audit_with_one_body_section_when_no_headings() -> None:
    doc, audit = _ingest_fallback(_make_temp_text_file("plain text only"), "test")
    assert isinstance(doc, CanonicalDocument)
    assert isinstance(audit, ChapterCoverageAudit)
    assert audit.audit_passed
    assert audit.headings_detected == 1


def _make_temp_text_file(text: str) -> Path:
    import tempfile

    path = Path(tempfile.mkstemp(suffix=".txt")[1])
    path.write_text(text)
    return path


# ---- Real Docling path: slow, requires torch + first-run model download ----


@pytest.mark.slow
def test_real_docling_extracts_three_chapters(synthetic_pdf: Path, tmp_path: Path) -> None:
    cfg = load_config(overrides={"mode": "api", "runs_dir": str(tmp_path)})
    working_dir = tmp_path / "wd"
    _seed_manifest(working_dir, synthetic_pdf)

    result = stage_01_ingest.run(working_dir, cfg)

    assert result.status in ("success", "warning")
    audit = read_json(
        working_dir / "01_ingest" / "chapter_coverage_audit.json", ChapterCoverageAudit
    )
    # The synthetic flat PDF has 3 chapter headings.
    assert audit.headings_detected == 3
    assert audit.paragraphs_detected >= 8
    assert audit.audit_passed


@pytest.mark.slow
def test_real_docling_recovers_subsections(nested_pdf: Path, tmp_path: Path) -> None:
    cfg = load_config(overrides={"mode": "api", "runs_dir": str(tmp_path)})
    working_dir = tmp_path / "wd"
    _seed_manifest(working_dir, nested_pdf)

    stage_01_ingest.run(working_dir, cfg)
    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)

    chapter1 = next(s for s in doc.toc if s.title.startswith("Chapter 1"))
    # Sub-sections detected as nested.
    sub_titles = [s.title for s in chapter1.subsections]
    assert any("Section 1.1" in t or "Intent" in t for t in sub_titles), sub_titles


@pytest.mark.slow
def test_real_docling_records_per_paragraph_pages(synthetic_pdf: Path, tmp_path: Path) -> None:
    cfg = load_config(overrides={"mode": "api", "runs_dir": str(tmp_path)})
    working_dir = tmp_path / "wd"
    _seed_manifest(working_dir, synthetic_pdf)

    stage_01_ingest.run(working_dir, cfg)
    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)

    pages_seen = sorted({p.page_start for _, p in doc.iter_paragraphs()})
    # Three chapters → three pages in the synthetic fixture.
    assert pages_seen == [1, 2, 3], pages_seen


@pytest.mark.slow
def test_real_docling_emits_source_md_with_anchors(synthetic_pdf: Path, tmp_path: Path) -> None:
    cfg = load_config(overrides={"mode": "api", "runs_dir": str(tmp_path)})
    working_dir = tmp_path / "wd"
    _seed_manifest(working_dir, synthetic_pdf)

    stage_01_ingest.run(working_dir, cfg)
    source_md = (working_dir / "01_ingest" / "source.md").read_text()

    assert "# Synthetic" in source_md or "## Chapter 1" in source_md
    # Every paragraph gets an anchor.
    import re

    anchors = re.findall(r"^\^[0-9a-fA-F-]{36}$", source_md, re.MULTILINE)
    assert len(anchors) >= 8
