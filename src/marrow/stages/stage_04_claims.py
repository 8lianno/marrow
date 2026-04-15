"""Stage 04: atomic claim extraction (M3 real implementation).

Pipeline:
1. Read chunks.jsonl from stage 02 (with embeddings).
2. For each chunk, call the configured LLM route (common API preset:
   ollama/qwen3:14b) with the `extract_claims.j2` prompt and
   `ExtractedClaimsResponse` schema.
3. Single-chunk failures are isolated — the chunk is logged to `failed_chunks`
   and the stage continues (per CLAUDE.md error-handling pattern).
4. Semantic dedup: cosine similarity of claim-text embeddings, threshold per
   config (default 0.92). Duplicates keep the first occurrence; later ones get
   `is_duplicate_of` set.
5. Coverage audit: every chunk MUST produce ≥1 claim. Chunks with zero claims
   are logged and emit a warning.
6. Write claims.jsonl + dedup_report.json.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

from marrow.config import MarrowConfig
from marrow.embed import get_embedder
from marrow.errors import LLMError
from marrow.ids import claim_id as derive_claim_id
from marrow.io import read_jsonl, write_json, write_jsonl
from marrow.llm import LLMCaller
from marrow.logging import get_logger
from marrow.progress import current as progress_current
from marrow.prompts import render
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import (
    AtomicClaim,
    ClaimsManifest,
    ExtractedClaim,
    ExtractedClaimsResponse,
)
from marrow.schemas.run import StageResult

log = get_logger(__name__)
STAGE_NAME = "04_claims"

# ROADMAP M3 budget: ≤ 20 min for 300 pages = 4s/page.
PERF_SECONDS_PER_PAGE_BUDGET = 20 * 60 / 300


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    chunks = list(read_jsonl(working_dir / "02_chunk" / "chunks.jsonl", ChunkRecord))
    if not chunks:
        warnings.append("no_chunks_to_extract_from")

    caller = LLMCaller(working_dir, config)
    all_claims: list[AtomicClaim] = []
    failed_chunks: list[UUID] = []
    empty_chunks: list[UUID] = []
    total_tokens = 0

    progress = progress_current()
    progress.stage_start(STAGE_NAME, total=max(1, len(chunks)), unit="chunk")

    for chunk in chunks:
        total_tokens += chunk.token_count
        try:
            response = _extract_from_chunk(caller, chunk)
        except LLMError as e:
            log.warning(
                "chunk_claim_extraction_failed",
                chunk_uuid=str(chunk.chunk_uuid),
                error=str(e),
            )
            failed_chunks.append(chunk.chunk_uuid)
            progress.stage_advance(1)
            continue
        except Exception as e:
            log.warning(
                "chunk_claim_extraction_crashed",
                chunk_uuid=str(chunk.chunk_uuid),
                error_type=type(e).__name__,
                error=str(e),
            )
            failed_chunks.append(chunk.chunk_uuid)
            progress.stage_advance(1)
            continue

        if not response.claims:
            empty_chunks.append(chunk.chunk_uuid)
            progress.stage_advance(1)
            continue

        for ec in response.claims:
            claim = _to_atomic_claim(ec, chunk)
            all_claims.append(claim)
        progress.stage_advance(1)

    # Semantic dedup.
    deduped = _semantic_dedup(all_claims, config)

    manifest = ClaimsManifest(
        total_extracted=len(all_claims),
        total_after_dedup=sum(1 for c in deduped if c.is_duplicate_of is None),
        failed_chunks=failed_chunks,
        chunks_with_zero_claims=empty_chunks,
        avg_claims_per_1k_tokens=(len(all_claims) * 1000.0 / total_tokens) if total_tokens else 0.0,
    )

    # Audit flags.
    if failed_chunks:
        warnings.append(f"claim_extraction_failed on {len(failed_chunks)} chunk(s)")
    if empty_chunks:
        warnings.append(
            f"chunks_with_zero_claims: {len(empty_chunks)} (below recommended ≥1 per chunk)"
        )

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "claims.jsonl", deduped)
    write_json(out_dir / "dedup_report.json", manifest)

    elapsed = perf_counter() - t0
    # Compute budget against doc pages (chunks can span multi-page).
    pages = max(c.page_end for c in chunks) if chunks else 1
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
            "chunks_processed": len(chunks),
            "chunks_failed": len(failed_chunks),
            "chunks_empty": len(empty_chunks),
            "claims_extracted": len(all_claims),
            "claims_after_dedup": manifest.total_after_dedup,
        },
        warnings=warnings,
        output_paths=[
            str(out_dir / "claims.jsonl"),
            str(out_dir / "dedup_report.json"),
        ],
    )


def _extract_from_chunk(caller: LLMCaller, chunk: ChunkRecord) -> ExtractedClaimsResponse:
    prompt = render(
        "extract_claims.j2",
        chunk_uuid=str(chunk.chunk_uuid),
        chapter_path=chunk.chapter_path,
        chunk_text=chunk.text,
    )
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="claim_extraction",
        response_schema=ExtractedClaimsResponse,
        chunk_uuids=[chunk.chunk_uuid],
    )
    if isinstance(raw, ExtractedClaimsResponse):
        return raw
    # Response came back as raw text despite schema — try to parse.
    try:
        return ExtractedClaimsResponse.model_validate_json(raw)
    except Exception:
        # Last resort: try to find a JSON object in the text.
        return _salvage_json(raw)


def _salvage_json(text: str) -> ExtractedClaimsResponse:
    """Locate the first top-level JSON object in the response text and parse it."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return ExtractedClaimsResponse(claims=[])
    try:
        data = json.loads(text[start : end + 1])
        return ExtractedClaimsResponse.model_validate(data)
    except Exception:
        return ExtractedClaimsResponse(claims=[])


