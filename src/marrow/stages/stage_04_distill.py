"""Stage 04: Distill each chapter against its spine.

Gemini Flash compresses each chapter to ~30% of its length (or per-section
compression ratio from classification). Uses a continuation loop to handle
chapters whose distillation exceeds the model's output window.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

from marrow.config import MarrowConfig
from marrow.io import read_json, write_json, write_text
from marrow.llm import LLMCaller
from marrow.logging import get_logger
from marrow.prompts import render
from marrow.schemas.classify import BookClassification
from marrow.schemas.distill import ChapterDistillation, Distillation
from marrow.schemas.document import CanonicalDocument, ParagraphNode, SectionNode
from marrow.schemas.run import StageResult
from marrow.schemas.spine import ChapterSpine, Spine

log = get_logger(__name__)
STAGE_NAME = "04_distill"

# Minimum word delta per continuation round. If a continuation adds fewer
# words than this, the model has nothing more to say — break early.
_MIN_CONTINUATION_DELTA = 50


def _flatten_paragraphs(section: SectionNode) -> list[ParagraphNode]:
    paragraphs: list[ParagraphNode] = list(section.paragraphs)
    for sub in section.subsections:
        paragraphs.extend(_flatten_paragraphs(sub))
    return paragraphs


def _word_count(text: str) -> int:
    return len(text.split())


def _appears_truncated(text: str, target_words: int, finish_reason: str) -> bool:
    """Determine if a distillation response was truncated and needs continuation."""
    # Primary signal: model said it hit the token limit
    if finish_reason == "MAX_TOKENS":
        return True

    # If we're within 85% of target, don't continue
    if _word_count(text) >= target_words * 0.85:
        return False

    # Check if text ends mid-sentence (no terminal punctuation)
    stripped = text.rstrip()
    if stripped and stripped[-1] not in ".!?\"')":
        return True

    return False


def _merge_continuation(accumulated: str, continuation: str) -> str:
    """Merge continuation text, detecting and removing overlap."""
    if not continuation.strip():
        return accumulated

    # Look for overlap between the end of accumulated and start of continuation
    tail = accumulated[-300:] if len(accumulated) > 300 else accumulated
    head = continuation[:300] if len(continuation) > 300 else continuation

    best_overlap = 0
    for length in range(20, min(len(tail), len(head)) + 1):
        if tail.endswith(head[:length]):
            best_overlap = length

    if best_overlap > 20:
        return accumulated + continuation[best_overlap:]

    # No significant overlap found — just concatenate with a space
    return accumulated.rstrip() + "\n\n" + continuation.lstrip()


def _remaining_spine_items(spine: ChapterSpine, current_text: str) -> list[str]:
    """Identify spine items not yet covered in the current text."""
    text_lower = current_text.lower()
    remaining: list[str] = []

    for f in spine.frameworks:
        if f.name.lower() not in text_lower:
            remaining.append(f"Framework: {f.name} — {f.description}")

    for e in spine.key_examples:
        if e.label.lower() not in text_lower:
            remaining.append(f"Example: {e.label} — {e.gist}")

    for t in spine.key_terms:
        if t.term.lower() not in text_lower:
            remaining.append(f"Key term: {t.term} — {t.definition}")

    return remaining


def _distill_chapter(
    caller: LLMCaller,
    section: SectionNode,
    spine: ChapterSpine,
    classification: BookClassification,
    config: MarrowConfig,
    out_dir: Path,
    chapter_idx: int,
) -> ChapterDistillation:
    """Distill a single chapter with continuation loop."""
    paragraphs = _flatten_paragraphs(section)
    target_words = spine.target_word_count
    compression_pct = int((target_words / max(1, spine.source_word_count)) * 100)

    log.info(
        "chapter_distill_started",
        chapter=section.title,
        source_words=spine.source_word_count,
        target_words=target_words,
    )

    # First call
    prompt = render(
        "distill_chapter.j2",
        chapter_title=section.title,
        spine=spine,
        paragraphs=paragraphs,
        target_words=target_words,
        compression_pct=compression_pct,
        mode=config.distill.mode,
    )
    response = caller.call_raw(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="distill",
        max_tokens=config.distill.max_output_tokens,
    )

    accumulated = response.text
    rounds = 1

    # Write intermediate round
    write_text(out_dir / f"chapter_{chapter_idx}_round_{rounds}.md", accumulated)

    # Continuation loop
    max_rounds = config.distill.max_continuation_rounds
    while (
        _appears_truncated(accumulated, target_words, response.finish_reason)
        and rounds < max_rounds
    ):
        current_wc = _word_count(accumulated)
        remaining_words = max(200, target_words - current_wc)
        remaining_items = _remaining_spine_items(spine, accumulated)

        log.info(
            "continuation_triggered",
            chapter=section.title,
            round=rounds + 1,
            current_words=current_wc,
            remaining_words=remaining_words,
            remaining_items=len(remaining_items),
        )

        continue_prompt = render(
            "distill_continue.j2",
            partial_output=accumulated,
            target_words=target_words,
            current_word_count=current_wc,
            remaining_words=remaining_words,
            remaining_items=remaining_items if remaining_items else ["(all spine items covered — just finish the prose)"],
            voice_sample=spine.voice_sample or "(no voice sample available)",
        )
        response = caller.call_raw(
            stage=STAGE_NAME,
            prompt=continue_prompt,
            model_role="distill",
        )

        prev_wc = _word_count(accumulated)
        accumulated = _merge_continuation(accumulated, response.text)
        delta = _word_count(accumulated) - prev_wc
        rounds += 1

        write_text(out_dir / f"chapter_{chapter_idx}_round_{rounds}.md", accumulated)

        if delta < _MIN_CONTINUATION_DELTA:
            log.info(
                "continuation_stalled",
                chapter=section.title,
                delta=delta,
            )
            break

    final_wc = _word_count(accumulated)
    log.info(
        "chapter_distill_completed",
        chapter=section.title,
        word_count=final_wc,
        target_words=target_words,
        rounds=rounds,
        ratio=f"{final_wc / max(1, target_words):.2f}",
    )

    return ChapterDistillation(
        chapter_title=section.title,
        section_id=section.section_id,
        body_md=accumulated,
        word_count=final_wc,
        continuation_rounds=rounds,
    )


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)
    spine = read_json(working_dir / "03_spine" / "spine.json", Spine)
    classification = read_json(
        working_dir / "02_classify" / "classification.json", BookClassification
    )

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    caller = LLMCaller(working_dir, config)

    # Build a map from section_id → spine
    spine_map: dict[str, ChapterSpine] = {}
    for cs in spine.chapters:
        spine_map[str(cs.section_id)] = cs

    chapter_distillations: list[ChapterDistillation] = []
    total_words = 0

    # Build work items
    work_items = []
    for idx, section in enumerate(doc.toc, 1):
        sid = str(section.section_id)
        chapter_spine = spine_map.get(sid)
        if chapter_spine is None:
            warnings.append(f"no_spine_for_section: '{section.title}' (skipped in spine extraction)")
            continue
        work_items.append((idx, section, chapter_spine))

    def _distill_one(item):
        idx, section, chapter_spine = item
        return idx, _distill_chapter(
            caller=caller, section=section, spine=chapter_spine,
            classification=classification, config=config,
            out_dir=out_dir, chapter_idx=idx,
        )

    # Parallel distillation (subprocess.run releases the GIL)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    indexed = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(_distill_one, w) for w in work_items]
        for future in as_completed(futures):
            indexed.append(future.result())

    indexed.sort(key=lambda x: x[0])
    chapter_distillations = [d for _, d in indexed]
    total_words = sum(d.word_count for d in chapter_distillations)

    full_distillation = Distillation(
        book_slug=doc.book_slug,
        chapters=chapter_distillations,
        total_word_count=total_words,
    )

    write_json(out_dir / "distillation.json", full_distillation)

    elapsed = perf_counter() - t0
    log.info(
        "stage_completed",
        stage=STAGE_NAME,
        chapters=len(chapter_distillations),
        total_words=total_words,
        target_words=spine.total_target_words,
        elapsed=f"{elapsed:.1f}s",
    )

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "chapters_distilled": len(chapter_distillations),
            "total_words": total_words,
            "total_target_words": spine.total_target_words,
            "total_continuation_rounds": sum(
                d.continuation_rounds for d in chapter_distillations
            ),
        },
        warnings=warnings,
        output_paths=[str(out_dir / "distillation.json")],
    )
