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

    for idx, section in enumerate(doc.toc, 1):
        paragraphs = _flatten_paragraphs(section)
        word_count = sum(len(p.text.split()) for p in paragraphs)
        compression = ratio_map.get(str(section.section_id), config.distill.compression_ratio)
        target_words = max(100, int(word_count * compression))

        if word_count < 50:
            warnings.append(f"skipping_short_section: '{section.title}' has only {word_count} words")
            continue

        log.info(
            "chapter_spine_extracting",
            chapter=section.title,
            index=idx,
            word_count=word_count,
        )

        prompt = render(
            "spine_extract.j2",
            chapter=section,
            chapter_index=idx,
            word_count=word_count,
            paragraphs=paragraphs,
            section_id=str(section.section_id),
            compression_pct=int(compression * 100),
        )

        try:
            spine_result = caller.call(
                stage=STAGE_NAME,
                prompt=prompt,
                model_role="spine",
                response_schema=ChapterSpine,
            )
        except Exception as e:
            log.warning(
                "chapter_spine_failed",
                chapter=section.title,
                error=str(e),
            )
            # Save raw failure for inspection
            failed_dir = out_dir / "failed"
            failed_dir.mkdir(parents=True, exist_ok=True)
            write_text(failed_dir / f"chapter_{idx}_error.txt", str(e))
            warnings.append(f"spine_extraction_failed: '{section.title}' — {e}")
            continue

        # Ensure target words are set correctly (model may not return these)
        spine_result.source_word_count = word_count
        spine_result.target_word_count = target_words
        spine_result.section_id = section.section_id

        chapter_spines.append(spine_result)
        total_source += word_count
        total_target += target_words

        log.info(
            "chapter_spine_extracted",
            chapter=section.title,
            frameworks=len(spine_result.frameworks),
            examples=len(spine_result.key_examples),
            moves=len(spine_result.argumentative_moves),
            terms=len(spine_result.key_terms),
        )

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
