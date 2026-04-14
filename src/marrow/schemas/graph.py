"""Stage 03 outputs: entities, relationships, communities, coverage audit."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

EntityType = Literal["person", "concept", "place", "org", "framework", "event", "other"]


class EntityRecord(BaseModel):
    entity_id: UUID
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    entity_type: EntityType
    description: str
    chunk_uuids: list[UUID] = Field(default_factory=list)
    importance: float = Field(ge=0.0, le=1.0)


class RelationshipRecord(BaseModel):
    relation_id: UUID
    subject_entity_id: UUID
    predicate: str
    object_entity_id: UUID
    chunk_uuids: list[UUID] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class CommunityRecord(BaseModel):
    community_id: UUID
    level: int
    title: str
    summary: str
    entity_ids: list[UUID] = Field(default_factory=list)
    chunk_uuids: list[UUID] = Field(default_factory=list)
    is_orphan_bucket: bool = False


class CoverageAudit(BaseModel):
    total_chunks: int
    chunks_in_communities: int
    orphan_chunk_uuids: list[UUID] = Field(default_factory=list)
    coverage_pct: float
    orphan_bucket_created: bool


# ---- LLM response schemas ----


class ExtractedEntity(BaseModel):
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    entity_type: EntityType
    description: str
    importance: float = Field(ge=0.0, le=1.0)


class ExtractedRelationship(BaseModel):
    subject_canonical_name: str
    predicate: str
    object_canonical_name: str
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedGraphResponse(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)


class CommunitySummaryResponse(BaseModel):
    title: str
    summary: str
