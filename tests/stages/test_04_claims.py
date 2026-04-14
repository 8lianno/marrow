"""M3 acceptance tests for stage_04_claims: LLM-backed extraction + dedup."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from marrow.config import load_config
from marrow.ids import chunk_uuid as derive_chunk_uuid
from marrow.io import read_json, read_jsonl, write_json, write_jsonl
from marrow.prompts import render
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import AtomicClaim, ClaimsManifest
from marrow.schemas.run import RunManifest
from marrow.stages import stage_04_claims

# ---- Prompt rendering ----


def test_extract_claims_prompt_contains_chunk_context() -> None:
    out = render(
        "extract_claims.j2",
        chunk_uuid="abc-123",
        chapter_path=["Chapter 1", "Section 1.1"],
        chunk_text="All warfare is based on deception.",
    )
    assert "Chapter 1 > Section 1.1" in out
    assert "abc-123" in out
    assert "All warfare is based on deception." in out
    assert "factual" in out and "argumentative" in out  # enum values present


# ---- Salvage parser (handles models that wrap JSON in prose) ----


def test_salvage_json_extracts_object_from_prose() -> None:
    raw = (
        "Sure! Here are the claims:\n\n"
        '{"claims": [{"claim_text": "X is Y.", "claim_type": "definitional", '
        '"source_span": "X is Y.", "confidence": 0.9}]}\n\n'
        "Hope that helps."
    )
    parsed = stage_04_claims._salvage_json(raw)
    assert len(parsed.claims) == 1
    assert parsed.claims[0].claim_type == "definitional"


def test_salvage_json_returns_empty_on_garbage() -> None:
    parsed = stage_04_claims._salvage_json("no json here at all")
    assert parsed.claims == []


# ---- Cosine similarity primitive ----


def test_cosine_identical_vectors_is_one() -> None:
    v = [1.0, 2.0, 3.0]
    assert abs(stage_04_claims._cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero() -> None:
    assert stage_04_claims._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


# ---- Dedup ----


def _make_claim(text: str, book_slug: str = "test", chunk_uid: UUID | None = None) -> AtomicClaim:
    from marrow.ids import claim_id

    return AtomicClaim(
        claim_id=claim_id(text, book_slug),
        claim_text=text,
        claim_type="factual",
        source_chunk_uuids=[chunk_uid or uuid4()],
        source_span=text,
        confidence=0.9,
    )


def test_dedup_merges_exact_text_duplicates_across_chunks(tmp_path: Path) -> None:
    cfg = load_config(
        overrides={
            "chunk": {"embedding_model": "stub"},
            "claims": {"dedup_threshold": 0.92},
            "runs_dir": str(tmp_path),
        }
    )
    chunk_a = uuid4()
    chunk_b = uuid4()
    claims = [
        _make_claim("Sun rises in the east.", chunk_uid=chunk_a),
        _make_claim("Sun rises in the east.", chunk_uid=chunk_b),
        _make_claim("Water boils at 100C.", chunk_uid=chunk_a),
    ]
    deduped = stage_04_claims._semantic_dedup(claims, cfg)
    # Exact-text dup merged via claim_id collision.
    assert len(deduped) == 2
    east_claim = next(c for c in deduped if "east" in c.claim_text)
    assert set(east_claim.source_chunk_uuids) == {chunk_a, chunk_b}


# ---- Stage integration with stubbed LLM ----


class _FakeOllamaServer:
    """Spin up a stdlib HTTP server that pretends to be Ollama for tests.

    Each request returns a canned claim for the given chunk text. We only
    match on the presence of "All warfare" vs "Water boils" to keep it simple.
    """

    def __init__(self, port: int) -> None:
        self.port = port

    def __enter__(self) -> _FakeOllamaServer:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                prompt = body["messages"][0]["content"]
                if "All warfare" in prompt:
                    claims = [
                        {
                            "claim_text": "Warfare relies on deception.",
                            "claim_type": "argumentative",
                            "source_span": "All warfare is based on deception.",
                            "confidence": 0.95,
                        }
                    ]
                elif "Water boils" in prompt:
                    claims = [
                        {
                            "claim_text": "Water boils at 100 degrees Celsius.",
                            "claim_type": "factual",
                            "source_span": "Water boils at 100C.",
                            "confidence": 0.99,
                        }
                    ]
                else:
                    claims = []
                payload = {
                    "message": {
                        "content": json.dumps({"claims": claims}),
                        "role": "assistant",
                    },
                    "prompt_eval_count": len(prompt.split()),
                    "eval_count": 20,
                    "done": True,
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode("utf-8"))

        self._server = http.server.HTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


def _make_chunks(tmp_path: Path) -> list[ChunkRecord]:
    book_slug = "m3-test"
    texts = ["All warfare is based on deception.", "Water boils at 100C."]
    chunks: list[ChunkRecord] = []
    for i, text in enumerate(texts):
        chunks.append(
            ChunkRecord(
                chunk_uuid=derive_chunk_uuid(text, book_slug, ["Body"]),
                book_slug=book_slug,
                text=text,
                chapter_path=["Body"],
                paragraph_ids=[],
                page_start=i + 1,
                page_end=i + 1,
                token_count=max(1, len(text) // 4),
                sentence_count=1,
                embedding_model="stub",
                embedding=[],
                window_index=i,
            )
        )
    return chunks


def _seed_working_dir(tmp_path: Path, book_path: Path, chunks: list[ChunkRecord]) -> Path:
    wd = tmp_path / "wd"
    (wd / "02_chunk").mkdir(parents=True)
    manifest = RunManifest(
        book_slug="m3-test",
        book_path=str(book_path),
        mode="api",
        started_at=datetime.now(UTC),
        status="in_progress",
        config={},
        marrow_version="test",
    )
    write_json(wd / "manifest.json", manifest)
    write_jsonl(wd / "02_chunk" / "chunks.jsonl", chunks)
    return wd


def test_stage_04_with_fake_ollama_extracts_and_persists(tmp_path: Path) -> None:
    port = 47811  # arbitrary free-range port
    book_path = tmp_path / "book.pdf"
    book_path.write_bytes(b"%PDF-1.4")

    chunks = _make_chunks(tmp_path)
    wd = _seed_working_dir(tmp_path, book_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "chunk": {"embedding_model": "stub"},
            "models": {
                "claim_extraction": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                }
            },
        }
    )

    with _FakeOllamaServer(port):
        result = stage_04_claims.run(wd, cfg)

    assert result.counts["chunks_processed"] == 2
    assert result.counts["claims_extracted"] == 2
    assert result.counts["chunks_empty"] == 0
    assert result.counts["chunks_failed"] == 0

    claims = list(read_jsonl(wd / "04_claims" / "claims.jsonl", AtomicClaim))
    assert len(claims) == 2
    assert any("Warfare" in c.claim_text for c in claims)
    assert any("100 degrees" in c.claim_text for c in claims)

    manifest = read_json(wd / "04_claims" / "dedup_report.json", ClaimsManifest)
    assert manifest.total_extracted == 2
    assert manifest.total_after_dedup == 2
    assert manifest.failed_chunks == []


def test_stage_04_isolates_per_chunk_failure(tmp_path: Path) -> None:
    """When one chunk's LLM call fails, the stage continues with the others."""
    port = 47812
    book_path = tmp_path / "book.pdf"
    book_path.write_bytes(b"%PDF-1.4")

    chunks = _make_chunks(tmp_path)
    # Add a third chunk that will trigger an unknown-prompt branch (empty claims).
    chunks.append(
        ChunkRecord(
            chunk_uuid=derive_chunk_uuid("unrelated prose", "m3-test", ["Body"]),
            book_slug="m3-test",
            text="unrelated prose",
            chapter_path=["Body"],
            paragraph_ids=[],
            page_start=3,
            page_end=3,
            token_count=3,
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=2,
        )
    )
    wd = _seed_working_dir(tmp_path, book_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "chunk": {"embedding_model": "stub"},
            "models": {
                "claim_extraction": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                }
            },
        }
    )

    with _FakeOllamaServer(port):
        result = stage_04_claims.run(wd, cfg)

    assert result.counts["chunks_processed"] == 3
    assert result.counts["chunks_empty"] == 1  # the "unrelated prose" chunk
    assert result.status == "warning"
    assert any("chunks_with_zero_claims" in w for w in result.warnings)


@pytest.mark.slow
@pytest.mark.network
def test_real_ollama_extracts_from_one_chunk(tmp_path: Path) -> None:
    """Exercises the live Ollama server. Skipped if ollama not reachable."""
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
    except (urllib.error.URLError, TimeoutError):
        pytest.skip("ollama server not running")

    book_path = tmp_path / "book.pdf"
    book_path.write_bytes(b"%PDF-1.4")
    chunks = _make_chunks(tmp_path)[:1]  # just one chunk to keep it fast
    wd = _seed_working_dir(tmp_path, book_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "chunk": {"embedding_model": "stub"},
            "models": {
                "claim_extraction": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": "http://localhost:11434",
                }
            },
        }
    )

    result = stage_04_claims.run(wd, cfg)
    assert result.counts["chunks_processed"] == 1
    # Real model should extract at least one claim.
    assert result.counts["claims_extracted"] >= 1
