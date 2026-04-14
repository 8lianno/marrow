"""Stage 05: hierarchical synthesis (M5 real implementation).

Pipeline:
1. Read CanonicalDocument (01), ChunkRecord[] (02), CommunityRecord[] (03),
   AtomicClaim[] (04).
2. Build chunk→chapter index from the document tree.
3. Group claims by chapter. Pair each chapter with any communities whose
   chunks overlap that chapter (context for coherence, not citable).
4. For each chapter: render `synthesize_chapter.j2` → call LLM with
   `ChapterSynthesisResponse` schema → parse citations from body_md.
5. Assemble BriefDraft. Compute word_count, estimated_page_count, citation_density.
6. Write merge_tree.json — the audit trail of what claims/communities went
   into which section (so the validator + evaluator can verify coverage).

Compression target: `config.synthesize.target_pages` ± tolerance. The per-chapter
word budget divides target_words proportionally by input claim volume so
chapter-length stays balanced.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from marrow.config import MarrowConfig
from marrow.errors import LLMError
from marrow.ids import section_id as derive_section_id
from marrow.io import read_json, read_jsonl, write_json
from marrow.llm import LLMCaller
from marrow.logging import get_logger
from marrow.prompts import render
from marrow.schemas.brief import (
    BriefDraft,
    BriefSection,
    ChapterSynthesisResponse,
)
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import AtomicClaim
from marrow.schemas.document import CanonicalDocument
from marrow.schemas.graph import CommunityRecord
from marrow.schemas.run import StageResult

log = get_logger(__name__)
STAGE_NAME = "05_synthesize"

# ROADMAP M5 budget: ≤ 25 min for 300 pages = 5s/page.
PERF_SECONDS_PER_PAGE_BUDGET = 25 * 60 / 300

# A target page ≈ 250 words.
WORDS_PER_PAGE = 250


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)
    chunks = list(read_jsonl(working_dir / "02_chunk" / "chunks.jsonl", ChunkRecord))
    communities = list(read_jsonl(working_dir / "03_graph" / "communities.jsonl", CommunityRecord))
    all_claims = list(read_jsonl(working_dir / "04_claims" / "claims.jsonl", AtomicClaim))

    # Drop claims that are duplicates (post-dedup).
    claims = [c for c in all_claims if c.is_duplicate_of is None]

    claims_by_chapter = _group_claims_by_chapter(claims, chunks)
    comms_by_chapter = _group_communities_by_chapter(communities, chunks)

    # Compression budget.
    target_total_words = config.synthesize.target_pages * WORDS_PER_PAGE
    total_input_claims = sum(len(v) for v in claims_by_chapter.values()) or 1

    caller = LLMCaller(working_dir, config)
    sections: list[BriefSection] = []
    merge_tree: dict[str, dict] = {}
    failed_chapters: list[str] = []

    for chapter_title in _chapter_order(doc):
        chapter_claims = claims_by_chapter.get(chapter_title, [])
        if not chapter_claims:
            continue

        chapter_comms = comms_by_chapter.get(chapter_title, [])
        # Allocate this chapter's word budget proportional to its claim share.
        share = len(chapter_claims) / total_input_claims
        chapter_target_words = max(150, int(share * target_total_words))

        try:
            response = _synthesize_chapter(
                caller, chapter_title, chapter_claims, chapter_comms, chapter_target_words
            )
        except LLMError as e:
            log.warning("chapter_synthesis_failed", chapter=chapter_title, error=str(e))
            failed_chapters.append(chapter_title)
            continue
        except Exception as e:  # isolate per-chapter crashes
            log.warning(
                "chapter_synthesis_crashed",
                chapter=chapter_title,
                error_type=type(e).__name__,
                error=str(e),
            )
            failed_chapters.append(chapter_title)
            continue

        cited = BriefSection.parse_citations(response.body_md)
        section = BriefSection(
            section_id=derive_section_id(response.title or chapter_title, 1, []),
            title=response.title or chapter_title,
            level=1,
            body_md=response.body_md,
            cited_chunk_uuids=cited,
        )
        sections.append(section)

        # Coverage audit per chapter: every claim's chunk_uuid should appear in citations.
        expected_chunk_uuids = {u for c in chapter_claims for u in c.source_chunk_uuids}
        missing = expected_chunk_uuids - set(cited)
        merge_tree[chapter_title] = {
            "section_id": str(section.section_id),
            "input_claims": len(chapter_claims),
            "input_communities": len(chapter_comms),
            "output_word_count": len(response.body_md.split()),
            "target_words": chapter_target_words,
            "citations_found": len(cited),
            "expected_chunks": len(expected_chunk_uuids),
            "missing_chunks": [str(u) for u in sorted(missing, key=str)],
        }
        if missing:
            warnings.append(
                f"chapter_{_slug(chapter_title)}_missing_citations_for_{len(missing)}_chunks"
            )

    if failed_chapters:
        warnings.append(f"chapter_synthesis_failed on {len(failed_chapters)} chapter(s)")

    # Assemble draft.
    word_count = sum(len(s.body_md.split()) for s in sections)
    citation_count = sum(len(s.cited_chunk_uuids) for s in sections)
    paragraph_count = sum(max(1, s.body_md.count("\n\n") + 1) for s in sections)
    draft = BriefDraft(
        draft_version=0,
        book_slug=doc.book_slug,
        book_title=doc.book_title,
        sections=sections,
        word_count=word_count,
        estimated_page_count=max(1, word_count // WORDS_PER_PAGE),
        citation_density=(citation_count / paragraph_count) if paragraph_count else 0.0,
        generated_at=datetime.now(UTC),
    )

    # Compression audit.
    target = config.synthesize.target_pages
    tolerance = config.synthesize.page_tolerance
    if draft.estimated_page_count > target + tolerance:
        warnings.append(
            f"compression_over_target: {draft.estimated_page_count}pp > "
            f"{target + tolerance}pp (target {target}±{tolerance})"
        )
    elif draft.estimated_page_count < max(1, target - tolerance):
        warnings.append(
            f"compression_under_target: {draft.estimated_page_count}pp < "
            f"{target - tolerance}pp (target {target}±{tolerance})"
        )

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "draft_brief.json", draft)
    write_json(
        out_dir / "merge_tree.json",
        {
            "strategy": "flat-chapter-hierarchical",
            "target_total_words": target_total_words,
            "sections": merge_tree,
        },
    )

    elapsed = perf_counter() - t0
    pages = doc.page_count or 1
    if elapsed / pages > PERF_SECONDS_PER_PAGE_BUDGET:
        warnings.append(
            f"performance_budget_exceeded: {elapsed / pages:.2f}s/page > "
            f"{PERF_SECONDS_PER_PAGE_BUDGET:.2f}s/page"
        )

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "chapters_synthesized": len(sections),
            "chapters_failed": len(failed_chapters),
            "total_claims_in": len(claims),
            "word_count": word_count,
            "estimated_pages": draft.estimated_page_count,
        },
        warnings=warnings,
        output_paths=[
            str(out_dir / "draft_brief.json"),
            str(out_dir / "merge_tree.json"),
        ],
    )


# ---- LLM call ----


def _synthesize_chapter(
    caller: LLMCaller,
    chapter_title: str,
    claims: list[AtomicClaim],
    communities: list[CommunityRecord],
    target_words: int,
) -> ChapterSynthesisResponse:
    prompt = render(
        "synthesize_chapter.j2",
        chapter_title=chapter_title,
        claims=claims,
        communities=communities,
        target_words=target_words,
    )
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="synthesis",
        response_schema=ChapterSynthesisResponse,
    )
    if isinstance(raw, ChapterSynthesisResponse):
        return raw
    try:
        return ChapterSynthesisResponse.model_validate_json(raw)
    except Exception:
        return _salvage_synthesis_json(raw, chapter_title)


def _salvage_synthesis_json(text: str, fallback_title: str) -> ChapterSynthesisResponse:
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return ChapterSynthesisResponse.model_validate(json.loads(text[start : end + 1]))
        except Exception:
            pass
    # Last resort: treat the whole text as body_md.
    return ChapterSynthesisResponse(title=fallback_title, body_md=text.strip()[:8000])


# ---- Grouping helpers ----


def _chapter_order(doc: CanonicalDocument) -> list[str]:
    ordered = [s.title for s in doc.toc]
    # Body is the implicit fallback chapter when nothing else matches.
    if "Body" not in ordered:
        ordered.append("Body")
    return ordered


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower())[:40]


def _group_claims_by_chapter(
    claims: list[AtomicClaim],
    chunks: list[ChunkRecord],
) -> dict[str, list[AtomicClaim]]:
    chunk_to_chapter = {
        c.chunk_uuid: (c.chapter_path[0] if c.chapter_path else "Body") for c in chunks
    }
    by_chapter: dict[str, list[AtomicClaim]] = defaultdict(list)
    for claim in claims:
        chapter = next(
            (chunk_to_chapter[u] for u in claim.source_chunk_uuids if u in chunk_to_chapter),
            "Body",
        )
        by_chapter[chapter].append(claim)
    return by_chapter


def _group_communities_by_chapter(
    communities: list[CommunityRecord],
    chunks: list[ChunkRecord],
) -> dict[str, list[CommunityRecord]]:
    chunk_to_chapter = {
        c.chunk_uuid: (c.chapter_path[0] if c.chapter_path else "Body") for c in chunks
    }
    by_chapter: dict[str, list[CommunityRecord]] = defaultdict(list)
    for comm in communities:
        if comm.is_orphan_bucket:
            continue
        chapter_votes: dict[str, int] = defaultdict(int)
        for u in comm.chunk_uuids:
            chapter_votes[chunk_to_chapter.get(u, "Body")] += 1
        if chapter_votes:
            top_chapter = max(chapter_votes.items(), key=lambda kv: kv[1])[0]
            by_chapter[top_chapter].append(comm)
    return by_chapter
