"""Stage-result and run-manifest schemas, plus host-mode task/result envelopes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

StageStatus = Literal["success", "warning", "failed"]
RunStatus = Literal["in_progress", "success", "failed", "partial"]
RunMode = Literal["host", "api"]


class StageResult(BaseModel):
    stage_name: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    status: StageStatus
    counts: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    output_paths: list[str] = Field(default_factory=list)


class CostBreakdown(BaseModel):
    by_stage: dict[str, float] = Field(default_factory=dict)
    by_model_role: dict[str, float] = Field(default_factory=dict)
    total_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0


class RunManifest(BaseModel):
    book_slug: str
    book_path: str
    mode: RunMode
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    status: RunStatus
    config: dict[str, Any]  # serialized MarrowConfig (avoids forward-ref cycle)
    stage_results: list[StageResult] = Field(default_factory=list)
    cost_breakdown: CostBreakdown = Field(default_factory=CostBreakdown)
    final_brief_path: str | None = None
    final_evaluation_path: str | None = None
    marrow_version: str


# ---- Host-mode task/result envelopes (file-based protocol) ----


class HostTask(BaseModel):
    """Written by Marrow when a stage needs LLM reasoning in Host Mode.

    The host agent reads the task, performs reasoning using its own tokens,
    writes a HostResult to runs/<slug>/host_results/<task_id>.json.
    """

    task_id: UUID
    stage: str
    model_role: str
    prompt: str
    response_schema: dict[str, Any] | None = None
    chunk_uuids: list[UUID] = Field(default_factory=list)
    max_input_tokens: int
    max_output_tokens: int
    created_at: datetime


class HostResult(BaseModel):
    task_id: UUID
    response: Any
    estimated_tokens_in: int = 0
    estimated_tokens_out: int = 0
    completed_at: datetime
