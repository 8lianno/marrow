"""M4 acceptance tests for stage_03_graph: entities, relationships, communities, coverage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from marrow.config import load_config
from marrow.ids import chunk_uuid as derive_chunk_uuid
from marrow.io import read_json, read_jsonl, write_json, write_jsonl
from marrow.prompts import render
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.graph import (
    CommunityRecord,
    CoverageAudit,
    EntityRecord,
    ExtractedEntity,
    RelationshipRecord,
)
from marrow.schemas.run import RunManifest
from marrow.stages import stage_03_graph

# ---- Prompt rendering ----


def test_extract_graph_prompt_contains_chunk_and_schema() -> None:
    out = render(
        "extract_graph.j2",
        chunk_uuid="abc-123",
        chapter_path=["Chapter 1"],
        chunk_text="Sun Tzu wrote The Art of War.",
    )
    assert "Chapter 1" in out
    assert "Sun Tzu wrote The Art of War." in out
    assert "canonical_name" in out and "entity_type" in out


def test_summarize_community_prompt_lists_entities() -> None:
    entities = [
        EntityRecord(
            entity_id=UUID(int=1),
            canonical_name="Deception",
            aliases=["Trickery"],
            entity_type="concept",
            description="The core tactical principle.",
            chunk_uuids=[UUID(int=100)],
            importance=0.95,
        ),
    ]
    out = render("summarize_community.j2", entities=entities, relationships=[])
    assert "Deception" in out
    assert "concept" in out
    assert "The core tactical principle." in out


# ---- Entity merging ----


def test_normalize_handles_whitespace_and_case() -> None:
    assert stage_03_graph._normalize("  Sun Tzu  ") == "sun tzu"
    assert stage_03_graph._normalize("Sun  Tzu") == "sun tzu"
    assert stage_03_graph._normalize("SUN TZU") == "sun tzu"


def test_merge_entity_unions_chunks_aliases_and_takes_max_importance() -> None:
    existing = EntityRecord(
        entity_id=UUID(int=1),
        canonical_name="Sun Tzu",
        aliases=["Master Sun"],
        entity_type="person",
        description="Author of The Art of War.",
        chunk_uuids=[UUID(int=100)],
        importance=0.7,
    )
    new = ExtractedEntity(
        canonical_name="Sun Tzu",
        aliases=["孫子", "Master Sun"],
        entity_type="person",
        description="Chinese military strategist.",
        importance=0.9,
    )
    stage_03_graph._merge_entity(existing, new, UUID(int=200))
    assert UUID(int=100) in existing.chunk_uuids
    assert UUID(int=200) in existing.chunk_uuids
    assert "孫子" in existing.aliases
    # Duplicate aliases not added twice.
    assert existing.aliases.count("Master Sun") == 1
    assert existing.importance == 0.9  # took the max


# ---- Community detection ----


def test_detect_communities_clusters_connected_components() -> None:
    entities = [
        EntityRecord(
            entity_id=UUID(int=i),
            canonical_name=f"E{i}",
            aliases=[],
            entity_type="concept",
            description="x",
            chunk_uuids=[UUID(int=i + 100)],
            importance=0.5,
        )
        for i in range(1, 6)
    ]
    # Two disconnected pairs: (E1,E2) (E3,E4); E5 isolated.
    relationships = [
        RelationshipRecord(
            relation_id=UUID(int=10),
            subject_entity_id=UUID(int=1),
            predicate="rel",
            object_entity_id=UUID(int=2),
            chunk_uuids=[UUID(int=101)],
            confidence=0.8,
        ),
        RelationshipRecord(
            relation_id=UUID(int=11),
            subject_entity_id=UUID(int=3),
            predicate="rel",
            object_entity_id=UUID(int=4),
            chunk_uuids=[UUID(int=103)],
            confidence=0.8,
        ),
    ]
    _graph, communities = stage_03_graph._detect_communities(entities, relationships)
    # Expect at least 2 communities (Louvain may split further).
    assert len(communities) >= 2


# ---- Salvage parser ----


def test_salvage_graph_json_extracts_from_prose() -> None:
    raw = (
        "Here is the graph:\n"
        '{"entities": [{"canonical_name": "X", "aliases": [], "entity_type": "concept", '
        '"description": "test", "importance": 0.5}], "relationships": []}'
    )
    parsed = stage_03_graph._salvage_graph_json(raw)
    assert len(parsed.entities) == 1
    assert parsed.entities[0].canonical_name == "X"


# ---- Fake Ollama server that returns graph responses ----


class _FakeGraphOllama:
    def __init__(self, port: int) -> None:
        self.port = port

    def __enter__(self) -> _FakeGraphOllama:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                prompt = body["messages"][0]["content"]

                if "Sun Tzu" in prompt and "extract" in prompt.lower():
                    payload_content = {
                        "entities": [
                            {
                                "canonical_name": "Sun Tzu",
                                "aliases": ["Master Sun"],
                                "entity_type": "person",
                                "description": "Ancient Chinese military strategist.",
                                "importance": 0.95,
                            },
                            {
                                "canonical_name": "The Art of War",
                                "aliases": [],
                                "entity_type": "framework",
                                "description": "Sun Tzu's strategic treatise.",
                                "importance": 0.9,
                            },
                        ],
                        "relationships": [
                            {
                                "subject_canonical_name": "Sun Tzu",
                                "predicate": "authored",
                                "object_canonical_name": "The Art of War",
                                "confidence": 0.99,
                            }
                        ],
                    }
                elif "deception" in prompt.lower() and "extract" in prompt.lower():
                    payload_content = {
                        "entities": [
                            {
                                "canonical_name": "Deception",
                                "aliases": [],
                                "entity_type": "concept",
                                "description": "Core principle of warfare per Sun Tzu.",
                                "importance": 0.9,
                            },
                            {
                                "canonical_name": "The Art of War",
                                "aliases": [],
                                "entity_type": "framework",
                                "description": "Sun Tzu's strategic treatise.",
                                "importance": 0.8,
                            },
                        ],
                        "relationships": [
                            {
                                "subject_canonical_name": "The Art of War",
                                "predicate": "discusses",
                                "object_canonical_name": "Deception",
                                "confidence": 0.9,
                            }
                        ],
                    }
                elif "title" in prompt and "summary" in prompt:
                    payload_content = {
                        "title": "Strategic Foundations",
                        "summary": "A cluster centered on Sun Tzu and his treatise. " * 10,
                    }
                else:
                    payload_content = {"entities": [], "relationships": []}

                response_body = {
                    "message": {
                        "content": json.dumps(payload_content),
                        "role": "assistant",
                    },
                    "prompt_eval_count": len(prompt.split()),
                    "eval_count": 50,
                    "done": True,
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response_body).encode("utf-8"))

        self._server = http.server.HTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


# ---- Stage integration ----


def _make_chunks() -> list[ChunkRecord]:
    book_slug = "m4-test"
    texts = [
        "Sun Tzu wrote The Art of War as a strategic treatise.",
        "The Art of War teaches that deception is central to warfare.",
    ]
    return [
        ChunkRecord(
            chunk_uuid=derive_chunk_uuid(t, book_slug, ["Body"]),
            book_slug=book_slug,
            text=t,
            chapter_path=["Body"],
            paragraph_ids=[],
            page_start=i + 1,
            page_end=i + 1,
            token_count=max(1, len(t) // 4),
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=i,
        )
        for i, t in enumerate(texts)
    ]


def _seed_working_dir(tmp_path: Path, chunks: list[ChunkRecord]) -> Path:
    wd = tmp_path / "wd"
    (wd / "02_chunk").mkdir(parents=True)
    manifest = RunManifest(
        book_slug="m4-test",
        book_path=str(tmp_path / "book.pdf"),
        mode="api",
        started_at=datetime.now(UTC),
        status="in_progress",
        config={},
        marrow_version="test",
    )
    write_json(wd / "manifest.json", manifest)
    write_jsonl(wd / "02_chunk" / "chunks.jsonl", chunks)
    return wd


def test_stage_03_with_fake_ollama_builds_graph(tmp_path: Path) -> None:
    port = 47901
    chunks = _make_chunks()
    wd = _seed_working_dir(tmp_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "chunk": {"embedding_model": "stub"},
            "models": {
                "graph_extraction": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                }
            },
        }
    )

    with _FakeGraphOllama(port):
        result = stage_03_graph.run(wd, cfg)

    assert result.counts["chunks_processed"] == 2
    assert result.counts["chunks_failed"] == 0
    # Sun Tzu, The Art of War, Deception — but The Art of War merges across chunks.
    assert result.counts["entities"] == 3
    assert result.counts["relationships"] >= 2
    assert result.counts["communities"] >= 1
    assert result.counts["orphan_chunks"] == 0

    entities = list(read_jsonl(wd / "03_graph" / "entities.jsonl", EntityRecord))
    # The Art of War appears in both chunks → merged with chunk_uuids from both.
    aow = next(e for e in entities if e.canonical_name.startswith("The Art"))
    assert len(aow.chunk_uuids) == 2

    audit = read_json(wd / "03_graph" / "coverage_audit.json", CoverageAudit)
    assert audit.coverage_pct == 100.0
    assert audit.orphan_bucket_created is False

    # graph.graphml exists.
    assert (wd / "03_graph" / "graph.graphml").exists()


def test_stage_03_creates_orphans_bucket_for_chunks_without_entities(tmp_path: Path) -> None:
    """When a chunk's LLM response yields no entities, that chunk must still be covered."""
    port = 47902
    chunks = _make_chunks()
    # Add a chunk that the fake server won't recognize → empty entities.
    chunks.append(
        ChunkRecord(
            chunk_uuid=derive_chunk_uuid("meandering prose", "m4-test", ["Body"]),
            book_slug="m4-test",
            text="meandering prose with no named entities worth extracting",
            chapter_path=["Body"],
            paragraph_ids=[],
            page_start=3,
            page_end=3,
            token_count=5,
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=2,
        )
    )
    wd = _seed_working_dir(tmp_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "chunk": {"embedding_model": "stub"},
            "models": {
                "graph_extraction": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                }
            },
        }
    )

    with _FakeGraphOllama(port):
        stage_03_graph.run(wd, cfg)

    audit = read_json(wd / "03_graph" / "coverage_audit.json", CoverageAudit)
    assert audit.orphan_bucket_created is True
    assert audit.coverage_pct < 100.0
    assert len(audit.orphan_chunk_uuids) == 1

    communities = list(read_jsonl(wd / "03_graph" / "communities.jsonl", CommunityRecord))
    orphan = next(c for c in communities if c.is_orphan_bucket)
    assert orphan.chunk_uuids == audit.orphan_chunk_uuids


