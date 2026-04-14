"""Pydantic schemas crossing stage boundaries."""

from marrow.schemas.brief import (
    BriefDraft,
    BriefSection,
    ChapterSynthesisResponse,
    CoherenceScore,
    EvaluationReport,
    FactVerification,
    GeneratedQuestion,
    GeneratedQuiz,
    QuizAnswerResponse,
    QuizGrade,
    QuizQuestion,
    QuizResult,
)
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import (
    AtomicClaim,
    ClaimsManifest,
    ExtractedClaim,
    ExtractedClaimsResponse,
)
from marrow.schemas.document import (
    CanonicalDocument,
    ChapterCoverageAudit,
    ParagraphNode,
    SectionNode,
)
from marrow.schemas.graph import (
    CommunityRecord,
    CommunitySummaryResponse,
    CoverageAudit,
    EntityRecord,
    ExtractedEntity,
    ExtractedGraphResponse,
    ExtractedRelationship,
    RelationshipRecord,
)
from marrow.schemas.run import (
    CostBreakdown,
    HostResult,
    HostTask,
    RunManifest,
    StageResult,
)

__all__ = [
    "AtomicClaim",
    "BriefDraft",
    "BriefSection",
    "CanonicalDocument",
    "ChapterCoverageAudit",
    "ChapterSynthesisResponse",
    "ChunkRecord",
    "ClaimsManifest",
    "CoherenceScore",
    "CommunityRecord",
    "CommunitySummaryResponse",
    "CostBreakdown",
    "CoverageAudit",
    "EntityRecord",
    "EvaluationReport",
    "ExtractedClaim",
    "ExtractedClaimsResponse",
    "ExtractedEntity",
    "ExtractedGraphResponse",
    "ExtractedRelationship",
    "FactVerification",
    "GeneratedQuestion",
    "GeneratedQuiz",
    "HostResult",
    "HostTask",
    "ParagraphNode",
    "QuizAnswerResponse",
    "QuizGrade",
    "QuizQuestion",
    "QuizResult",
    "RelationshipRecord",
    "RunManifest",
    "SectionNode",
    "StageResult",
]
