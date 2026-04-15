"""Stage 05b: SummQ-style adversarial quiz validation (M6 real implementation).

The lossless gate: generate questions from source chunks, examine the brief
on those questions, grade the answers. If pass_rate falls below
`config.validate_.pass_rate_threshold`, regenerate the chapter sections whose
chunks produced the failing questions, then re-quiz. Cap iterations at
`config.validate_.max_iterations`.

Each iteration writes its own subdirectory:
  iter_NN/quiz.jsonl   — QuizQuestion[]
  iter_NN/results.json — QuizResult

The final brief (best-pass-rate iteration) is written to `final_brief.json`.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

from marrow.config import MarrowConfig
from marrow.errors import LLMError
from marrow.ids import question_id as derive_question_id
from marrow.ids import section_id as derive_section_id
from marrow.io import read_json, read_jsonl, write_json, write_jsonl
from marrow.llm import LLMCaller
from marrow.logging import get_logger
from marrow.progress import current as progress_current
from marrow.prompts import render
from marrow.schemas.brief import (
    BriefDraft,
    BriefSection,
    ChapterSynthesisResponse,
    GeneratedQuiz,
    QuizAnswerResponse,
    QuizGrade,
    QuizQuestion,
    QuizResult,
)
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import AtomicClaim
from marrow.schemas.graph import CommunityRecord
from marrow.schemas.run import StageResult

log = get_logger(__name__)
STAGE_NAME = "05b_validate"

# Limit per-chunk question generation to keep iteration time bounded.
QUESTIONS_PER_CHUNK = 2
# Cap the chunks that get sampled in M6 walking; full coverage lands in M6.5.
MAX_CHUNKS_QUIZZED = 30

# ROADMAP M6 budget: ≤ 30 min for 300 pages = 6s/page.
PERF_SECONDS_PER_PAGE_BUDGET = 30 * 60 / 300


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    chunks = list(read_jsonl(working_dir / "02_chunk" / "chunks.jsonl", ChunkRecord))
    initial_draft = read_json(working_dir / "05_synthesize" / "draft_brief.json", BriefDraft)
    communities = list(read_jsonl(working_dir / "03_graph" / "communities.jsonl", CommunityRecord))
    all_claims = list(read_jsonl(working_dir / "04_claims" / "claims.jsonl", AtomicClaim))
    claims = [c for c in all_claims if c.is_duplicate_of is None]

    caller = LLMCaller(working_dir, config)
    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    # Sample chunks for quizzing (deterministic via seeded RNG).
    rng = random.Random(42)
    sampled_chunks = (
        chunks if len(chunks) <= MAX_CHUNKS_QUIZZED else rng.sample(chunks, MAX_CHUNKS_QUIZZED)
    )

    threshold = config.validate_.pass_rate_threshold
    max_iters = config.validate_.max_iterations

    # Express Mode: max_iterations == 0 skips quiz/examine/regen entirely and
    # passes the Stage 05 draft through unchanged.
    if max_iters == 0:
        write_json(out_dir / "final_brief.json", initial_draft)
        log.info("validate_skipped_express_mode", reason="max_iterations=0")
        return StageResult(
            stage_name=STAGE_NAME,
            started_at=started,
            completed_at=datetime.now(UTC),
            duration_seconds=perf_counter() - t0,
            status="warning",
            counts={
                "iterations": 0,
                "chunks_sampled": 0,
                "questions_generated": 0,
                "best_pass_rate_pct": 0,
                "threshold_pct": int(threshold * 100),
            },
            warnings=["validate_skipped: max_iterations=0 (express mode)"],
            output_paths=[str(out_dir / "final_brief.json")],
        )

    # Progress: quiz generation + (per-iter answer+grade, up to max_iters rounds).
    # Exact question count is known only after generation; start with the
    # quiz-generation portion and extend once the quiz is in hand.
    progress = progress_current()
    progress.stage_start(STAGE_NAME, total=max(1, len(sampled_chunks)), unit="quiz-gen/grade")

    # Generate quiz once from source chunks (questions are stable across iterations).
    quiz = _generate_quiz(caller, sampled_chunks, progress=progress)
    # Add per-iteration work to the bar (answer + grade = 2 ticks per grounded question).
    grounded_quiz = [q for q in quiz if q.is_grounded]
    per_iter_ticks = len(grounded_quiz) * 2
    progress.stage_extend(per_iter_ticks * max_iters)

    current_draft = initial_draft.model_copy(deep=True)
    iteration_results: list[QuizResult] = []
    best_pass_rate = -1.0
    best_draft = current_draft

    for iter_idx in range(1, max_iters + 1):
        iter_dir = out_dir / f"iter_{iter_idx:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        # Examine the current brief.
        write_jsonl(iter_dir / "quiz.jsonl", quiz)
        results = _examine_brief(caller, current_draft, quiz, iter_idx, progress=progress)
        write_json(iter_dir / "results.json", results)
        iteration_results.append(results)
        current_draft.iteration_history.append(
            f"iter_{iter_idx:02d}: pass_rate={results.pass_rate:.3f}"
        )
        progress.stage_log(
            f"iter {iter_idx}: pass_rate={results.pass_rate:.3f} "
            f"({results.answered_correctly}/{results.grounded_questions} correct)"
        )

        if results.pass_rate > best_pass_rate:
            best_pass_rate = results.pass_rate
            best_draft = current_draft.model_copy(deep=True)

        log.info(
            "validate_iteration_complete",
            iteration=iter_idx,
            pass_rate=results.pass_rate,
            grounded=results.grounded_questions,
            correct=results.answered_correctly,
        )

        if results.pass_rate >= threshold:
            log.info("validate_passed_threshold", iteration=iter_idx, threshold=threshold)
            break

        if iter_idx == max_iters:
            warnings.append(
                f"validate_did_not_reach_threshold "
                f"(best pass_rate={best_pass_rate:.3f} < {threshold})"
            )
            break

        # Regenerate failed sections.
        failed_chapters = _failed_chapters_from_results(results, quiz, sampled_chunks)
        if not failed_chapters:
            log.warning(
                "no_failing_chapters_to_regen_but_below_threshold",
                pass_rate=results.pass_rate,
            )
            break

        log.info("regenerating_chapters", chapters=failed_chapters)
        current_draft = _regenerate_chapters(
            caller, current_draft, failed_chapters, claims, communities, chunks, config
        )

    final_draft = best_draft
    final_draft.draft_version = len(iteration_results)
    write_json(out_dir / "final_brief.json", final_draft)

    elapsed = perf_counter() - t0

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "iterations": len(iteration_results),
            "chunks_sampled": len(sampled_chunks),
            "questions_generated": len(quiz),
            "best_pass_rate_pct": int(best_pass_rate * 100),
            "threshold_pct": int(threshold * 100),
        },
        warnings=warnings,
        output_paths=[str(out_dir / "final_brief.json")],
    )


# ---- Quiz generation ----


def _generate_quiz(
    caller: LLMCaller,
    chunks: list[ChunkRecord],
    progress=None,
) -> list[QuizQuestion]:
    out: list[QuizQuestion] = []
    for chunk in chunks:
        try:
            response = _quiz_one_chunk(caller, chunk)
        except Exception as e:  # isolate per-chunk failures
            log.warning(
                "quiz_generation_failed",
                chunk_uuid=str(chunk.chunk_uuid),
                error=str(e),
            )
            if progress is not None:
                progress.stage_advance(1)
            continue
        for gq in response.questions:
            out.append(
                QuizQuestion(
                    question_id=derive_question_id(gq.question_text, chunk.chapter_path),
                    chapter_path=chunk.chapter_path,
                    question_text=gq.question_text,
                    expected_answer=gq.expected_answer,
                    source_chunk_uuids=[chunk.chunk_uuid],
                    leaf_level=gq.leaf_level,
                    is_grounded=gq.is_grounded,
                )
            )
        if progress is not None:
            progress.stage_advance(1)
    return out


def _quiz_one_chunk(caller: LLMCaller, chunk: ChunkRecord) -> GeneratedQuiz:
    prompt = render(
        "quiz_generate.j2",
        chunk_uuid=str(chunk.chunk_uuid),
        chapter_path=chunk.chapter_path,
        chunk_text=chunk.text,
        n=QUESTIONS_PER_CHUNK,
    )
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="quiz_generation",
        response_schema=GeneratedQuiz,
        chunk_uuids=[chunk.chunk_uuid],
    )
    if isinstance(raw, GeneratedQuiz):
        return raw
    return _salvage(raw, GeneratedQuiz, GeneratedQuiz())


# ---- Examination ----


def _examine_brief(
    caller: LLMCaller,
    draft: BriefDraft,
    quiz: list[QuizQuestion],
    iteration: int,
    progress=None,
) -> QuizResult:
    brief_md = _draft_to_md(draft)
    grounded_count = 0
    correct_count = 0
    failed_question_ids: list[UUID] = []

    for question in quiz:
        if not question.is_grounded:
            continue
        grounded_count += 1
        try:
            answer = _ask_examinee(caller, question, brief_md)
            if progress is not None:
                progress.stage_advance(1)  # one for the answer call
            grade = _grade_answer(caller, question, answer)
            if progress is not None:
                progress.stage_advance(1)  # one for the grade call
        except LLMError as e:
            log.warning(
                "examine_call_failed",
                question_id=str(question.question_id),
                error=str(e),
            )
            failed_question_ids.append(question.question_id)
            continue

        if grade.is_correct:
            correct_count += 1
        else:
            failed_question_ids.append(question.question_id)

    return QuizResult(
        iteration=iteration,
        total_questions=len(quiz),
        grounded_questions=grounded_count,
        answered_correctly=correct_count,
        failed_question_ids=failed_question_ids,
    )


def _ask_examinee(caller: LLMCaller, question: QuizQuestion, brief_md: str) -> QuizAnswerResponse:
    prompt = render(
        "examinee_answer.j2",
        question_text=question.question_text,
        brief_md=brief_md,
    )
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="validation",
        response_schema=QuizAnswerResponse,
    )
    if isinstance(raw, QuizAnswerResponse):
        return raw
    return _salvage(
        raw, QuizAnswerResponse, QuizAnswerResponse(answer=raw or "", answered_from_brief=False)
    )


def _grade_answer(
    caller: LLMCaller, question: QuizQuestion, answer: QuizAnswerResponse
) -> QuizGrade:
    if not answer.answered_from_brief or "Brief does not cover" in answer.answer:
        return QuizGrade(is_correct=False, rationale="Brief did not cover this fact.")
    prompt = render(
        "quiz_grade.j2",
        question_text=question.question_text,
        expected_answer=question.expected_answer,
        examinee_answer=answer.answer,
    )
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="validation",
        response_schema=QuizGrade,
    )
    if isinstance(raw, QuizGrade):
        return raw
    return _salvage(raw, QuizGrade, QuizGrade(is_correct=False, rationale="Grader parse failure."))


# ---- Regeneration ----


def _failed_chapters_from_results(
    results: QuizResult,
    quiz: list[QuizQuestion],
    chunks: list[ChunkRecord],
) -> list[str]:
    """Return the chapter titles whose questions failed (sorted by failure count)."""
    chunk_to_chapter = {
        c.chunk_uuid: (c.chapter_path[0] if c.chapter_path else "Body") for c in chunks
    }
    failed_chapter_counts: dict[str, int] = {}
    for q in quiz:
        if q.question_id in results.failed_question_ids:
            for u in q.source_chunk_uuids:
                chapter = chunk_to_chapter.get(u, "Body")
                failed_chapter_counts[chapter] = failed_chapter_counts.get(chapter, 0) + 1
    return sorted(failed_chapter_counts, key=lambda c: -failed_chapter_counts[c])


def _regenerate_chapters(
    caller: LLMCaller,
    draft: BriefDraft,
    failed_chapters: list[str],
    claims: list[AtomicClaim],
    communities: list[CommunityRecord],
    chunks: list[ChunkRecord],
    config: MarrowConfig,
) -> BriefDraft:
    """Re-call synthesis on the named chapters; replace those sections in the draft."""
    from marrow.stages.stage_05_synthesize import (
        WORDS_PER_PAGE,
        _group_claims_by_chapter,
        _group_communities_by_chapter,
        _synthesize_chapter,
    )

    claims_by_chapter = _group_claims_by_chapter(claims, chunks)
    comms_by_chapter = _group_communities_by_chapter(communities, chunks)
    target_total_words = config.synthesize.target_pages * WORDS_PER_PAGE
    total_input_claims = sum(len(v) for v in claims_by_chapter.values()) or 1

    new_draft = draft.model_copy(deep=True)
    title_to_idx = {s.title: i for i, s in enumerate(new_draft.sections)}

    for chapter_title in failed_chapters:
        chapter_claims = claims_by_chapter.get(chapter_title, [])
        if not chapter_claims:
            continue
        chapter_comms = comms_by_chapter.get(chapter_title, [])
        share = len(chapter_claims) / total_input_claims
        chapter_target_words = max(150, int(share * target_total_words))

        try:
            response = _synthesize_chapter(
                caller, chapter_title, chapter_claims, chapter_comms, chapter_target_words
            )
        except Exception as e:
            log.warning("regen_chapter_failed", chapter=chapter_title, error=str(e))
            continue

        if not isinstance(response, ChapterSynthesisResponse):
            log.warning("regen_response_unexpected_type", chapter=chapter_title)
            continue

        cited = BriefSection.parse_citations(response.body_md)
        new_section = BriefSection(
            section_id=derive_section_id(response.title or chapter_title, 1, []),
            title=response.title or chapter_title,
            level=1,
            body_md=response.body_md,
            cited_chunk_uuids=cited,
        )
        if chapter_title in title_to_idx:
            new_draft.sections[title_to_idx[chapter_title]] = new_section
        else:
            new_draft.sections.append(new_section)

    return new_draft


# ---- Helpers ----


def _draft_to_md(draft: BriefDraft) -> str:
    parts = [f"# {draft.book_title}", ""]
    for s in draft.sections:
        parts.append(f"## {s.title}")
        parts.append("")
        parts.append(s.body_md)
        parts.append("")
    return "\n".join(parts)


def _salvage(raw: str, schema, fallback):
    """Best-effort JSON-from-prose fallback."""
    if not isinstance(raw, str):
        return raw
    try:
        return schema.model_validate_json(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return schema.model_validate(json.loads(raw[start : end + 1]))
        except Exception:
            pass
    return fallback
