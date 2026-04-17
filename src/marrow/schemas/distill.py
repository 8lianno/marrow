"""Stage 04 outputs: chapter-by-chapter distillation."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ChapterDistillation(BaseModel):
    """Distilled prose for one chapter."""

    chapter_title: str
    section_id: UUID
    body_md: str
    word_count: int
    continuation_rounds: int = 1


class Distillation(BaseModel):
    """Complete book distillation — all chapters assembled."""

    book_slug: str
    chapters: list[ChapterDistillation] = Field(default_factory=list)
    total_word_count: int = 0
