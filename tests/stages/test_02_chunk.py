"""M2 acceptance tests for stage_02_chunk."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from marrow.chunking import (
    PlannedChunk,
    approx_token_count,
    plan_chunks,
    split_sentences,
)
from marrow.config import load_config
from marrow.embed import StubEmbedder, get_embedder
from marrow.io import read_jsonl, write_json
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.run import RunManifest
from marrow.stages import stage_01_ingest, stage_02_chunk
from marrow.store.vector import VectorStore

# ---- chunking primitives ----


def test_split_sentences_basic() -> None:
    text = "Hello world. This is a test! Is it? Yes."
    out = split_sentences(text)
    assert len(out) == 4
    assert out[0] == "Hello world."


def test_approx_token_count_grows_with_text() -> None:
    assert approx_token_count("a") < approx_token_count("a longer sentence here")


def test_plan_chunks_paragraph_aligned_within_budget() -> None:
    paragraphs = [
        (["Ch 1"], "First paragraph.", "00000000-0000-0000-0000-000000000001", 1),
        (["Ch 1"], "Second paragraph here.", "00000000-0000-0000-0000-000000000002", 1),
        (
            ["Ch 1"],
            "Third paragraph still in chapter one.",
            "00000000-0000-0000-0000-000000000003",
            2,
        ),
    ]
    chunks = plan_chunks(paragraphs, target_tokens=1000, overlap_pct=0.0)
    assert len(chunks) == 1
    assert chunks[0].sentence_count >= 3
    assert chunks[0].page_start == 1 and chunks[0].page_end == 2


def test_plan_chunks_respects_chapter_boundary() -> None:
    paragraphs = [
        (["Chapter 1"], "Para one.", "00000000-0000-0000-0000-000000000001", 1),
        (["Chapter 2"], "Para two.", "00000000-0000-0000-0000-000000000002", 2),
    ]
    chunks = plan_chunks(paragraphs, target_tokens=1000, overlap_pct=0.0)
    # Chapter boundary forces a split even when both fit in budget.
    assert len(chunks) == 2
    assert chunks[0].chapter_path == ["Chapter 1"]
    assert chunks[1].chapter_path == ["Chapter 2"]


def test_plan_chunks_overlap_carries_paragraphs() -> None:
    # Paragraphs ~10 tokens each → 6 per 64-token-budget chunk; 50% overlap
    # should carry 3 paragraphs into the next chunk.
    paragraphs = [
        (["Ch 1"], f"para body {i} done.", f"00000000-0000-0000-0000-{i:012d}", 1)
        for i in range(1, 13)
    ]
    chunks = plan_chunks(paragraphs, target_tokens=64, overlap_pct=0.5)
    assert len(chunks) >= 2
    assert any(
        set(chunks[i].paragraph_ids) & set(chunks[i + 1].paragraph_ids)
        for i in range(len(chunks) - 1)
    ), [c.paragraph_ids for c in chunks]


# ---- embedder ----


def test_stub_embedder_returns_zero_vectors_of_correct_dim() -> None:
    e = StubEmbedder(dim=768)
    chunks = [
        PlannedChunk(
            text="hello",
            sentence_count=1,
            token_count=1,
            chapter_path=["Ch"],
            paragraph_ids=[],
            page_start=1,
            page_end=1,
            window_index=0,
        )
    ]
    out = e.embed_chunks("hello", chunks)
    assert len(out) == 1
    assert len(out[0]) == 768
    assert all(v == 0.0 for v in out[0])


def test_get_embedder_returns_stub_for_stub_name() -> None:
    e = get_embedder("stub")
    assert isinstance(e, StubEmbedder)


# ---- LanceDB roundtrip ----


def _sample_chunk(uid_byte: int = 1) -> ChunkRecord:
    return ChunkRecord(
        chunk_uuid=UUID(int=uid_byte),
        book_slug="test",
        text="Sample chunk text.",
        chapter_path=["Body"],
        paragraph_ids=[UUID(int=100)],
        page_start=1,
        page_end=1,
        token_count=4,
        sentence_count=1,
        embedding_model="stub",
        embedding=[0.0] * 768,
        window_index=0,
    )


def test_vector_store_writes_chunks(tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "vectors.lance", dim=768)
    chunks = [_sample_chunk(i) for i in range(1, 4)]
    written = store.write(chunks)
    assert written == 3
    # Either lancedb or jsonl fallback exists.
    assert (tmp_path / "vectors.lance").exists() or (tmp_path / "vectors.jsonl").exists()


# ---- Stage integration ----


def _seed_manifest(working_dir: Path, book_path: Path) -> None:
    working_dir.mkdir(parents=True, exist_ok=True)
    manifest = RunManifest(
        book_slug="synthetic",
        book_path=str(book_path.resolve()),
        mode="api",
        started_at=datetime.now(UTC),
        status="in_progress",
        config={},
        marrow_version="test",
    )
    write_json(working_dir / "manifest.json", manifest)


@pytest.mark.slow
def test_stage_02_chunk_with_stub_embedder_after_real_ingest(
    synthetic_pdf: Path, tmp_path: Path
) -> None:
    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path),
            "chunk": {"embedding_model": "stub", "window_tokens": 128, "overlap_pct": 0.0},
        }
    )
    working_dir = tmp_path / "wd"
    _seed_manifest(working_dir, synthetic_pdf)

    stage_01_ingest.run(working_dir, cfg)
    result = stage_02_chunk.run(working_dir, cfg)

    assert result.counts["chunks"] >= 3  # 3 chapters → at least 3 chunks at 128-token budget
    assert result.counts["paragraphs_missing"] == 0
    assert result.counts["embedder_dim"] == 768

    chunks = list(read_jsonl(working_dir / "02_chunk" / "chunks.jsonl", ChunkRecord))
    assert all(len(c.embedding) == 768 for c in chunks)
    assert all(c.embedding_model == "stub" for c in chunks)


def test_stage_02_chunk_determinism_across_two_runs(tmp_path: Path) -> None:
    """Re-running on the same document yields byte-identical chunk UUIDs."""
    from marrow.ids import paragraph_id, section_id
    from marrow.schemas.document import CanonicalDocument, ParagraphNode, SectionNode

    chapter_path = ["Body"]
    paragraphs = [
        ParagraphNode(
            paragraph_id=paragraph_id(text, chapter_path, 1),
            text=text,
            page_start=1,
            page_end=1,
        )
        for text in [
            "All warfare is based on deception.",
            "Hence, when capable of attacking, feign incapacity.",
            "When near, appear far; when far, appear near.",
        ]
    ]
    doc = CanonicalDocument(
        book_slug="determinism-test",
        book_title="Determinism Test",
        source_format="pdf",
        source_path=str(tmp_path / "fake.pdf"),
        page_count=1,
        word_count=30,
        parser="test",
        toc=[
            SectionNode(
                section_id=section_id("Body", 1, []),
                title="Body",
                level=1,
                paragraphs=paragraphs,
            )
        ],
        extracted_at=datetime.now(UTC),
    )

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path),
            "chunk": {"embedding_model": "stub", "window_tokens": 1000, "overlap_pct": 0.0},
        }
    )

    def run_once(subdir: str) -> list[str]:
        wd = tmp_path / subdir
        wd.mkdir()
        write_json(
            wd / "manifest.json",
            {
                "book_slug": "determinism-test",
                "book_path": str(tmp_path / "fake.pdf"),
                "mode": "api",
                "started_at": datetime.now(UTC).isoformat(),
                "status": "in_progress",
                "config": {},
                "marrow_version": "test",
                "stage_results": [],
                "cost_breakdown": {},
            },
        )
        (wd / "01_ingest").mkdir()
        write_json(wd / "01_ingest" / "document.json", doc)
        stage_02_chunk.run(wd, cfg)
        return [
            str(c.chunk_uuid) for c in read_jsonl(wd / "02_chunk" / "chunks.jsonl", ChunkRecord)
        ]

    uuids_1 = run_once("run1")
    uuids_2 = run_once("run2")
    assert uuids_1 == uuids_2, "Chunk UUIDs must be byte-identical across re-runs"


@pytest.mark.slow
@pytest.mark.network
def test_real_jina_embedder_produces_768d_nonzero_vectors() -> None:
    """End-to-end real Jina path: model loads, embeddings are non-degenerate."""
    e = get_embedder("jinaai/jina-embeddings-v2-base-en")
    chunks = [
        PlannedChunk(
            text="The cat sat on the mat.",
            sentence_count=1,
            token_count=6,
            chapter_path=["Body"],
            paragraph_ids=[],
            page_start=1,
            page_end=1,
            window_index=0,
        ),
        PlannedChunk(
            text="Quantum entanglement defies classical intuition.",
            sentence_count=1,
            token_count=8,
            chapter_path=["Body"],
            paragraph_ids=[],
            page_start=1,
            page_end=1,
            window_index=0,
        ),
    ]
    doc_text = "\n\n".join(c.text for c in chunks)
    embeddings = e.embed_chunks(doc_text, chunks)
    assert len(embeddings) == 2
    assert all(len(v) == 768 for v in embeddings)
    # Non-degenerate: at least one non-zero component.
    assert any(any(v) for v in embeddings)
    # Different texts → different vectors.
    assert embeddings[0] != embeddings[1]
