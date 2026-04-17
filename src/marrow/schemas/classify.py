"""Stage 02 outputs: section type classification."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SectionType = Literal["intro", "body", "conclusion", "appendix", "foreword", "other"]

# Default compression ratios per section type.
COMPRESSION_RATIOS: dict[SectionType, float] = {
    "intro": 0.12,
    "body": 0.30,
    "conclusion": 0.12,
    "appendix": 0.70,
    "foreword": 0.10,
    "other": 0.20,
}


class SectionClassification(BaseModel):
    section_id: UUID
    title: str
    section_type: SectionType = "body"
    compression_ratio: float = 0.30


class BookClassification(BaseModel):
    book_slug: str
    sections: list[SectionClassification] = Field(default_factory=list)
