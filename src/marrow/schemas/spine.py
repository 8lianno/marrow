"""Stage 03 outputs: the structural skeleton (spine) of the book."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class Framework(BaseModel):
    """A named model, method, principle, or schema the author introduces."""

    name: str = ""
    description: str = ""
    paragraph_ids: list[UUID] = Field(default_factory=list)


class Example(BaseModel):
    """A load-bearing example the author returns to or builds on."""

    label: str = ""
    gist: str = ""
    concept_illustrated: str = ""
    paragraph_ids: list[UUID] = Field(default_factory=list)
    is_load_bearing: bool = True


class KeyTerm(BaseModel):
    """Vocabulary the author defines or uses in a technical sense."""

    term: str = ""
    definition: str = ""
    paragraph_ids: list[UUID] = Field(default_factory=list)


class ChapterSpine(BaseModel):
    """Structural skeleton of one chapter — what MUST survive distillation."""

    chapter_title: str = ""
    section_id: UUID = Field(default_factory=lambda: __import__("uuid").uuid4())
    thesis: str = ""
    frameworks: list[Framework] = Field(default_factory=list)
    key_examples: list[Example] = Field(default_factory=list)
    argumentative_moves: list[str] = Field(default_factory=list)
    key_terms: list[KeyTerm] = Field(default_factory=list)
    voice_sample: str = ""
    source_word_count: int = 0
    target_word_count: int = 0


class Spine(BaseModel):
    """Collection of chapter spines for the entire book."""

    book_slug: str
    book_title: str
    chapters: list[ChapterSpine] = Field(default_factory=list)
    total_source_words: int = 0
    total_target_words: int = 0
