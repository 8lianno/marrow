"""Stage 05 outputs: coherence audit report."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MissingSpineItem(BaseModel):
    chapter_title: str
    item_type: Literal["framework", "example", "key_term", "argumentative_move"]
    item_description: str
    severity: Literal["critical", "minor"] = "minor"


class VoiceDrift(BaseModel):
    chapter_title: str
    description: str


class BrokenThread(BaseModel):
    description: str
    chapters_involved: list[str] = Field(default_factory=list)


class Redundancy(BaseModel):
    description: str
    chapters_involved: list[str] = Field(default_factory=list)


class CoherenceReport(BaseModel):
    missing_spine_items: list[MissingSpineItem] = Field(default_factory=list)
    voice_drift: list[VoiceDrift] = Field(default_factory=list)
    broken_threads: list[BrokenThread] = Field(default_factory=list)
    redundancies: list[Redundancy] = Field(default_factory=list)
    overall_pass: bool = True
