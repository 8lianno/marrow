"""Stage 01: PDF/EPUB → CanonicalDocument via Docling (M1 real implementation).

Walks Docling's structured items (`SectionHeaderItem`, `TextItem`, `TableItem`)
to reconstruct chapter/section hierarchy with page provenance, then runs a
chapter-detection coverage audit. If Docling fails (e.g., torch/transformers
mismatch), falls back to plain-text pypdf extraction and emits a warning.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any
from zipfile import ZipFile

from marrow.config import MarrowConfig
from marrow.errors import StageError
from marrow.ids import paragraph_id, section_id
from marrow.io import write_json, write_text
from marrow.logging import get_logger
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

    try:
        doc, audit = _ingest_with_docling(book_path, slug, config)
    except Exception as e:
        warnings.append(f"docling_failed ({type(e).__name__}): {e}; using fallback")
        log.warning("docling_failed_using_fallback", error=str(e))
        doc, audit = _ingest_fallback(book_path, slug)

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


def _epub_spine_sections(book_path: Path, slug: str) -> list[SectionNode]:
    """Split EPUB by spine items, extracting headings and paragraphs from HTML."""
    try:
        from bs4 import BeautifulSoup

        with ZipFile(book_path) as zf:
            paths = _epub_spine_paths(zf)
            sections: list[SectionNode] = []

            for path in paths:
                try:
                    raw = zf.read(path)
                except KeyError:
                    continue

                soup = BeautifulSoup(raw, "html.parser")
                for tag in soup(["script", "style", "svg"]):
                    tag.decompose()

                # Extract heading
                heading = None
                for tag in soup.find_all(["h1", "h2", "h3"]):
                    candidate = _normalize_inline_whitespace(tag.get_text(" ", strip=True))
                    if candidate and len(candidate) < 120:
                        heading = candidate
                        tag.decompose()  # remove from body so it's not repeated
                        break
                title = heading or PurePosixPath(path).stem.replace("-", " ").replace("_", " ").title()

                # Extract paragraphs from <p> tags
                chapter_path = [title]
                paragraphs: list[ParagraphNode] = []
                for p_tag in soup.find_all("p"):
                    text = _normalize_inline_whitespace(p_tag.get_text(" ", strip=True))
                    if text and len(text) > 10:
                        paragraphs.append(
                            ParagraphNode(
                                paragraph_id=paragraph_id(text, chapter_path, 1),
                                text=text,
                                page_start=1,
                                page_end=1,
                            )
                        )

                # Fallback: if no <p> tags, split by newlines
                if not paragraphs:
                    plain = soup.get_text("\n", strip=True)
                    for line in plain.split("\n"):
                        line = _normalize_inline_whitespace(line)
                        if line and len(line) > 10:
                            paragraphs.append(
                                ParagraphNode(
                                    paragraph_id=paragraph_id(line, chapter_path, 1),
                                    text=line,
                                    page_start=1,
                                    page_end=1,
                                )
                            )

                if not paragraphs or sum(len(p.text.split()) for p in paragraphs) < 30:
                    continue

                sections.append(
                    SectionNode(
                        section_id=section_id(title, 1, []),
                        title=title,
                        level=1,
                        paragraphs=paragraphs,
                    )
                )
            return sections
    except Exception:
        return []


_WORD_NUMBERS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
)

_CHAPTER_HEADING_RE = re.compile(
    r"^(?:chapter\s+(?:\d+|" + _WORD_NUMBERS + r"))(?:\s.*)?$",
    re.IGNORECASE,
)
_SECTION_HEADING_RE = re.compile(
    r"^(?:introduction|preface|prologue|epilogue|conclusion|afterword)$",
    re.IGNORECASE,
)


def _split_paragraphs_by_chapter(
    paragraphs: list[ParagraphNode], slug: str
) -> list[SectionNode]:
    """Re-split a flat list of paragraphs into sections by detecting chapter headings.

    Handles poorly-structured EPUBs where the TOC lists chapter names as short
    paragraphs, then the body text flows continuously. Strategy:
    1. Scan for heading-like paragraphs (short, matching chapter/intro/epilogue patterns)
    2. Skip TOC region (cluster of consecutive headings)
    3. Each heading starts a new section; body paragraphs go into the current section
    """
    sections: list[SectionNode] = []
    current_title: str | None = None
    current_paras: list[ParagraphNode] = []

    # First pass: find all heading positions
    heading_indices: list[int] = []
    for i, p in enumerate(paragraphs):
        text = p.text.strip()
        if _CHAPTER_HEADING_RE.match(text) or _SECTION_HEADING_RE.match(text):
            heading_indices.append(i)

    # Detect TOC: a heading is a TOC entry if the next heading comes within a few
    # paragraphs AND there's no substantial content between them. A heading followed
    # by substantial content (>100 words before the next heading) is a real chapter start.
    toc_indices: set[int] = set()
    for j, idx in enumerate(heading_indices):
        next_heading = heading_indices[j + 1] if j + 1 < len(heading_indices) else len(paragraphs)
        # Count words between this heading and the next
        words_between = sum(
            len(paragraphs[k].text.split())
            for k in range(idx + 1, min(next_heading, len(paragraphs)))
        )
        if words_between < 100:
            toc_indices.add(idx)

    body_heading_indices = set(heading_indices) - toc_indices

    for i, p in enumerate(paragraphs):
        text = p.text.strip()

        if i in toc_indices:
            continue  # skip TOC entries

        if i in body_heading_indices:
            # Save previous section
            if current_title and current_paras:
                sections.append(
                    SectionNode(
                        section_id=section_id(current_title, 1, []),
                        title=current_title,
                        level=1,
                        paragraphs=current_paras,
                    )
                )
            current_title = text
            current_paras = []
        elif current_title:
            if len(text.split()) > 5:  # skip very short decorative lines
                current_paras.append(p)
        elif len(text.split()) > 20 and not current_title:
            # Content before first heading → "Front Matter"
            current_title = "Front Matter"
            current_paras.append(p)

    # Save the last section
    if current_title and current_paras:
        sections.append(
            SectionNode(
                section_id=section_id(current_title, 1, []),
                title=current_title,
                level=1,
                paragraphs=current_paras,
            )
        )

    # If we ended up with very few large sections, auto-split them into
    # ~5000-word chunks so that LLM calls don't hit token limits.
    final_sections: list[SectionNode] = []
    for s in sections:
        wc = sum(len(p.text.split()) for p in s.paragraphs)
        if wc > 8000:
            parts = _split_large_section(s, target_words=5000)
            final_sections.extend(parts)
        else:
            final_sections.append(s)

    return final_sections


def _split_large_section(section: SectionNode, target_words: int = 5000) -> list[SectionNode]:
    """Split a large section into sub-sections of ~target_words at paragraph boundaries."""
    parts: list[SectionNode] = []
    current_paras: list[ParagraphNode] = []
    current_words = 0
    part_num = 1

    for p in section.paragraphs:
        pw = len(p.text.split())
        current_paras.append(p)
        current_words += pw

        if current_words >= target_words:
            title = f"{section.title} (Part {part_num})"
            parts.append(
                SectionNode(
                    section_id=section_id(title, 1, []),
                    title=title,
                    level=1,
                    paragraphs=current_paras,
                )
            )
            current_paras = []
            current_words = 0
            part_num += 1

    if current_paras:
        title = f"{section.title} (Part {part_num})" if part_num > 1 else section.title
        parts.append(
            SectionNode(
                section_id=section_id(title, 1, []),
                title=title,
                level=1,
                paragraphs=current_paras,
            )
        )

    return parts


def _build_fallback_doc(
    book_path: Path,
    slug: str,
    sections: list[SectionNode],
    title: str | None,
    author: str | None,
) -> tuple[CanonicalDocument, ChapterCoverageAudit]:
    paragraph_count = sum(len(s.paragraphs) for s in sections)
    word_count = sum(len(p.text.split()) for s in sections for p in s.paragraphs)
    total_text = " ".join(p.text for s in sections for p in s.paragraphs)

    doc = CanonicalDocument(
        book_slug=slug,
        book_title=title or book_path.stem.replace("-", " ").title(),
        book_author=author,
        source_format="epub",
        source_path=str(book_path.resolve()),
        page_count=max(1, _estimate_pages(total_text)),
        word_count=word_count,
        parser="fallback@epub-spine",
        parser_mode="text_only",
        toc=sections,
        extracted_at=datetime.now(UTC),
    )

    audit = ChapterCoverageAudit(
        toc_chapters_declared=0,
        headings_detected=len(sections),
        paragraphs_detected=paragraph_count,
        tables_detected=0,
        coverage_pct=100.0 if sections else 0.0,
        audit_passed=bool(sections),
    )
    return doc, audit


def _ingest_fallback(book_path: Path, slug: str) -> tuple[CanonicalDocument, ChapterCoverageAudit]:
    # For EPUBs, extract paragraphs from HTML then re-split by chapter headings.
    if book_path.suffix.lower() == ".epub":
        spine_sections = _epub_spine_sections(book_path, slug)

        # If spine items have real chapter titles (not "Index Split"), use directly
        has_real_titles = any(
            not s.title.lower().startswith(("index split", "document outline"))
            and len(s.title) > 3
            for s in spine_sections
        )
        if len(spine_sections) >= 3 and has_real_titles:
            title, author = _extract_fallback_metadata(book_path)
            return _build_fallback_doc(book_path, slug, spine_sections, title, author)

        # Otherwise: merge all paragraphs and re-split by chapter headings
        if spine_sections:
            all_paragraphs = [p for s in spine_sections for p in s.paragraphs]
            sections = _split_paragraphs_by_chapter(all_paragraphs, slug)
            if len(sections) > 1:
                title, author = _extract_fallback_metadata(book_path)
                return _build_fallback_doc(book_path, slug, sections, title, author)

    text = _extract_plain_text(book_path)
    sections = _heuristic_split_sections(text, slug)
    title, author = _extract_fallback_metadata(book_path)

    paragraph_count = sum(len(s.paragraphs) for s in sections)
    word_count = sum(len(p.text.split()) for s in sections for p in s.paragraphs)

    doc = CanonicalDocument(
        book_slug=slug,
        book_title=title or book_path.stem.replace("-", " ").title(),
        book_author=author,
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
    if book_path.suffix.lower() == ".epub":
        return _extract_epub_plain_text(book_path)

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(book_path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        try:
            return book_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""


def _extract_fallback_metadata(book_path: Path) -> tuple[str | None, str | None]:
    if book_path.suffix.lower() != ".epub":
        return None, None

    try:
        with ZipFile(book_path) as zf:
            opf_path = _epub_package_path(zf)
            if not opf_path:
                return None, None

            from bs4 import BeautifulSoup

            soup = BeautifulSoup(zf.read(opf_path), "xml")
            title_tag = soup.find(["dc:title", "title"])
            author_tag = soup.find(["dc:creator", "creator"])
            title = (
                _normalize_inline_whitespace(title_tag.get_text(" ", strip=True))
                if title_tag is not None
                else None
            )
            author = (
                _normalize_inline_whitespace(author_tag.get_text(" ", strip=True))
                if author_tag is not None
                else None
            )
            return title or None, author or None
    except Exception:
        return None, None


def _extract_epub_plain_text(book_path: Path) -> str:
    try:
        with ZipFile(book_path) as zf:
            parts = [_extract_epub_html_text(zf, path) for path in _epub_spine_paths(zf)]
            non_empty = [part for part in parts if part.strip()]
            if non_empty:
                return "\n\n".join(non_empty)
    except Exception:
        pass

    try:
        return book_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _epub_spine_paths(zf: ZipFile) -> list[str]:
    opf_path = _epub_package_path(zf)
    if not opf_path:
        return _fallback_epub_html_paths(zf)

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(zf.read(opf_path), "xml")
    manifest: dict[str, str] = {}
    for item in soup.find_all("item"):
        item_id = item.get("id")
        href = item.get("href")
        media_type = item.get("media-type", "")
        if item_id and href and "html" in media_type:
            manifest[item_id] = href

    base = PurePosixPath(opf_path).parent
    paths: list[str] = []
    for itemref in soup.find_all("itemref"):
        idref = itemref.get("idref")
        href = manifest.get(idref or "")
        if href:
            paths.append((base / href).as_posix())

    return paths or _fallback_epub_html_paths(zf)


def _epub_package_path(zf: ZipFile) -> str | None:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(zf.read("META-INF/container.xml"), "xml")
        rootfile = soup.find("rootfile")
        full_path = rootfile.get("full-path") if rootfile is not None else None
        return full_path or None
    except Exception:
        return None


def _fallback_epub_html_paths(zf: ZipFile) -> list[str]:
    html_suffixes = (".xhtml", ".html", ".htm")
    return sorted(
        name
        for name in zf.namelist()
        if name.lower().endswith(html_suffixes) and not name.startswith("META-INF/")
    )


def _extract_epub_html_text(zf: ZipFile, path: str) -> str:
    from bs4 import BeautifulSoup

    try:
        raw = zf.read(path)
    except KeyError:
        return ""

    soup = BeautifulSoup(raw, "xml")
    for tag in soup(["script", "style", "svg"]):
        tag.decompose()

    lines = [_normalize_inline_whitespace(line) for line in soup.get_text("\n", strip=True).splitlines()]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned)


def _normalize_inline_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _heuristic_split_sections(text: str, slug: str) -> list[SectionNode]:
    """Detect chapter headings in plain text via common heading patterns."""
    # Match: "Chapter 1:", "Chapter One", "CHAPTER THREE", "Introduction", "Epilogue", "Part 2"
    chapter_re = re.compile(
        r"^\s*((?:chapter|part)\s+(?:\d+|" + _WORD_NUMBERS + r")[:.]?\s*.*"
        r"|introduction|preface|prologue|epilogue|conclusion|afterword"
        r")$",
        re.IGNORECASE | re.MULTILINE,
    )
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