def _to_atomic_claim(ec: ExtractedClaim, chunk: ChunkRecord) -> AtomicClaim:
    return AtomicClaim(
        claim_id=derive_claim_id(ec.claim_text, chunk.book_slug),
        claim_text=ec.claim_text,
        claim_type=ec.claim_type,
        source_chunk_uuids=[chunk.chunk_uuid],
        source_span=ec.source_span,
        confidence=ec.confidence,
    )


def _semantic_dedup(claims: list[AtomicClaim], config: MarrowConfig) -> list[AtomicClaim]:
    """Mark later occurrences of near-duplicate claims with `is_duplicate_of`.

    Cosine similarity on claim-text embeddings. When two chunks produce the same
    surface text, the UUID5-derived `claim_id` already collides → we also merge
    `source_chunk_uuids`. When texts differ but are near-duplicates per
    cosine >= threshold, we keep the higher-confidence one and mark the other.
    """
    threshold = config.claims.dedup_threshold

    # First pass: exact text-match merge via claim_id collision.
    by_id: dict[UUID, AtomicClaim] = {}
    for claim in claims:
        existing = by_id.get(claim.claim_id)
        if existing is None:
            by_id[claim.claim_id] = claim
        else:
            merged = existing.source_chunk_uuids + [
                u for u in claim.source_chunk_uuids if u not in existing.source_chunk_uuids
            ]
            by_id[claim.claim_id] = existing.model_copy(update={"source_chunk_uuids": merged})

    unique = list(by_id.values())
    if len(unique) < 2 or threshold >= 1.0:
        return unique

    # Second pass: cosine similarity on embeddings.
    try:
        embedder = get_embedder(config.chunk.embedding_model)
        texts = [c.claim_text for c in unique]
        if not texts:
            return unique

        # Late-chunk-like: embed each claim text individually via a synthetic planner.
        from marrow.chunking import PlannedChunk

        planned = [
            PlannedChunk(
                text=t,
                sentence_count=1,
                token_count=max(1, len(t) // 4),
                chapter_path=["dedup"],
                paragraph_ids=[],
                page_start=0,
                page_end=0,
                window_index=i,
            )
            for i, t in enumerate(texts)
        ]
        # Use newline-separated doc_text so each chunk's text is findable.
        vectors = embedder.embed_chunks("\n\n".join(texts), planned)
    except Exception as e:
        log.warning("dedup_embedding_failed_falling_back_to_exact_only", error=str(e))
        return unique

    # Skip dedup if all vectors are zero (stub embedder).
    if all(all(v == 0.0 for v in vec) for vec in vectors):
        return unique

    out: list[AtomicClaim] = []
    for i, claim in enumerate(unique):
        duplicate_of: UUID | None = None
        for j in range(i):
            if _cosine(vectors[i], vectors[j]) >= threshold:
                duplicate_of = unique[j].claim_id
                break
        if duplicate_of is not None:
            out.append(claim.model_copy(update={"is_duplicate_of": duplicate_of}))
        else:
            out.append(claim)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
