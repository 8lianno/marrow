"""M5 acceptance tests for stage_05_synthesize: chapter grouping, hierarchical merge, merge_tree audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from marrow.config import load_config
from marrow.ids import chunk_uuid as derive_chunk_uuid
from marrow.ids import claim_id as derive_claim_id
from marrow.io import read_json, write_json, write_jsonl
from marrow.prompts import render
from marrow.schemas.brief import BriefDraft
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import AtomicClaim
from marrow.schemas.document import CanonicalDocument, ParagraphNode, SectionNode
from marrow.schemas.graph import CommunityRecord
from marrow.schemas.run import RunManifest
from marrow.stages import stage_05_synthesize

# ---- Prompt rendering ----


def test_synthesize_chapter_prompt_embeds_citations_and_claims() -> None:
    claim = AtomicClaim(
        claim_id=uuid4(),
        claim_text="Sun rises in the east.",
        claim_type="factual",
        source_chunk_uuids=[UUID(int=100)],
        source_span="The sun rises in the east.",
        confidence=0.9,
    )
    out = render(
        "synthesize_chapter.j2",
        chapter_title="Ch 1",
        claims=[claim],
        communities=[],
        target_words=500,
    )
    assert "Sun rises in the east." in out
    assert "[chunk:" in out and str(UUID(int=100)) in out
    assert "500 words" in out


# ---- JSON salvage ----


def test_salvage_synthesis_json_extracts_from_prose() -> None:
    raw = (
        "Sure, here's the section:\n\n"
        '{"title": "Chapter One", "body_md": "Deception is central [chunk:00000000-0000-0000-0000-000000000001]."}'
    )
    parsed = stage_05_synthesize._salvage_synthesis_json(raw, "fallback")
    assert parsed.title == "Chapter One"
    assert "Deception" in parsed.body_md


def test_salvage_synthesis_json_falls_back_to_raw_body() -> None:
    parsed = stage_05_synthesize._salvage_synthesis_json("no braces here", "Fallback Title")
    assert parsed.title == "Fallback Title"
    assert "no braces" in parsed.body_md


# ---- Chapter grouping ----


def _chunks_for_two_chapters() -> list[ChunkRecord]:
    book_slug = "m5-test"
    return [
        ChunkRecord(
            chunk_uuid=derive_chunk_uuid("ch1 text a", book_slug, ["Chapter 1"]),
            book_slug=book_slug,
            text="ch1 text a",
            chapter_path=["Chapter 1"],
            paragraph_ids=[],
            page_start=1,
            page_end=1,
            token_count=2,
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=0,
        ),
        ChunkRecord(
            chunk_uuid=derive_chunk_uuid("ch2 text b", book_slug, ["Chapter 2"]),
            book_slug=book_slug,
            text="ch2 text b",
            chapter_path=["Chapter 2"],
            paragraph_ids=[],
            page_start=2,
            page_end=2,
            token_count=2,
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=1,
        ),
    ]


def test_group_claims_by_chapter_routes_via_source_chunks() -> None:
    chunks = _chunks_for_two_chapters()
    ch1_claim = AtomicClaim(
        claim_id=uuid4(),
        claim_text="claim for chapter 1",
        claim_type="factual",
        source_chunk_uuids=[chunks[0].chunk_uuid],
        source_span="ch1 text a",
        confidence=0.9,
    )
    ch2_claim = AtomicClaim(
        claim_id=uuid4(),
        claim_text="claim for chapter 2",
        claim_type="factual",
        source_chunk_uuids=[chunks[1].chunk_uuid],
        source_span="ch2 text b",
        confidence=0.9,
    )
    grouped = stage_05_synthesize._group_claims_by_chapter([ch1_claim, ch2_claim], chunks)
    assert len(grouped["Chapter 1"]) == 1
    assert len(grouped["Chapter 2"]) == 1


def test_group_communities_by_chapter_picks_majority_chapter() -> None:
    chunks = _chunks_for_two_chapters()
    comm = CommunityRecord(
        community_id=uuid4(),
        level=0,
        title="Test",
        summary="...",
        entity_ids=[],
        chunk_uuids=[chunks[0].chunk_uuid, chunks[0].chunk_uuid, chunks[1].chunk_uuid],
        is_orphan_bucket=False,
    )
    grouped = stage_05_synthesize._group_communities_by_chapter([comm], chunks)
    # Duplicate chunk_uuid counts twice → Chapter 1 majority.
    assert comm in grouped["Chapter 1"]


def test_group_communities_skips_orphan_bucket() -> None:
    chunks = _chunks_for_two_chapters()
    orphan = CommunityRecord(
        community_id=uuid4(),
        level=0,
        title="_orphans",
        summary="...",
        entity_ids=[],
        chunk_uuids=[chunks[0].chunk_uuid],
        is_orphan_bucket=True,
    )
    grouped = stage_05_synthesize._group_communities_by_chapter([orphan], chunks)
    assert all(orphan not in v for v in grouped.values())


# ---- Stage integration (fake LLM) ----


class _FakeSynthesisOllama:
    def __init__(self, port: int) -> None:
        self.port = port

    def __enter__(self) -> _FakeSynthesisOllama:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                prompt = body["messages"][0]["content"]

                # Extract the first few [chunk:UUID] tokens from the prompt
                # so our fake output cites real chunks.
                import re

                uuids = re.findall(r"\[chunk:([0-9a-fA-F-]{36})\]", prompt)
                # Title comes from the CHAPTER: line in the prompt.
                title_match = re.search(r"CHAPTER:\s*(.+)", prompt)
                title = title_match.group(1).strip() if title_match else "Untitled"

                if not uuids:
                    payload = {"title": title, "body_md": "No substantive claims. "}
                else:
                    cites = " ".join(f"[chunk:{u}]" for u in uuids[:3])
                    payload = {
                        "title": title,
                        "body_md": (
                            f"This chapter establishes a deception doctrine. {cites}\n\n"
                            f"It then derives three corollaries. {cites}"
                        ),
                    }

                response_body = {
                    "message": {
                        "content": json.dumps(payload),
                        "role": "assistant",
                    },
                    "prompt_eval_count": len(prompt.split()),
                    "eval_count": 30,
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


def _seed_upstream_artifacts(tmp_path: Path, chunks: list[ChunkRecord]) -> Path:
    from marrow.ids import paragraph_id

    wd = tmp_path / "wd"
    (wd / "01_ingest").mkdir(parents=True)
    (wd / "02_chunk").mkdir(parents=True)
    (wd / "03_graph").mkdir(parents=True)
    (wd / "04_claims").mkdir(parents=True)

    manifest = RunManifest(
        book_slug="m5-test",
        book_path=str(tmp_path / "book.pdf"),
        mode="api",
        started_at=datetime.now(UTC),
        status="in_progress",
        config={},
        marrow_version="test",
    )
    write_json(wd / "manifest.json", manifest)

    # Minimal canonical document for chapter ordering.
    def _para(text: str, ch: str) -> ParagraphNode:
        return ParagraphNode(
            paragraph_id=paragraph_id(text, [ch], 1),
            text=text,
            page_start=1,
            page_end=1,
        )

    from marrow.ids import section_id

    doc = CanonicalDocument(
        book_slug="m5-test",
        book_title="M5 Test",
        source_format="pdf",
        source_path=str(tmp_path / "book.pdf"),
        page_count=2,
        word_count=10,
        parser="test",
        toc=[
            SectionNode(
                section_id=section_id("Chapter 1", 1, []),
                title="Chapter 1",
                level=1,
                paragraphs=[_para("ch1 text a", "Chapter 1")],
            ),
            SectionNode(
                section_id=section_id("Chapter 2", 1, []),
                title="Chapter 2",
                level=1,
                paragraphs=[_para("ch2 text b", "Chapter 2")],
            ),
        ],
        extracted_at=datetime.now(UTC),
    )
    write_json(wd / "01_ingest" / "document.json", doc)
    write_jsonl(wd / "02_chunk" / "chunks.jsonl", chunks)
    write_jsonl(wd / "03_graph" / "communities.jsonl", [])

    claims = [
        AtomicClaim(
            claim_id=derive_claim_id("claim one", "m5-test"),
            claim_text="Warfare relies on deception.",
            claim_type="argumentative",
            source_chunk_uuids=[chunks[0].chunk_uuid],
            source_span="ch1 text a",
            confidence=0.9,
        ),
        AtomicClaim(
            claim_id=derive_claim_id("claim two", "m5-test"),
            claim_text="Logistics decide outcomes.",
            claim_type="causal",
            source_chunk_uuids=[chunks[1].chunk_uuid],
            source_span="ch2 text b",
            confidence=0.9,
        ),
    ]
    write_jsonl(wd / "04_claims" / "claims.jsonl", claims)
    return wd


def test_stage_05_synthesizes_one_section_per_chapter_with_citations(tmp_path: Path) -> None:
    port = 48001
    chunks = _chunks_for_two_chapters()
    wd = _seed_upstream_artifacts(tmp_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "synthesize": {"target_pages": 2, "page_tolerance": 10},
            "models": {
                "synthesis": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                }
            },
        }
    )

    with _FakeSynthesisOllama(port):
        result = stage_05_synthesize.run(wd, cfg)

    assert result.counts["chapters_synthesized"] == 2
    assert result.counts["chapters_failed"] == 0

    draft = read_json(wd / "05_synthesize" / "draft_brief.json", BriefDraft)
    assert len(draft.sections) == 2
    assert draft.sections[0].title == "Chapter 1"
    assert draft.sections[1].title == "Chapter 2"

    # Every section has citations that resolve to real chunk UUIDs.
    ch1_chunk = chunks[0].chunk_uuid
    ch2_chunk = chunks[1].chunk_uuid
    assert ch1_chunk in draft.sections[0].cited_chunk_uuids
    assert ch2_chunk in draft.sections[1].cited_chunk_uuids


def test_stage_05_merge_tree_records_coverage_per_chapter(tmp_path: Path) -> None:
    port = 48002
    chunks = _chunks_for_two_chapters()
    wd = _seed_upstream_artifacts(tmp_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "synthesize": {"target_pages": 2, "page_tolerance": 10},
            "models": {
                "synthesis": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                }
            },
        }
    )

    with _FakeSynthesisOllama(port):
        stage_05_synthesize.run(wd, cfg)

    merge_tree = read_json(wd / "05_synthesize" / "merge_tree.json")
    assert "sections" in merge_tree
    assert "Chapter 1" in merge_tree["sections"]
    ch1 = merge_tree["sections"]["Chapter 1"]
    assert ch1["input_claims"] == 1
    assert ch1["expected_chunks"] == 1
    assert ch1["missing_chunks"] == []


def test_stage_05_isolates_per_chapter_failure(tmp_path: Path) -> None:
    """A 500 from the LLM on one chapter must not crash the stage."""
    port = 48003
    chunks = _chunks_for_two_chapters()
    wd = _seed_upstream_artifacts(tmp_path, chunks)

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
                self.wfile.write(b"boom")
                return
            body = {
                "message": {
                    "content": json.dumps(
                        {
                            "title": "Chapter 2",
                            "body_md": "A sentence. [chunk:" + str(chunks[1].chunk_uuid) + "]",
                        }
                    ),
                    "role": "assistant",
                },
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
                "synthesize": {"target_pages": 2, "page_tolerance": 10},
                "models": {
                    "synthesis": {
                        "provider": "ollama",
                        "model_id": "qwen3:14b",
                        "api_base": f"http://127.0.0.1:{port}",
                    }
                },
            }
        )
        result = stage_05_synthesize.run(wd, cfg)
        # One chapter failed, one succeeded.
        assert result.counts["chapters_failed"] == 1
        assert result.counts["chapters_synthesized"] == 1
        assert result.status == "warning"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.slow
@pytest.mark.network
def test_real_ollama_synthesizes_one_chapter(tmp_path: Path) -> None:
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
    except (urllib.error.URLError, TimeoutError):
        pytest.skip("ollama server not running")

    chunks = _chunks_for_two_chapters()[:1]
    wd = _seed_upstream_artifacts(tmp_path, chunks)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "synthesize": {"target_pages": 2, "page_tolerance": 10},
            "models": {
                "synthesis": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": "http://localhost:11434",
                }
            },
        }
    )

    result = stage_05_synthesize.run(wd, cfg)
    assert result.counts["chapters_synthesized"] == 1
    draft = read_json(wd / "05_synthesize" / "draft_brief.json", BriefDraft)
    assert draft.sections, "Real synthesis should produce at least one section"
    assert draft.sections[0].body_md, "body_md must not be empty"
