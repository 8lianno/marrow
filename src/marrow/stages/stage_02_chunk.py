"""Stage 02: chunk + embed (M2 real implementation).

Pipeline:
1. Read CanonicalDocument from stage 01.
2. Plan chunks: paragraph-aligned, sentence-counted, target-token bounded,
   chapter-respecting, with `overlap_pct` fraction of carry-over.
3. Embed via late chunking (Jina v2 if available, stub otherwise).
4. Write chunks.jsonl + LanceDB vector table.

Coverage audit (lossless gate): every paragraph_id from stage 01 MUST appear
in at least one chunk's `paragraph_ids`. Failure emits a warning and blocks
the `_complete` marker (stage status becomes `warning`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from marrow.chunking import PlannedChunk, plan_chunks, split_sentences
from marrow.config import MarrowConfig
from marrow.embed import get_embedder
from marrow.ids import chunk_uuid as derive_chunk_uuid
from marrow.io import read_json, write_jsonl
from marrow.logging import get_logger
from marrow.progress import current as progress_current
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.document import CanonicalDocument
from marrow.schemas.run import StageResult
from marrow.store.vector import VectorStore

log = get_logger(__name__)
STAGE_NAME = "02_chunk"

# ROADMAP M2 budget: ≤ 8 min for 300 pages.
PERF_SECONDS_PER_PAGE_BUDGET = 8 * 60 / 300


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)

    paragraphs = [
        (chapter_path, p.text, str(p.paragraph_id), p.page_start)
        for chapter_path, p in doc.iter_paragraphs()
        if p.text.strip()
    ]
    if not paragraphs:
        warnings.append("no_paragraphs_in_document")

    planned = plan_chunks(
        paragraphs,
        target_tokens=config.chunk.window_tokens,
        overlap_pct=config.chunk.overlap_pct,
    )

    progress = progress_current()
    progress.stage_start(STAGE_NAME, total=max(1, len(planned)), unit="chunk")

    embedder = _get_embedder(config)
    try:
        embeddings = embedder.embed_chunks(_concat_doc_text(paragraphs), planned)
    except Exception as e:
        warnings.append(f"embedding_failed ({type(e).__name__}): {e}; using zero vectors")
        log.warning("embedding_failed_using_zeros", error=str(e))
        embeddings = [[0.0] * embedder.dim for _ in planned]
    # Embedding is one opaque forward pass; fill the bar once it returns.
    progress.stage_advance(len(planned))

    chunks = _to_chunk_records(planned, embeddings, doc.book_slug, embedder.model_name)

    # Lossless coverage audit.
    seen_paragraphs: set[str] = set()
    for c in chunks:
        for pid in c.paragraph_ids:
            seen_paragraphs.add(str(pid))
    expected_paragraphs = {str(pid) for _, _, pid, _ in paragraphs}
    missing = expected_paragraphs - seen_paragraphs
    if missing:
        warnings.append(
            f"chunk_coverage_audit_failed: {len(missing)} paragraph(s) absent from any chunk"
        )

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "chunks.jsonl", chunks)

    vector_store = VectorStore(out_dir / "vectors.lance", dim=embedder.dim)
    written = vector_store.write(chunks)

    elapsed = perf_counter() - t0
    perf_per_page = elapsed / max(1, doc.page_count)
    if perf_per_page > PERF_SECONDS_PER_PAGE_BUDGET:
        warnings.append(
            f"performance_budget_exceeded: {perf_per_page:.2f}s/page > "
            f"{PERF_SECONDS_PER_PAGE_BUDGET:.2f}s/page budget"
        )

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "chunks": len(chunks),
            "vectors_written": written,
            "paragraphs_covered": len(seen_paragraphs),
            "paragraphs_missing": len(missing),
            "embedder_dim": embedder.dim,
        },
        warnings=warnings,
        output_paths=[
            str(out_dir / "chunks.jsonl"),
            str(out_dir / "vectors.lance"),
        ],
    )


def _concat_doc_text(paragraphs: list[tuple[list[str], str, str, int]]) -> str:
    """Build the doc-text view that planned chunks index into.

    Must use the same separator as PlannedChunk.text construction so the
    `doc_text.find(chunk.text)` lookup in JinaLateChunkingEmbedder succeeds.
    """
    return "\n\n".join(p[1] for p in paragraphs)


def _to_chunk_records(
    planned: list[PlannedChunk],
    embeddings: list[list[float]],
    book_slug: str,
    embedding_model: str,
) -> list[ChunkRecord]:
    from uuid import UUID

    out: list[ChunkRecord] = []
    seen_uuids: set[str] = set()
    for chunk, emb in zip(planned, embeddings, strict=True):
        uid = derive_chunk_uuid(chunk.text, book_slug, chunk.chapter_path)
        # Boundary dedup: identical (text, book, chapter) → same UUID; keep first.
        if str(uid) in seen_uuids:
            continue
        seen_uuids.add(str(uid))
        out.append(
            ChunkRecord(
                chunk_uuid=uid,
                book_slug=book_slug,
                text=chunk.text,
                chapter_path=chunk.chapter_path,
                paragraph_ids=[UUID(pid) for pid in chunk.paragraph_ids],
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                token_count=chunk.token_count,
                sentence_count=max(1, sum(len(split_sentences(chunk.text)) for _ in [0])),
                embedding_model=embedding_model,
                embedding=emb,
                window_index=chunk.window_index,
            )
        )
    return out


def _get_embedder(config: MarrowConfig):
    """Resolve embedder from config; honors stub override for CI."""
    model_name = config.chunk.embedding_model
    return get_embedder(model_name)
