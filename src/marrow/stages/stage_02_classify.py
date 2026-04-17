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
