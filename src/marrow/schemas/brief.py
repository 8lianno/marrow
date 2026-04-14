"""Stages 05/05b/06a outputs: brief draft, quiz, evaluation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

# Internal in-memory citation marker. Export translates this to Obsidian wikilinks.
CITATION_PATTERN = re.compile(r"\[chunk:([0-9a-fA-F-]{36})\]")


class BriefSection(BaseModel):
    section_id: UUID
    title: str
    level: int
    body_md: str
    cited_chunk_uuids: list[UUID] = Field(default_factory=list)
    subsections: list[BriefSection] = Field(default_factory=list)

    @classmethod
    def parse_citations(cls, body_md: str) -> list[UUID]:
        return [UUID(m.group(1)) for m in CITATION_PATTERN.finditer(body_md)]


BriefSection.model_rebuild()


class ChapterSynthesisResponse(BaseModel):
    """LLM output schema for chapter-level synthesis."""

    title: str
    body_md: str


# ---- M6: validation + evaluation LLM response schemas ----


class GeneratedQuestion(BaseModel):
    question_text: str
    expected_answer: str
    leaf_level: QuizLeafLevel
    is_grounded: bool = True


class GeneratedQuiz(BaseModel):
    """LLM output: quiz questions for one source chunk."""

    questions: list[GeneratedQuestion] = Field(default_factory=list)


class QuizAnswerResponse(BaseModel):
    """LLM output: an attempt to answer a quiz question from the brief."""

    answer: str
    answered_from_brief: bool  # False = brief did not cover this


class QuizGrade(BaseModel):
    """LLM output: graded judgement of whether `answer` matches `expected`."""

    is_correct: bool
    rationale: str


class CoherenceScore(BaseModel):
    """LLM output: BooookScore-style coherence rating per chapter, 0.0-1.0."""

    score: float = Field(ge=0.0, le=1.0)
    rationale: str


class FactVerification(BaseModel):
    """LLM output: FActScore-style verification of one cited claim against source."""

    is_supported: bool
    rationale: str


class BriefDraft(BaseModel):
    draft_version: int = 0
    book_slug: str
    book_title: str
    sections: list[BriefSection]
    word_count: int
    estimated_page_count: int
    citation_density: float
    generated_at: datetime
    iteration_history: list[str] = Field(default_factory=list)


QuizLeafLevel = Literal["date", "name", "number", "definition", "causal", "example"]


class QuizQuestion(BaseModel):
    question_id: UUID
    chapter_path: list[str]
    question_text: str
    expected_answer: str
    source_chunk_uuids: list[UUID]
    leaf_level: QuizLeafLevel
    is_grounded: bool


class QuizResult(BaseModel):
    iteration: int
    total_questions: int
    grounded_questions: int
    answered_correctly: int
    failed_question_ids: list[UUID] = Field(default_factory=list)
    regenerated_section_ids: list[UUID] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pass_rate(self) -> float:
        if self.grounded_questions == 0:
            return 0.0
        return self.answered_correctly / self.grounded_questions


class EvaluationReport(BaseModel):
    book_slug: str
    brief_version: int
    booookscore: float
    factscore: float
    factscore_length_penalty_applied: bool
    hamlet_root_recall: float
    hamlet_branch_recall: float
    hamlet_leaf_recall: float
    composite_score: float
    verdict: Literal["PASS", "FAIL"]
    failure_reasons: list[str] = Field(default_factory=list)
    evaluated_at: datetime
