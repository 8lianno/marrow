"""Stage 01 outputs: the canonical document tree."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ParagraphNode(BaseModel):
    paragraph_id: UUID
    text: str
    page_start: int
    page_end: int
    is_footnote: bool = False
    is_table: bool = False
    table_grid: list[list[str]] | None = None


class SectionNode(BaseModel):
    section_id: UUID
    title: str
    level: int  # 1=chapter, 2=section, 3=subsection
    paragraphs: list[ParagraphNode] = Field(default_factory=list)
    subsections: list[SectionNode] = Field(default_factory=list)


class CanonicalDocument(BaseModel):
    book_slug: str
    book_title: str
    book_author: str | None = None
    source_format: Literal["pdf", "epub"]
    source_path: str
    page_count: int
    word_count: int
    parser: str
    parser_mode: Literal["auto", "force_ocr", "text_only"] = "auto"
    toc: list[SectionNode] = Field(default_factory=list)
    skipped_pages: list[int] = Field(default_factory=list)
    extracted_at: datetime

    def iter_paragraphs(self) -> list[tuple[list[str], ParagraphNode]]:
        """Yield (chapter_path, paragraph) pairs in document order."""
        out: list[tuple[list[str], ParagraphNode]] = []

        def walk(section: SectionNode, parent_path: list[str]) -> None:
            path = [*parent_path, section.title]
            for p in section.paragraphs:
                out.append((path, p))
            for sub in section.subsections:
                walk(sub, path)

        for top in self.toc:
            walk(top, [])
        return out


SectionNode.model_rebuild()


class ChapterCoverageAudit(BaseModel):
    """US-001 acceptance gate: 100% chapter detection.

    `toc_chapters_detected` / `headings_detected` come from Docling's structured
    output. `coverage_pct` should be ≥ 100% when no ToC was present (we count
    headings as ground truth in that case). When a ToC IS present, coverage is
    ToC entries divided by detected headings.
    """

    toc_chapters_declared: int  # from PDF/EPUB ToC if present
    headings_detected: int  # SectionHeaderItem count
    paragraphs_detected: int
    tables_detected: int
    coverage_pct: float
    skipped_pages: list[int] = Field(default_factory=list)
    audit_passed: bool
