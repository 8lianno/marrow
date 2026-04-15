"""Stage 01: PDF/EPUB → CanonicalDocument via Docling (M1 real implementation).

Walks Docling's structured items (`SectionHeaderItem`, `TextItem`, `TableItem`)
to reconstruct chapter/section hierarchy with page provenance, then runs a
chapter-detection coverage audit. If Docling fails (e.g., torch/transformers
mismatch), falls back to plain-text pypdf extraction and emits a warning.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from marrow.config import MarrowConfig
from marrow.errors import StageError
from marrow.ids import paragraph_id, section_id
from marrow.io import write_json, write_text
from marrow.logging import get_logger
from marrow.progress import current as progress_current
from marrow.schemas.document import (
    CanonicalDocument,
    ChapterCoverageAudit,
    ParagraphNode,
    SectionNode,
)
from marrow.schemas.run import StageResult
from marrow.slug import book_slug

log = get_logger(__name__)
STAGE_NAME = "01_ingest"

# Performance budget per ROADMAP M1: ≤ 6 min for 300 pages.
PERF_SECONDS_PER_PAGE_BUDGET = 6 * 60 / 300


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    book_path = Path(_book_path_from_manifest(working_dir))
    if not book_path.exists():
        raise StageError(STAGE_NAME, f"Book file disappeared: {book_path}")
    slug = book_slug(book_path)

    progress = progress_current()
    # Page count is unknown until Docling returns; use indeterminate spinner.
    progress.stage_start(STAGE_NAME, total=None, unit="page")

    try:
        doc, audit = _ingest_with_docling(book_path, slug, config)
    except Exception as e:
        warnings.append(f"docling_failed ({type(e).__name__}): {e}; using fallback")
        log.warning("docling_failed_using_fallback", error=str(e))
        doc, audit = _ingest_fallback(book_path, slug)

    # Now we know the page count — extend the bar and fill it.
    progress.stage_extend(doc.page_count)
    progress.stage_advance(doc.page_count)

    if not audit.audit_passed:
        warnings.append(
            f"chapter_coverage_audit_failed: {audit.coverage_pct:.1f}% coverage "
            f"({audit.headings_detected} headings detected vs "
            f"{audit.toc_chapters_declared} ToC entries)"
        )

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "document.json", doc)
    write_json(out_dir / "chapter_coverage_audit.json", audit)
    write_text(out_dir / "source.md", _render_source_md(doc))

    elapsed = perf_counter() - t0
    perf_per_page = elapsed / max(1, doc.page_count)
    if perf_per_page > PERF_SECONDS_PER_PAGE_BUDGET:
        warnings.append(
            f"performance_budget_exceeded: {perf_per_page:.2f}s/page > "
            f"{PERF_SECONDS_PER_PAGE_BUDGET:.2f}s/page budget"
        )

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "chapters": audit.headings_detected,
            "paragraphs": audit.paragraphs_detected,
            "tables": audit.tables_detected,
            "pages": doc.page_count,
            "words": doc.word_count,
        },
        warnings=warnings,
        output_paths=[
            str(out_dir / "document.json"),
            str(out_dir / "chapter_coverage_audit.json"),
            str(out_dir / "source.md"),
        ],
    )


def _book_path_from_manifest(working_dir: Path) -> str:
    from marrow.io import read_json
    from marrow.schemas.run import RunManifest

    manifest = read_json(working_dir / "manifest.json", RunManifest)
    return manifest.book_path


# ---- Docling primary path ----


def _ingest_with_docling(
    book_path: Path, slug: str, config: MarrowConfig
) -> tuple[CanonicalDocument, ChapterCoverageAudit]:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(book_path))
    docling_doc = result.document

    title = _derive_title(book_path, docling_doc)
    sections, tables_count, paragraphs_count = _walk_docling_items(docling_doc, slug)

    page_count = _docling_page_count(docling_doc)
    word_count = sum(len(p.text.split()) for section in sections for p in _iter_paragraphs(section))

    doc = CanonicalDocument(
        book_slug=slug,
        book_title=title,
        book_author=None,
        source_format="pdf" if book_path.suffix.lower() == ".pdf" else "epub",
        source_path=str(book_path.resolve()),
        page_count=page_count,
        word_count=word_count,
        parser=f"docling@{_docling_version()}",
        parser_mode=config.ingest.parser_mode,
        toc=sections,
        extracted_at=datetime.now(UTC),
    )

    audit = _audit_chapter_coverage(
        docling_doc=docling_doc,
        headings_detected=len(sections),
        paragraphs_detected=paragraphs_count,
        tables_detected=tables_count,
    )
    return doc, audit


def _walk_docling_items(docling_doc: Any, slug: str) -> tuple[list[SectionNode], int, int]:
    """Walk Docling's flat item iteration into a hierarchical SectionNode tree.

    Heading levels (item.level on SectionHeaderItem) drive the hierarchy.
    Paragraphs go into the most recent section at the deepest open level.
    """
    from docling_core.types.doc import (  # type: ignore[import-not-found]
        DocItemLabel,
    )

    # Stack of (section, level). Top of stack is current target for paragraphs.
    stack: list[tuple[SectionNode, int]] = []
    top_level: list[SectionNode] = []
    tables_count = 0
    paragraphs_count = 0
    fallback_chapter: SectionNode | None = None

    def attach(section: SectionNode, level: int) -> None:
        nonlocal stack, top_level
        # Pop sections at >= this level.
        while stack and stack[-1][1] >= level:
            stack.pop()
        if stack:
            stack[-1][0].subsections.append(section)
        else:
            top_level.append(section)
        stack.append((section, level))

    def current_chapter_path() -> list[str]:
        return [s.title for s, _ in stack] or ["Body"]

    def current_target() -> SectionNode:
        nonlocal fallback_chapter
        if stack:
            return stack[-1][0]
        if fallback_chapter is None:
            fallback_chapter = SectionNode(
                section_id=section_id("Body", 1, []), title="Body", level=1
            )
            top_level.append(fallback_chapter)
            stack.append((fallback_chapter, 1))
        return fallback_chapter

    for item, _iter_level in docling_doc.iterate_items():
        label = getattr(item, "label", None)
        text = (getattr(item, "text", "") or "").strip()
        page_no = _item_page_no(item)

        if label == DocItemLabel.SECTION_HEADER:
            docling_level = max(1, int(getattr(item, "level", 1) or 1))
            heading_level = _refine_heading_level(text, docling_level)
            section = SectionNode(
                section_id=section_id(text or "Untitled", heading_level, current_chapter_path()),
                title=text or "Untitled",
                level=heading_level,
            )
            attach(section, heading_level)
            continue

        if label == DocItemLabel.TITLE and not stack:
            # Treat the document title as a synthetic top-level chapter.
            section = SectionNode(
                section_id=section_id(text or "Title", 1, []),
                title=text or "Title",
                level=1,
            )
            attach(section, 1)
            continue

        if label == DocItemLabel.TABLE:
            grid = _table_to_grid(item)
            target = current_target()
            chapter_path = current_chapter_path()
            target.paragraphs.append(
                ParagraphNode(
                    paragraph_id=paragraph_id(
                        f"<table:{len(target.paragraphs)}>", chapter_path, page_no
                    ),
                    text=_table_to_text(grid),
                    page_start=page_no,
                    page_end=page_no,
                    is_table=True,
                    table_grid=grid,
                )
            )
            tables_count += 1
            paragraphs_count += 1
            continue

        if not text:
            continue

        is_footnote = label == DocItemLabel.FOOTNOTE if hasattr(DocItemLabel, "FOOTNOTE") else False

        target = current_target()
        chapter_path = current_chapter_path()
        target.paragraphs.append(
            ParagraphNode(
                paragraph_id=paragraph_id(text, chapter_path, page_no),
                text=text,
                page_start=page_no,
                page_end=page_no,
                is_footnote=is_footnote,
            )
        )
        paragraphs_count += 1

    return top_level, tables_count, paragraphs_count


_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:section\s+|sub\s+|appendix\s+)?(\d+(?:\.\d+)*)", re.IGNORECASE
)


def _refine_heading_level(text: str, docling_level: int) -> int:
    """Refine Docling's heading level using textual conventions.

    Docling's RT-DETR layout model often flattens all headings to level 1 when
    visual hierarchy is weak (small font deltas, no bookmarks). We promote
    'Chapter N' / 'Part N' to level 1 and demote 'Section N.M' / 'N.M.K' to a
    deeper level based on the dot count.
    """
    if not text:
        return docling_level

    lowered = text.strip().lower()

    if lowered.startswith(("chapter ", "part ")) or lowered in {
        "introduction",
        "preface",
        "epilogue",
    }:
        return 1

    m = _NUMBERED_HEADING_RE.match(text)
    if m:
        # "1.1" → 2 dots count + 1; "1" → 1
        return max(1, min(m.group(1).count(".") + 1, 6))

    return docling_level


def _item_page_no(item: Any) -> int:
    prov = getattr(item, "prov", None)
    if prov:
        return int(getattr(prov[0], "page_no", 1))
    return 1


def _table_to_grid(item: Any) -> list[list[str]]:
    """Best-effort table → grid conversion. Docling's TableItem schema varies."""
    data = getattr(item, "data", None)
    if data is None:
        return []
    table_cells = getattr(data, "table_cells", None) or getattr(data, "grid", None)
    if not table_cells:
        return []
    try:
        # data.grid is typically list[list[TableCell]]
        return [[str(getattr(cell, "text", cell) or "") for cell in row] for row in table_cells]
    except Exception:
        return []


