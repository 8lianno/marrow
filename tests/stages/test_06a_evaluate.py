"""M6 acceptance tests for stage_06a_evaluate: BooookScore + FActScore + HAMLET."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from marrow.config import load_config
from marrow.ids import chunk_uuid as derive_chunk_uuid
from marrow.ids import claim_id as derive_claim_id
from marrow.ids import paragraph_id, section_id
from marrow.io import read_json, write_json, write_jsonl
from marrow.schemas.brief import BriefDraft, BriefSection, EvaluationReport
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import AtomicClaim
from marrow.schemas.document import CanonicalDocument, ParagraphNode, SectionNode
from marrow.schemas.run import RunManifest
from marrow.stages import stage_06a_evaluate

# ---- HAMLET (deterministic) ----


def test_hamlet_root_recall_chapters_in_brief() -> None:
    doc = CanonicalDocument(
        book_slug="t",
        book_title="T",
        source_format="pdf",
        source_path="x",
        page_count=1,
        word_count=1,
        parser="t",
        toc=[
            SectionNode(section_id=uuid4(), title="Ch 1", level=1),
            SectionNode(section_id=uuid4(), title="Ch 2", level=1),
        ],
        extracted_at=datetime.now(UTC),
    )
    brief = BriefDraft(
        draft_version=0,
        book_slug="t",
        book_title="T",
        sections=[
            BriefSection(section_id=uuid4(), title="Ch 1", level=1, body_md="x"),
        ],
        word_count=1,
        estimated_page_count=1,
        citation_density=0.0,
        generated_at=datetime.now(UTC),
    )
    root, _, _ = stage_06a_evaluate._hamlet(doc, [], [], brief)
    assert root == 0.5  # 1 of 2 chapters present


def test_hamlet_branch_recall_fraction_chunks_cited() -> None:
    chunks = [
        ChunkRecord(
            chunk_uuid=UUID(int=i),
            book_slug="t",
            text=str(i),
            chapter_path=["Body"],
            paragraph_ids=[],
            page_start=1,
            page_end=1,
            token_count=1,
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=i,
        )
        for i in range(1, 5)
    ]
    brief = BriefDraft(
        draft_version=0,
        book_slug="t",
        book_title="T",
        sections=[
            BriefSection(
                section_id=uuid4(),
                title="Body",
                level=1,
                body_md=f"x [chunk:{UUID(int=1)}] [chunk:{UUID(int=2)}]",
                cited_chunk_uuids=[UUID(int=1), UUID(int=2)],
            )
        ],
        word_count=1,
        estimated_page_count=1,
        citation_density=0.0,
        generated_at=datetime.now(UTC),
    )
    doc = CanonicalDocument(
        book_slug="t",
        book_title="T",
        source_format="pdf",
        source_path="x",
        page_count=1,
        word_count=1,
        parser="t",
        toc=[SectionNode(section_id=uuid4(), title="Body", level=1)],
        extracted_at=datetime.now(UTC),
    )
    _, branch, _ = stage_06a_evaluate._hamlet(doc, chunks, [], brief)
    assert branch == 0.5  # 2 of 4 cited


def test_hamlet_leaf_recall_claims_with_cited_chunks() -> None:
    chunk_a = UUID(int=1)
    chunk_b = UUID(int=2)
    claims = [
        AtomicClaim(
            claim_id=uuid4(),
            claim_text="claim a",
            claim_type="factual",
            source_chunk_uuids=[chunk_a],
            source_span="x",
            confidence=1.0,
        ),
        AtomicClaim(
            claim_id=uuid4(),
            claim_text="claim b",
            claim_type="factual",
            source_chunk_uuids=[chunk_b],
            source_span="x",
            confidence=1.0,
        ),
    ]
    brief = BriefDraft(
        draft_version=0,
        book_slug="t",
        book_title="T",
        sections=[
            BriefSection(
                section_id=uuid4(),
                title="Body",
                level=1,
                body_md=f"x [chunk:{chunk_a}]",
                cited_chunk_uuids=[chunk_a],
            )
        ],
        word_count=1,
        estimated_page_count=1,
        citation_density=0.0,
        generated_at=datetime.now(UTC),
    )
    doc = CanonicalDocument(
        book_slug="t",
        book_title="T",
        source_format="pdf",
        source_path="x",
        page_count=1,
        word_count=1,
        parser="t",
        toc=[],
        extracted_at=datetime.now(UTC),
    )
    _, _, leaf = stage_06a_evaluate._hamlet(doc, [], claims, brief)
    assert leaf == 0.5


def test_sample_cited_sentences_extracts_pairs() -> None:
    brief = BriefDraft(
        draft_version=0,
        book_slug="t",
        book_title="T",
        sections=[
            BriefSection(
                section_id=uuid4(),
                title="Ch 1",
                level=1,
                body_md=(
                    f"Sentence one. [chunk:{UUID(int=1)}]\n"
                    f"Sentence two. Sentence three. [chunk:{UUID(int=2)}]\n"
                    "Uncited line."
                ),
            ),
        ],
        word_count=1,
        estimated_page_count=1,
        citation_density=0.0,
        generated_at=datetime.now(UTC),
    )
    pairs = stage_06a_evaluate._sample_cited_sentences(brief)
    # Three sentences across two cited lines.
    assert len(pairs) == 3
    assert all(isinstance(u, UUID) for _, u in pairs)


# ---- Stage integration with fake LLM ----


class _FakeEvalOllama:
    """Returns:
    - coherence: score=0.85
    - fact verification: is_supported=True
    """

    def __init__(self, port: int) -> None:
        self.port = port

    def __enter__(self) -> _FakeEvalOllama:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                prompt = body["messages"][0]["content"]

                if "coherence" in prompt.lower():
                    payload = {"score": 0.85, "rationale": "coherent"}
                elif "verify" in prompt.lower() or "is_supported" in prompt:
                    payload = {"is_supported": True, "rationale": "supported"}
                else:
                    payload = {"score": 0.5, "rationale": "unknown"}

                response_body = {
                    "message": {"content": json.dumps(payload), "role": "assistant"},
                    "prompt_eval_count": 10,
                    "eval_count": 10,
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


def _seed_eval_dir(tmp_path: Path) -> Path:
    book_slug = "m6-eval-test"
    chunk = ChunkRecord(
        chunk_uuid=derive_chunk_uuid("warfare deception", book_slug, ["Body"]),
        book_slug=book_slug,
        text="warfare deception",
        chapter_path=["Body"],
        paragraph_ids=[],
        page_start=1,
        page_end=1,
        token_count=2,
        sentence_count=1,
        embedding_model="stub",
        embedding=[],
        window_index=0,
    )

    wd = tmp_path / "wd"
    (wd / "01_ingest").mkdir(parents=True)
    (wd / "02_chunk").mkdir(parents=True)
    (wd / "04_claims").mkdir(parents=True)
    (wd / "05b_validate").mkdir(parents=True)

    write_json(
        wd / "manifest.json",
        RunManifest(
            book_slug=book_slug,
            book_path=str(tmp_path / "book.pdf"),
            mode="api",
            started_at=datetime.now(UTC),
            status="in_progress",
            config={},
            marrow_version="test",
        ),
    )
    chapter_path = ["Body"]
    doc = CanonicalDocument(
        book_slug=book_slug,
        book_title="Test",
        source_format="pdf",
        source_path=str(tmp_path / "book.pdf"),
        page_count=1,
        word_count=2,
        parser="t",
        toc=[
            SectionNode(
                section_id=section_id("Body", 1, []),
                title="Body",
                level=1,
                paragraphs=[
                    ParagraphNode(
                        paragraph_id=paragraph_id("warfare deception", chapter_path, 1),
                        text="warfare deception",
                        page_start=1,
                        page_end=1,
                    )
                ],
            )
        ],
        extracted_at=datetime.now(UTC),
    )
    write_json(wd / "01_ingest" / "document.json", doc)
    write_jsonl(wd / "02_chunk" / "chunks.jsonl", [chunk])
    write_jsonl(
        wd / "04_claims" / "claims.jsonl",
        [
            AtomicClaim(
                claim_id=derive_claim_id("Warfare is deception.", book_slug),
                claim_text="Warfare is deception.",
                claim_type="argumentative",
                source_chunk_uuids=[chunk.chunk_uuid],
                source_span="warfare deception",
                confidence=0.95,
            ),
        ],
    )

    section = BriefSection(
        section_id=section_id("Body", 1, []),
        title="Body",
        level=1,
        body_md=f"Warfare is deception. [chunk:{chunk.chunk_uuid}]",
        cited_chunk_uuids=[chunk.chunk_uuid],
    )
    final = BriefDraft(
        draft_version=1,
        book_slug=book_slug,
        book_title="Test",
        sections=[section],
        word_count=4,
        estimated_page_count=1,
        citation_density=1.0,
        generated_at=datetime.now(UTC),
    )
    write_json(wd / "05b_validate" / "final_brief.json", final)
    return wd


def test_stage_06a_produces_evaluation_report(tmp_path: Path) -> None:
    port = 48201
    wd = _seed_eval_dir(tmp_path)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "evaluate": {
                "hamlet_leaf_threshold": 0.0,  # we have only 1 claim → trivially passes
                "booookscore_threshold": 0.0,
                "factscore_threshold": 0.0,
            },
            "models": {
                "validation": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                },
            },
        }
    )

    with _FakeEvalOllama(port):
        result = stage_06a_evaluate.run(wd, cfg)

    assert result.counts["verdict_pass"] == 1
    report = read_json(wd / "06a_evaluate" / "composite.json", EvaluationReport)
    assert report.verdict == "PASS"
    assert report.booookscore > 0.5
    assert report.factscore > 0
    # All structure covered (1 chapter, 1 chunk, 1 claim).
    assert report.hamlet_root_recall == 1.0
    assert report.hamlet_branch_recall == 1.0
    assert report.hamlet_leaf_recall == 1.0


def test_stage_06a_marks_fail_when_thresholds_missed(tmp_path: Path) -> None:
    port = 48202
    wd = _seed_eval_dir(tmp_path)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "evaluate": {
                "hamlet_leaf_threshold": 0.99,
                "booookscore_threshold": 0.99,
                "factscore_threshold": 0.99,
            },
            "models": {
                "validation": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                },
            },
        }
    )
    with _FakeEvalOllama(port):
        stage_06a_evaluate.run(wd, cfg)

    report = read_json(wd / "06a_evaluate" / "composite.json", EvaluationReport)
    # FActScore length-penalty (1 sample << gamma=10) drops it well below 0.99.
    assert report.verdict == "FAIL"
    assert any("FActScore" in r for r in report.failure_reasons)


# ---- Express Mode bypass ----


def test_stage_06a_express_bypass_writes_default_scorecard(tmp_path: Path) -> None:
    """US-011 FR-E05: evaluate.skip writes an empty PASS scorecard with zero LLM calls."""
    wd = _seed_eval_dir(tmp_path)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "evaluate": {"skip": True},
        }
    )

    # No fake server — any LLM call would fail.
    result = stage_06a_evaluate.run(wd, cfg)

    assert any("express" in w for w in result.warnings)

    report = read_json(wd / "06a_evaluate" / "composite.json", EvaluationReport)
    assert report.verdict == "PASS"
    assert report.booookscore == 0.0
    assert report.factscore == 0.0
    assert report.composite_score == 0.0
    assert report.hamlet_root_recall == 0.0
    assert report.hamlet_branch_recall == 0.0
    assert report.hamlet_leaf_recall == 0.0
    assert report.failure_reasons == []

    # Per-metric sidecar files are not written in skip mode.
    assert not (wd / "06a_evaluate" / "booookscore.json").exists()
    assert not (wd / "06a_evaluate" / "factscore.json").exists()
    assert not (wd / "06a_evaluate" / "hamlet.json").exists()


@pytest.mark.slow
@pytest.mark.network
def test_real_ollama_full_evaluation(tmp_path: Path) -> None:
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
    except (urllib.error.URLError, TimeoutError):
        pytest.skip("ollama server not running")

    wd = _seed_eval_dir(tmp_path)
    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "evaluate": {
                "hamlet_leaf_threshold": 0.0,
                "booookscore_threshold": 0.0,
                "factscore_threshold": 0.0,
            },
        }
    )
    result = stage_06a_evaluate.run(wd, cfg)
    assert result.counts["verdict_pass"] == 1