def test_stage_03_isolates_per_chunk_failure(tmp_path: Path) -> None:
    """An LLM failure on one chunk must not crash the stage."""
    port = 47903

    chunks = _make_chunks()
    wd = _seed_working_dir(tmp_path, chunks)

    # Server that fails 50% of requests to simulate flaky LLM.
    import http.server
    import threading

    class FlakyHandler(http.server.BaseHTTPRequestHandler):
        call_count = 0

        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            FlakyHandler.call_count += 1
            if FlakyHandler.call_count == 1:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"server error")
                return
            payload_content = {"entities": [], "relationships": []}
            body = {
                "message": {"content": json.dumps(payload_content), "role": "assistant"},
                "prompt_eval_count": 10,
                "eval_count": 10,
                "done": True,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))

    server = http.server.HTTPServer(("127.0.0.1", port), FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        cfg = load_config(
            overrides={
                "mode": "api",
                "runs_dir": str(tmp_path / "runs"),
                "chunk": {"embedding_model": "stub"},
                "models": {
                    "graph_extraction": {
                        "provider": "ollama",
                        "model_id": "qwen3:14b",
                        "api_base": f"http://127.0.0.1:{port}",
                    }
                },
            }
        )
        result = stage_03_graph.run(wd, cfg)
        assert result.counts["chunks_failed"] >= 1
        assert result.status == "warning"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.slow
@pytest.mark.network
def test_real_ollama_extracts_a_small_graph(tmp_path: Path) -> None:
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
    except (urllib.error.URLError, TimeoutError):
        pytest.skip("ollama server not running")

    chunks = _make_chunks()[:1]
    wd = _seed_working_dir(tmp_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "chunk": {"embedding_model": "stub"},
            "models": {
                "graph_extraction": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": "http://localhost:11434",
                }
            },
        }
    )

    result = stage_03_graph.run(wd, cfg)
    assert result.counts["chunks_processed"] == 1
    assert result.counts["entities"] >= 2
