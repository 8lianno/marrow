"""Stage 02 outputs: chunk records with embeddings."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ChunkRecord(BaseModel):
    chunk_uuid: UUID
    book_slug: str
    text: str
    chapter_path: list[str]
    paragraph_ids: list[UUID] = Field(default_factory=list)
    page_start: int
    page_end: int
    token_count: int
    sentence_count: int
    embedding_model: str
    embedding: list[float] = Field(default_factory=list)
    window_index: int
