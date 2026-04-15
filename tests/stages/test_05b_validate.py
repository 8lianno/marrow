"""M6 acceptance tests for stage_05b_validate: quiz → answer → grade → regen loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from marrow.config import load_config
from marrow.ids import chunk_uuid as derive_chunk_uuid
from marrow.ids import claim_id as derive_claim_id
from marrow.ids import section_id as derive_section_id
from marrow.io import read_json, read_jsonl, write_json, write_jsonl
from marrow.prompts import render
from marrow.schemas.brief import (
    BriefDraft,
    BriefSection,
    QuizQuestion,
    QuizResult,
)
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import AtomicClaim
from marrow.schemas.run import RunManifest
from marrow.stages import stage_05b_validate

# ---- Prompts ----


def test_quiz_generate_prompt_embeds_chunk_text() -> None:
    out = render(
        "quiz_generate.j2",
        chunk_uuid="abc",
        chapter_path=["Ch 1"],
        chunk_text="The capital of France is Paris.",
        n=3,
    )
    assert "Paris" in out
    assert "is_grounded" in out


def test_examinee_prompt_marks_uncovered_path() -> None:
    out = render(
        "examinee_answer.j2",
        question_text="What year did X happen?",
        brief_md="# Brief\n\nNothing about X.",
    )
    assert "Brief does not cover this." in out


# ---- _draft_to_md helper ----


def test_draft_to_md_concatenates_sections_with_chapter_headings() -> None:
    draft = BriefDraft(
        draft_version=0,
        book_slug="t",
        book_title="T",
        sections=[
            BriefSection(
                section_id=uuid4(),
                title="Ch 1",
                level=1,
                body_md="alpha",
                cited_chunk_uuids=[],
            ),
            BriefSection(
                section_id=uuid4(),
                title="Ch 2",
                level=1,
                body_md="beta",
                cited_chunk_uuids=[],
            ),
        ],
        word_count=2,
        estimated_page_count=1,
        citation_density=0.0,
        generated_at=datetime.now(UTC),
    )
    md = stage_05b_validate._draft_to_md(draft)
    assert "## Ch 1" in md and "## Ch 2" in md
    assert "alpha" in md and "beta" in md


# ---- Failed-chapter routing ----


def test_failed_chapters_orders_by_failure_count() -> None:
    chunk_a = uuid4()
    chunk_b = uuid4()
    chunk_c = uuid4()
    chunks = [
        ChunkRecord(
            chunk_uuid=chunk_a,
            book_slug="t",
            text="a",
            chapter_path=["Ch 1"],
            paragraph_ids=[],
            page_start=1,
            page_end=1,
            token_count=1,
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=0,
        ),
        ChunkRecord(
            chunk_uuid=chunk_b,
            book_slug="t",
            text="b",
            chapter_path=["Ch 2"],
            paragraph_ids=[],
            page_start=2,
            page_end=2,
            token_count=1,
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=1,
        ),
        ChunkRecord(
            chunk_uuid=chunk_c,
            book_slug="t",
            text="c",
            chapter_path=["Ch 2"],
            paragraph_ids=[],
            page_start=2,
            page_end=2,
            token_count=1,
            sentence_count=1,
            embedding_model="stub",
            embedding=[],
            window_index=2,
        ),
    ]
    quiz = [
        QuizQuestion(
            question_id=UUID(int=1),
            chapter_path=["Ch 1"],
            question_text="q1",
            expected_answer="a1",
            source_chunk_uuids=[chunk_a],
            leaf_level="name",
            is_grounded=True,
        ),
        QuizQuestion(
            question_id=UUID(int=2),
            chapter_path=["Ch 2"],
            question_text="q2",
            expected_answer="a2",
            source_chunk_uuids=[chunk_b],
            leaf_level="name",
            is_grounded=True,
        ),
        QuizQuestion(
            question_id=UUID(int=3),
            chapter_path=["Ch 2"],
            question_text="q3",
            expected_answer="a3",
            source_chunk_uuids=[chunk_c],
            leaf_level="name",
            is_grounded=True,
        ),
    ]
    results = QuizResult(
        iteration=1,
        total_questions=3,
        grounded_questions=3,
        answered_correctly=0,
        failed_question_ids=[UUID(int=1), UUID(int=2), UUID(int=3)],
    )
    failed = stage_05b_validate._failed_chapters_from_results(results, quiz, chunks)
    # Ch 2 has 2 failures, Ch 1 has 1 → Ch 2 first.
    assert failed == ["Ch 2", "Ch 1"]


# ---- Stage integration with a fake examinee server ----


class _FakeValidateOllama:
    """Returns:
    - to quiz_generate prompts: 1 question per chunk, expected = first 3 words.
    - to examinee prompts: the first sentence of the brief verbatim.
    - to grade prompts: is_correct=True iff expected ⊂ examinee answer.
    """

    def __init__(self, port: int) -> None:
        self.port = port

    def __enter__(self) -> _FakeValidateOllama:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                prompt = body["messages"][0]["content"]

                if "atomic recall" in prompt or "Generate" in prompt:
                    payload = {
                        "questions": [
                            {
                                "question_text": "What does the chunk say?",
                                "expected_answer": "warfare",
                                "leaf_level": "definition",
                                "is_grounded": True,
                            }
                        ]
                    }
                elif "EXPECTED ANSWER" in prompt and "EXAMINEE ANSWER" in prompt:
                    expected_in_examinee = "warfare" in prompt.lower()
                    payload = {"is_correct": expected_in_examinee, "rationale": "stub"}
                elif "BRIEF:" in prompt or "You are the examinee" in prompt:
                    payload = {
                        "answer": "The chunk discusses warfare.",
                        "answered_from_brief": True,
                    }
                else:
                    payload = {"answer": "n/a", "answered_from_brief": False}

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


def _seed_validate_dir(tmp_path: Path) -> tuple[Path, list[ChunkRecord]]:
    book_slug = "m6-test"
    chunk_a = ChunkRecord(
        chunk_uuid=derive_chunk_uuid("warfare is deception", book_slug, ["Ch 1"]),
        book_slug=book_slug,
        text="warfare is deception",
        chapter_path=["Ch 1"],
        paragraph_ids=[],
        page_start=1,
        page_end=1,
        token_count=3,
        sentence_count=1,
        embedding_model="stub",
        embedding=[],
        window_index=0,
    )

    wd = tmp_path / "wd"
    (wd / "02_chunk").mkdir(parents=True)
    (wd / "03_graph").mkdir(parents=True)
    (wd / "04_claims").mkdir(parents=True)
    (wd / "05_synthesize").mkdir(parents=True)

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
    write_jsonl(wd / "02_chunk" / "chunks.jsonl", [chunk_a])
    write_jsonl(wd / "03_graph" / "communities.jsonl", [])
    write_jsonl(
        wd / "04_claims" / "claims.jsonl",
        [
            AtomicClaim(
                claim_id=derive_claim_id("warfare is deception", book_slug),
                claim_text="Warfare is deception.",
                claim_type="argumentative",
                source_chunk_uuids=[chunk_a.chunk_uuid],
                source_span="warfare is deception",
                confidence=0.95,
            ),
        ],
    )
    section = BriefSection(
        section_id=derive_section_id("Ch 1", 1, []),
        title="Ch 1",
        level=1,
        body_md=f"Warfare is centered on deception. [chunk:{chunk_a.chunk_uuid}]",
        cited_chunk_uuids=[chunk_a.chunk_uuid],
    )
    initial = BriefDraft(
        draft_version=0,
        book_slug=book_slug,
        book_title="Test",
        sections=[section],
        word_count=10,
        estimated_page_count=1,
        citation_density=1.0,
        generated_at=datetime.now(UTC),
    )
    write_json(wd / "05_synthesize" / "draft_brief.json", initial)
    return wd, [chunk_a]


def test_stage_05b_completes_one_iteration_and_passes_threshold(tmp_path: Path) -> None:
    port = 48101
    wd, _ = _seed_validate_dir(tmp_path)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "validate": {"max_iterations": 1, "pass_rate_threshold": 0.5},
            "models": {
                "validation": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                },
                "quiz_generation": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                },
            },
        }
    )

    with _FakeValidateOllama(port):
        result = stage_05b_validate.run(wd, cfg)

    assert result.counts["iterations"] == 1
    assert result.counts["best_pass_rate_pct"] >= 50

    final = read_json(wd / "05b_validate" / "final_brief.json", BriefDraft)
    assert final.draft_version == 1
    assert any("iter_01" in line for line in final.iteration_history)


def test_stage_05b_writes_per_iteration_artifacts(tmp_path: Path) -> None:
    port = 48102
    wd, _ = _seed_validate_dir(tmp_path)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "validate": {"max_iterations": 1, "pass_rate_threshold": 0.5},
            "models": {
                "validation": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                },
                "quiz_generation": {
                    "provider": "ollama",
                    "model_id": "qwen3:14b",
                    "api_base": f"http://127.0.0.1:{port}",
                },
            },
        }
    )
    with _FakeValidateOllama(port):
        stage_05b_validate.run(wd, cfg)

    iter1 = wd / "05b_validate" / "iter_01"
    assert (iter1 / "quiz.jsonl").exists()
    assert (iter1 / "results.json").exists()
    quiz = list(read_jsonl(iter1 / "quiz.jsonl", QuizQuestion))
    assert len(quiz) == 1
    res = read_json(iter1 / "results.json", QuizResult)
    assert res.iteration == 1


# ---- Express Mode bypass ----


def test_stage_05b_express_bypass_skips_quiz_and_loop(tmp_path: Path) -> None:
    """US-011 FR-E04: max_iterations=0 must pass Stage 05 draft through unchanged."""
    wd, _ = _seed_validate_dir(tmp_path)
    initial = read_json(wd / "05_synthesize" / "draft_brief.json", BriefDraft)

    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "validate": {"max_iterations": 0, "pass_rate_threshold": 0.9},
        }
    )

    # No fake server — if the stage tries an LLM call it will fail, proving
    # the bypass skipped quiz generation and the iteration loop.
    result = stage_05b_validate.run(wd, cfg)

    assert result.counts["iterations"] == 0
    assert result.counts["questions_generated"] == 0
    assert any("express" in w for w in result.warnings)

    final = read_json(wd / "05b_validate" / "final_brief.json", BriefDraft)
    assert final.book_slug == initial.book_slug
    assert final.draft_version == initial.draft_version
    assert [s.title for s in final.sections] == [s.title for s in initial.sections]

    # No iter_NN/ directories should exist.
    assert not (wd / "05b_validate" / "iter_01").exists()


@pytest.mark.slow
@pytest.mark.network
def test_real_ollama_validate_loop(tmp_path: Path) -> None:
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
    except (urllib.error.URLError, TimeoutError):
        pytest.skip("ollama server not running")

    wd, _ = _seed_validate_dir(tmp_path)
    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "validate": {"max_iterations": 1, "pass_rate_threshold": 0.5},
        }
    )
    result = stage_05b_validate.run(wd, cfg)
    assert result.counts["iterations"] >= 1
