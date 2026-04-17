"""Stage-result and run-manifest schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

StageStatus = Literal["success", "warning", "failed"]
RunStatus = Literal["in_progress", "success", "failed", "partial"]


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
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    status: RunStatus
    config: dict[str, Any]  # serialized MarrowConfig (avoids forward-ref cycle)
    stage_results: list[StageResult] = Field(default_factory=list)
    cost_breakdown: CostBreakdown = Field(default_factory=CostBreakdown)
    final_output_path: str | None = None
    marrow_version: str
