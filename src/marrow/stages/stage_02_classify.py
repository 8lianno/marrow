"""Stage 02: Classify top-level sections by structural role.

A single Gemini Flash call classifies each section as intro/body/conclusion/
appendix/foreword/other. This determines the compression ratio per section.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from marrow.config import MarrowConfig
from marrow.io import read_json, write_json
from marrow.llm import LLMCaller
from marrow.logging import get_logger
from marrow.prompts import render
from marrow.schemas.classify import (
    COMPRESSION_RATIOS,
    BookClassification,
    SectionClassification,
)
from marrow.schemas.document import CanonicalDocument, SectionNode
from marrow.schemas.run import StageResult

log = get_logger(__name__)
STAGE_NAME = "02_classify"


def _section_preview(section: SectionNode, max_words: int = 200) -> str:
    """First ~200 words of a section's paragraphs."""
    words: list[str] = []
    for p in section.paragraphs:
        words.extend(p.text.split())
        if len(words) >= max_words:
            break
    for sub in section.subsections:
        for p in sub.paragraphs:
            words.extend(p.text.split())
            if len(words) >= max_words:
                break
        if len(words) >= max_words:
            break
    return " ".join(words[:max_words])


def _section_word_count(section: SectionNode) -> int:
    """Total words in a section including subsections."""
    count = sum(len(p.text.split()) for p in section.paragraphs)
    for sub in section.subsections:
        count += _section_word_count(sub)
    return count


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)

    sections_data = []
    for section in doc.toc:
        sections_data.append({
            "title": section.title,
            "section_id": str(section.section_id),
            "preview": _section_preview(section),
        })

    if not sections_data:
        warnings.append("no_sections_found: book has no top-level sections")
        classification = BookClassification(book_slug=doc.book_slug, sections=[])
    else:
        caller = LLMCaller(working_dir, config)
        prompt = render(
            "classify_sections.j2",
            book_title=doc.book_title,
            sections=sections_data,
        )
        response_text = caller.call(
            stage=STAGE_NAME,
            prompt=prompt,
            model_role="spine",  # reuse spine model for this cheap call
        )

        # Parse the response — it should be a JSON array
        try:
            if isinstance(response_text, str):
                raw_list = json.loads(response_text)
            else:
                raw_list = response_text
        except json.JSONDecodeError:
            warnings.append("classify_parse_failed: falling back to all-body")
            raw_list = []

        # Build classification, falling back to "body" for unmatched sections
        section_map: dict[str, dict] = {}
        if isinstance(raw_list, list):
            for item in raw_list:
                sid = item.get("section_id", "")
                section_map[sid] = item

        classifications: list[SectionClassification] = []
        for section in doc.toc:
            sid = str(section.section_id)
            matched = section_map.get(sid, {})
            stype = matched.get("section_type", "body")
            if stype not in COMPRESSION_RATIOS:
                stype = "body"
            classifications.append(
                SectionClassification(
                    section_id=section.section_id,
                    title=section.title,
                    section_type=stype,
                    compression_ratio=COMPRESSION_RATIOS[stype],
                )
            )

        classification = BookClassification(
            book_slug=doc.book_slug,
            sections=classifications,
        )

    # Validate: effective compression ratio should be 0.15–0.50
    if classification.sections and doc.word_count > 0:
        total_target = sum(
            sc.compression_ratio * _section_word_count(section)
            for sc, section in zip(classification.sections, doc.toc)
        )
        effective_ratio = total_target / doc.word_count
        if not (0.15 <= effective_ratio <= 0.50):
            warnings.append(
                f"degenerate_classification: effective ratio {effective_ratio:.2f} "
                f"outside 0.15–0.50 range; falling back to uniform {config.distill.compression_ratio}"
            )
            for sc in classification.sections:
                sc.section_type = "body"
                sc.compression_ratio = config.distill.compression_ratio

    # Pre-flight cost estimation
    # Rough model: input tokens ≈ word_count × 1.3, output ≈ word_count × compression × 1.3
    # Spine: input = full text, output = ~10% of input (JSON)
    # Distill: input = full text + spine, output = compressed text
    # Coherence: input = compressed text + spines, output = small JSON
    input_tokens_est = int(doc.word_count * 1.3)
    output_tokens_est = int(doc.word_count * config.distill.compression_ratio * 1.3)
    spine_cost = input_tokens_est * 0.00015 / 1000 + (input_tokens_est * 0.1) * 0.0006 / 1000
    distill_cost = input_tokens_est * 0.00125 / 1000 + output_tokens_est * 0.005 / 1000
    coherence_cost = output_tokens_est * 0.003 / 1000 + 5000 * 0.015 / 1000
    projected_cost = spine_cost + distill_cost + coherence_cost
    ceiling = config.cost.max_per_book

    log.info(
        "cost_estimate",
        projected_usd=f"${projected_cost:.2f}",
        ceiling_usd=f"${ceiling:.2f}",
        source_words=doc.word_count,
    )

    if projected_cost > ceiling * 1.2:
        from marrow.errors import CostCeilingHit

        raise CostCeilingHit(
            f"Projected cost ${projected_cost:.2f} exceeds ceiling ${ceiling:.2f} by >20%. "
            f"Source: {doc.word_count} words. "
            f"Raise MARROW_COST_MAX_PER_BOOK or use --compression {config.distill.compression_ratio * 0.7:.2f} to reduce scope."
        )

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "classification.json", classification)

    elapsed = perf_counter() - t0
    log.info(
        "stage_completed",
        stage=STAGE_NAME,
        sections=len(classification.sections),
        elapsed=f"{elapsed:.1f}s",
    )

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={"sections_classified": len(classification.sections)},
        warnings=warnings,
        output_paths=[str(out_dir / "classification.json")],
    )
