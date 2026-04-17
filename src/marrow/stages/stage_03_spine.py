"""Stage 03: Extract structural spine per chapter.

One Gemini Flash call per chapter extracts thesis, frameworks, examples,
argumentative moves, key terms, and a voice sample. The spine is the
selection artifact — everything downstream writes against it.
"""

from __future__ import annotations

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
from marrow.schemas.document import CanonicalDocument, ParagraphNode, SectionNode
from marrow.schemas.run import StageResult
from marrow.schemas.spine import ChapterSpine, Spine

log = get_logger(__name__)
STAGE_NAME = "03_spine"


def _flatten_paragraphs(section: SectionNode) -> list[ParagraphNode]:
    """Collect all paragraphs from a section and its subsections in order."""
    paragraphs: list[ParagraphNode] = list(section.paragraphs)
    for sub in section.subsections:
        paragraphs.extend(_flatten_paragraphs(sub))
    return paragraphs


def _section_word_count(section: SectionNode) -> int:
    return sum(len(p.text.split()) for p in _flatten_paragraphs(section))


def _extract_spine(caller: LLMCaller, prompt: str, stage: str) -> ChapterSpine:
    """Call the LLM without response_schema to avoid structured output truncation,
    then parse the JSON manually."""
    import json as _json

    raw_text = caller.call(
        stage=stage,
        prompt=prompt,
        model_role="spine",
        max_tokens=16384,
    )
    # Strip code fences and parse
    text = raw_text if isinstance(raw_text, str) else str(raw_text)
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return ChapterSpine.model_validate_json(text)


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)
    classification = read_json(
        working_dir / "02_classify" / "classification.json", BookClassification
    )

    # Build a map of section_id → compression_ratio
    ratio_map: dict[str, float] = {}
    for sc in classification.sections:
        ratio_map[str(sc.section_id)] = sc.compression_ratio

    caller = LLMCaller(working_dir, config)
    chapter_spines: list[ChapterSpine] = []
    total_source = 0
    total_target = 0

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build work items (cheap prep)
    work_items = []
    for idx, section in enumerate(doc.toc, 1):
        paragraphs = _flatten_paragraphs(section)
        word_count = sum(len(p.text.split()) for p in paragraphs)
        compression = ratio_map.get(str(section.section_id), config.distill.compression_ratio)
        target_words = max(100, int(word_count * compression))

        if word_count < 50:
            warnings.append(f"skipping_short_section: '{section.title}' has only {word_count} words")
            continue
        work_items.append((idx, section, paragraphs, word_count, compression, target_words))

    def _extract_one(item):
        idx, section, paragraphs, wc, comp, tgt = item
        log.info("chapter_spine_extracting", chapter=section.title, index=idx, word_count=wc)

        prompt = render(
            "spine_extract.j2",
            chapter=section,
            chapter_index=idx,
            word_count=wc,
            paragraphs=paragraphs,
            section_id=str(section.section_id),
            compression_pct=int(comp * 100),
        )

        try:
            result = _extract_spine(caller, prompt, STAGE_NAME)
        except Exception:
            log.warning("chapter_spine_retrying", chapter=section.title)
            retry_prompt = (
                prompt
                + "\n\nIMPORTANT: Your previous response was not valid JSON. "
                "Return ONLY a JSON object matching the schema above. No preamble, "
                "no markdown code fences, no commentary. Start with { and end with }."
            )
            try:
                result = _extract_spine(caller, retry_prompt, STAGE_NAME)
            except Exception as e:
                log.warning("chapter_spine_failed", chapter=section.title, error=str(e))
                failed_dir = out_dir / "failed"
                failed_dir.mkdir(parents=True, exist_ok=True)
                write_text(failed_dir / f"chapter_{idx}_error.txt", str(e))
                return (idx, section, None, wc, tgt)

        result.source_word_count = wc
        result.target_word_count = tgt
        result.section_id = section.section_id

        log.info(
            "chapter_spine_extracted",
            chapter=section.title,
            frameworks=len(result.frameworks),
            examples=len(result.key_examples),
            moves=len(result.argumentative_moves),
            terms=len(result.key_terms),
        )
        return (idx, section, result, wc, tgt)

    # Parallel extraction (subprocess.run releases the GIL)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    indexed_results = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(_extract_one, w) for w in work_items]
        for future in as_completed(futures):
            indexed_results.append(future.result())

    # Re-sort by chapter index and collect results
    indexed_results.sort(key=lambda x: x[0])
    for idx, section, result, wc, tgt in indexed_results:
        if result is None:
            warnings.append(f"spine_extraction_failed: '{section.title}'")
            continue
        chapter_spines.append(result)
        total_source += wc
        total_target += tgt

    full_spine = Spine(
        book_slug=doc.book_slug,
        book_title=doc.book_title,
        chapters=chapter_spines,
        total_source_words=total_source,
        total_target_words=total_target,
    )

    write_json(out_dir / "spine.json", full_spine)

    elapsed = perf_counter() - t0
    log.info(
        "stage_completed",
        stage=STAGE_NAME,
        chapters=len(chapter_spines),
        total_source_words=total_source,
        total_target_words=total_target,
        elapsed=f"{elapsed:.1f}s",
    )

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "chapters": len(chapter_spines),
            "total_frameworks": sum(len(s.frameworks) for s in chapter_spines),
            "total_examples": sum(len(s.key_examples) for s in chapter_spines),
            "total_source_words": total_source,
            "total_target_words": total_target,
        },
        warnings=warnings,
        output_paths=[str(out_dir / "spine.json")],
    )
