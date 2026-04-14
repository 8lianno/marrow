"""Stage 04 outputs: atomic claims and dedup manifest."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ClaimType = Literal["factual", "definitional", "argumentative", "causal", "statistical"]


class AtomicClaim(BaseModel):
    claim_id: UUID
    claim_text: str
    claim_type: ClaimType
    source_chunk_uuids: list[UUID] = Field(min_length=1)
    source_span: str
    confidence: float = Field(ge=0.0, le=1.0)
    entities_referenced: list[UUID] = Field(default_factory=list)
    is_duplicate_of: UUID | None = None


class ClaimsManifest(BaseModel):
    total_extracted: int
    total_after_dedup: int
    failed_chunks: list[UUID] = Field(default_factory=list)
    avg_claims_per_1k_tokens: float
    chunks_with_zero_claims: list[UUID] = Field(default_factory=list)


class ExtractedClaim(BaseModel):
    """LLM output schema for a single extracted claim (no UUID or chunk attribution yet)."""

    claim_text: str
    claim_type: ClaimType
    source_span: str
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedClaimsResponse(BaseModel):
    """LLM response schema for claim extraction on one chunk."""

    claims: list[ExtractedClaim] = Field(default_factory=list)