def _table_to_text(grid: list[list[str]]) -> str:
    if not grid:
        return "[empty table]"
    return "\n".join(" | ".join(row) for row in grid)


def _docling_page_count(docling_doc: Any) -> int:
    pages = getattr(docling_doc, "pages", None)
    if isinstance(pages, dict):
        return len(pages)
    if isinstance(pages, list):
        return len(pages)
    num_pages = getattr(docling_doc, "num_pages", None)
    if callable(num_pages):
        try:
            return int(num_pages())
        except Exception:
            pass
    if isinstance(num_pages, int):
        return num_pages
    return 1


def _audit_chapter_coverage(
    *,
    docling_doc: Any,
    headings_detected: int,
    paragraphs_detected: int,
    tables_detected: int,
) -> ChapterCoverageAudit:
    """Compare Docling's detected headings to ToC entries when present.

    No ToC: headings are ground truth → coverage 100% if any heading detected.
    ToC present: coverage = headings_detected / toc_entries (capped at 100).
    """
    toc = _docling_toc_entries(docling_doc)
    declared = len(toc)

    if declared == 0:
        coverage_pct = 100.0 if headings_detected > 0 else 0.0
        passed = headings_detected > 0
    else:
        coverage_pct = min(100.0, 100.0 * headings_detected / declared)
        passed = coverage_pct >= 100.0

    return ChapterCoverageAudit(
        toc_chapters_declared=declared,
        headings_detected=headings_detected,
        paragraphs_detected=paragraphs_detected,
        tables_detected=tables_detected,
        coverage_pct=coverage_pct,
        skipped_pages=[],
        audit_passed=passed,
    )


def _docling_toc_entries(docling_doc: Any) -> list[str]:
    """Best-effort extraction of declared ToC entries from Docling/PDF metadata."""
    # Docling exposes outline/bookmarks via document.bookmarks or similar; fall back to [].
    for attr in ("bookmarks", "outline", "toc"):
        v = getattr(docling_doc, attr, None)
        if v:
            try:
                return [str(getattr(e, "title", e)) for e in v]
            except Exception:
                continue
    return []


def _derive_title(book_path: Path, docling_doc: Any) -> str:
    title = getattr(docling_doc, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    return book_path.stem.replace("-", " ").replace("_", " ").title()


def _docling_version() -> str:
    try:
        import docling

        return getattr(docling, "__version__", "unknown")
    except Exception:
        return "unknown"


# ---- Fallback path (Docling unavailable) ----


def _ingest_fallback(book_path: Path, slug: str) -> tuple[CanonicalDocument, ChapterCoverageAudit]:
    text = _extract_plain_text(book_path)
    sections = _heuristic_split_sections(text, slug)

    paragraph_count = sum(len(s.paragraphs) for s in sections)
    word_count = sum(len(p.text.split()) for s in sections for p in s.paragraphs)

    doc = CanonicalDocument(
        book_slug=slug,
        book_title=book_path.stem.replace("-", " ").title(),
        source_format="pdf" if book_path.suffix.lower() == ".pdf" else "epub",
        source_path=str(book_path.resolve()),
        page_count=max(1, _estimate_pages(text)),
        word_count=word_count,
        parser="fallback@plain-text",
        parser_mode="text_only",
        toc=sections,
        extracted_at=datetime.now(UTC),
    )

    audit = ChapterCoverageAudit(
        toc_chapters_declared=0,
        headings_detected=len(sections),
        paragraphs_detected=paragraph_count,
        tables_detected=0,
        coverage_pct=0.0 if not sections else 100.0,
        audit_passed=bool(sections),
    )
    return doc, audit


def _extract_plain_text(book_path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(book_path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        try:
            return book_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""


def _heuristic_split_sections(text: str, slug: str) -> list[SectionNode]:
    """Detect chapter headings in plain text via leading 'Chapter N:' patterns."""
    chapter_re = re.compile(r"^\s*(chapter\s+\d+[:.]?\s*.*)$", re.IGNORECASE | re.MULTILINE)
    matches = list(chapter_re.finditer(text))

    if not matches:
        return [_single_body_section(text)]

    sections: list[SectionNode] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        chapter_path = [title]
        paragraphs = [
            ParagraphNode(
                paragraph_id=paragraph_id(p.strip(), chapter_path, 1),
                text=p.strip(),
                page_start=1,
                page_end=1,
            )
            for p in body.split("\n\n")
            if p.strip()
        ]
        sections.append(
            SectionNode(
                section_id=section_id(title, 1, []),
                title=title,
                level=1,
                paragraphs=paragraphs,
            )
        )
    return sections


def _single_body_section(text: str) -> SectionNode:
    chapter_path = ["Body"]
    paragraphs = [
        ParagraphNode(
            paragraph_id=paragraph_id(p, chapter_path, 1),
            text=p,
            page_start=1,
            page_end=1,
        )
        for p in text.split("\n\n")
        if p.strip()
    ]
    return SectionNode(
        section_id=section_id("Body", 1, []),
        title="Body",
        level=1,
        paragraphs=paragraphs,
    )


def _estimate_pages(text: str) -> int:
    return max(1, len(text) // 2400)  # ~250 words/page x ~10 chars/word


# ---- Source.md rendering ----


def _iter_paragraphs(section: SectionNode):
    yield from section.paragraphs
    for sub in section.subsections:
        yield from _iter_paragraphs(sub)


def _render_source_md(doc: CanonicalDocument) -> str:
    lines: list[str] = [f"# {doc.book_title}", ""]

    def render_section(section: SectionNode, depth: int) -> None:
        lines.append(f"{'#' * min(depth + 1, 6)} {section.title}")
        lines.append("")
        for p in section.paragraphs:
            if p.is_table and p.table_grid:
                lines.append("| " + " | ".join(p.table_grid[0]) + " |")
                lines.append("|" + "|".join("---" for _ in p.table_grid[0]) + "|")
                for row in p.table_grid[1:]:
                    lines.append("| " + " | ".join(row) + " |")
            else:
                lines.append(p.text)
            lines.append(f"^{p.paragraph_id}")
            lines.append("")
        for sub in section.subsections:
            render_section(sub, depth + 1)

    for section in doc.toc:
        render_section(section, 1)

    return "\n".join(lines).rstrip() + "\n"
