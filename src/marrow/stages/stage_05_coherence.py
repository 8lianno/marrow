"""Stage 05: Coherence audit + output assembly.

Phase A: Deterministic spine coverage check (fuzzy match).
Phase B: Sonnet audit for voice drift, broken threads, redundancy.
Phase C: Flash fix-ups for chapters with critical issues.
Phase D: Assemble final Obsidian markdown output.
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from marrow.config import MarrowConfig
from marrow.io import read_json, write_json, write_text
from marrow.llm import LLMCaller
from marrow.logging import get_logger
from marrow.prompts import render
from marrow.schemas.coherence import (
    BrokenThread,
    CoherenceReport,
    MissingSpineItem,
    Redundancy,
    VoiceDrift,
)
from marrow.schemas.distill import ChapterDistillation, Distillation
from marrow.schemas.document import CanonicalDocument
from marrow.schemas.run import StageResult
from marrow.schemas.spine import ChapterSpine, Spine

log = get_logger(__name__)
STAGE_NAME = "05_coherence"


# ---- Phase A: Deterministic spine coverage ----


def _normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fuzzy_contains(haystack: str, needle: str, threshold: float) -> bool:
    """Check if needle appears in haystack using progressively looser matching."""
    haystack_lower = haystack.lower()
    needle_lower = needle.lower()

    # 1. Exact substring match (case-insensitive)
    if needle_lower in haystack_lower:
        return True

    # 2. Normalized match: strip punctuation, collapse whitespace
    hay_norm = _normalize_text(haystack)
    needle_norm = _normalize_text(needle)
    if needle_norm in hay_norm:
        return True

    # 3. Token-window match: check if ≥70% of the needle's words appear
    #    within any 50-word window of the haystack
    needle_words = needle_norm.split()
    if not needle_words:
        return True

    hay_words = hay_norm.split()
    window_size = 50
    required = max(1, int(len(needle_words) * threshold))

    for start in range(0, max(1, len(hay_words) - window_size + 1)):
        window = set(hay_words[start : start + window_size])
        matched = sum(1 for w in needle_words if w in window)
        if matched >= required:
            return True

    return False


def _check_spine_coverage(
    spine: Spine,
    distillation: Distillation,
    threshold: float,
) -> list[MissingSpineItem]:
    """Deterministic check: which spine items don't appear in the distillation?"""
    flagged: list[MissingSpineItem] = []

    dist_map: dict[str, str] = {}
    for cd in distillation.chapters:
        # Strip [p:uuid] citation tokens before matching
        clean_text = re.sub(r"\[p:[a-f0-9-]+\]", "", cd.body_md)
        dist_map[str(cd.section_id)] = clean_text

    for cs in spine.chapters:
        chapter_text = dist_map.get(str(cs.section_id), "")
        if not chapter_text:
            for f in cs.frameworks:
                flagged.append(MissingSpineItem(
                    chapter_title=cs.chapter_title,
                    item_type="framework",
                    item_description=f.name,
                    severity="critical",
                ))
            continue

        for f in cs.frameworks:
            if not _fuzzy_contains(chapter_text, f.name, threshold):
                flagged.append(MissingSpineItem(
                    chapter_title=cs.chapter_title,
                    item_type="framework",
                    item_description=f"{f.name}: {f.description}",
                    severity="critical",
                ))

        for e in cs.key_examples:
            if not _fuzzy_contains(chapter_text, e.label, threshold):
                flagged.append(MissingSpineItem(
                    chapter_title=cs.chapter_title,
                    item_type="example",
                    item_description=f"{e.label}: {e.gist}",
                    severity="critical" if e.is_load_bearing else "minor",
                ))

        for t in cs.key_terms:
            if not _fuzzy_contains(chapter_text, t.term, threshold):
                flagged.append(MissingSpineItem(
                    chapter_title=cs.chapter_title,
                    item_type="key_term",
                    item_description=f"{t.term}: {t.definition}",
                    severity="minor",
                ))

    return flagged


# ---- Phase B: Sonnet audit ----


def _assemble_draft(distillation: Distillation) -> str:
    """Assemble all chapters into a single markdown string."""
    parts: list[str] = []
    for cd in distillation.chapters:
        parts.append(cd.body_md)
        parts.append("")
    return "\n\n".join(parts)


def _sonnet_audit(
    caller: LLMCaller,
    spine: Spine,
    distillation: Distillation,
    flagged_items: list[MissingSpineItem],
) -> CoherenceReport:
    """Run the Sonnet coherence audit."""
    draft = _assemble_draft(distillation)

    prompt = render(
        "coherence_audit.j2",
        spines=spine.chapters,
        draft=draft,
        flagged_items=flagged_items,
    )

    report = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="coherence",
        response_schema=CoherenceReport,
        max_tokens=8192,
    )
    return report


# ---- Phase C: Flash fix-ups ----


def _fix_chapter(
    caller: LLMCaller,
    chapter_dist: ChapterDistillation,
    chapter_spine: ChapterSpine,
    fix_instructions: list[str],
) -> ChapterDistillation:
    """Re-distill a chapter with specific fix instructions."""
    prompt = render(
        "coherence_fix.j2",
        chapter_title=chapter_dist.chapter_title,
        spine=chapter_spine,
        current_distillation=chapter_dist.body_md,
        fix_instructions=fix_instructions,
        target_words=chapter_spine.target_word_count,
    )

    fixed_text = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="distill",
        max_tokens=chapter_spine.target_word_count * 2,  # generous ceiling
    )

    body_text = fixed_text if isinstance(fixed_text, str) else str(fixed_text)
    return ChapterDistillation(
        chapter_title=chapter_dist.chapter_title,
        section_id=chapter_dist.section_id,
        body_md=body_text,
        word_count=len(body_text.split()),
        continuation_rounds=chapter_dist.continuation_rounds,
    )


# ---- Phase D: Output assembly ----


def _render_distillation_md(
    distillation: Distillation,
    doc: CanonicalDocument,
    slug: str,
) -> str:
    """Render the final distillation as Obsidian markdown."""
    lines: list[str] = []
    lines.append(f"# {doc.book_title}")
    lines.append("")
    if doc.book_author:
        lines.append(f"*Distilled from \"{doc.book_title}\" by {doc.book_author}.*")
    else:
        lines.append(f"*Distilled from \"{doc.book_title}\".*")
    lines.append(f"*~{distillation.total_word_count // 275} pages. "
                  f"Generated {datetime.now(UTC).strftime('%Y-%m-%d')}.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    for cd in distillation.chapters:
        # Convert [p:uuid] citations to Obsidian wikilinks
        body = re.sub(
            r'\[p:([a-f0-9-]+)\]',
            rf'[[{slug}.source#^\1|↗]]',
            cd.body_md,
        )
        lines.append(body)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_spine_md(spine: Spine, doc: CanonicalDocument) -> str:
    """Render the spine as a human-readable markdown document."""
    lines: list[str] = []
    lines.append(f"# {doc.book_title} — Spine")
    lines.append("")

    for cs in spine.chapters:
        lines.append(f"## {cs.chapter_title}")
        lines.append("")
        lines.append(f"**Thesis:** {cs.thesis}")
        lines.append("")

        if cs.frameworks:
            lines.append("**Frameworks:**")
            for f in cs.frameworks:
                lines.append(f"- **{f.name}**: {f.description}")
            lines.append("")

        if cs.key_examples:
            lines.append("**Key examples:**")
            for e in cs.key_examples:
                lines.append(f"- **{e.label}**: {e.gist}")
            lines.append("")

        if cs.argumentative_moves:
            lines.append("**Argument flow:**")
            for i, move in enumerate(cs.argumentative_moves, 1):
                lines.append(f"{i}. {move}")
            lines.append("")

        if cs.key_terms:
            lines.append("**Key terms:**")
            for t in cs.key_terms:
                lines.append(f"- **{t.term}**: {t.definition}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_source_md(doc: CanonicalDocument) -> str:
    """Render the original text with ^paragraph_id anchors."""
    lines: list[str] = [f"# {doc.book_title}", ""]

    def render_section(section, depth: int) -> None:
        lines.append(f"{'#' * min(depth + 1, 6)} {section.title}")
        lines.append("")
        for p in section.paragraphs:
            if p.is_table and p.table_grid:
                lines.append("| " + " | ".join(p.table_grid[0]) + " |")
                lines.append("|" + "|".join("---" for _ in p.table_grid[0]) + "|")
                for row in p.table_grid[1:]:
                    lines.append("| " + " | ".join(row) + " |")
            else:
                lines.append(p.text)
            lines.append(f"^{p.paragraph_id}")
            lines.append("")
        for sub in section.subsections:
            render_section(sub, depth + 1)

    for section in doc.toc:
        render_section(section, 1)

    return "\n".join(lines).rstrip() + "\n"


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)
    spine = read_json(working_dir / "03_spine" / "spine.json", Spine)
    distillation = read_json(
        working_dir / "04_distill" / "distillation.json", Distillation
    )

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    caller = LLMCaller(working_dir, config)

    # Phase A: Deterministic coverage check
    log.info("coherence_check_deterministic")
    flagged = _check_spine_coverage(
        spine, distillation, config.coherence.similarity_threshold
    )

    critical_count = sum(1 for f in flagged if f.severity == "critical")
    log.info(
        "coherence_deterministic_result",
        flagged_total=len(flagged),
        flagged_critical=critical_count,
    )

    # Phase B: Sonnet audit
    log.info("coherence_audit_sonnet")
    report = _sonnet_audit(caller, spine, distillation, flagged)

    write_json(out_dir / "coherence_report.json", report)

    # Phase C: Fix-ups
    final_distillation = Distillation(
        book_slug=distillation.book_slug,
        chapters=list(distillation.chapters),
        total_word_count=distillation.total_word_count,
    )

    if not report.overall_pass:
        spine_map: dict[str, ChapterSpine] = {
            str(cs.section_id): cs for cs in spine.chapters
        }

        # Collect fix instructions per chapter
        chapter_fixes: dict[str, list[str]] = {}
        for item in report.missing_spine_items:
            if item.severity == "critical":
                chapter_fixes.setdefault(item.chapter_title, []).append(
                    f"Missing {item.item_type}: {item.item_description}"
                )
        for bt in report.broken_threads:
            for ch in bt.chapters_involved:
                chapter_fixes.setdefault(ch, []).append(
                    f"Broken thread: {bt.description}"
                )
        for vd in report.voice_drift:
            chapter_fixes.setdefault(vd.chapter_title, []).append(
                f"Voice drift: {vd.description}"
            )

        fix_count = 0
        for i, cd in enumerate(final_distillation.chapters):
            fixes = chapter_fixes.get(cd.chapter_title, [])
            if not fixes:
                continue
            if fix_count >= config.coherence.max_fix_rounds:
                warnings.append(
                    f"fix_cap_reached: skipping fixes for '{cd.chapter_title}'"
                )
                break

            chapter_spine = spine_map.get(str(cd.section_id))
            if chapter_spine is None:
                continue

            log.info("fixup_triggered", chapter=cd.chapter_title, fixes=len(fixes))
            fixed = _fix_chapter(caller, cd, chapter_spine, fixes)
            final_distillation.chapters[i] = fixed
            fix_count += 1

        # Recalculate total word count
        final_distillation.total_word_count = sum(
            cd.word_count for cd in final_distillation.chapters
        )

    # Phase D: Assemble output
    slug = doc.book_slug

    distillation_md = _render_distillation_md(final_distillation, doc, slug)
    spine_md = _render_spine_md(spine, doc)
    source_md = _render_source_md(doc)

    write_text(out_dir / f"{slug}.md", distillation_md)
    write_text(out_dir / f"{slug}.spine.md", spine_md)
    write_text(out_dir / f"{slug}.source.md", source_md)
    write_json(out_dir / "final_distillation.json", final_distillation)

    # Build manifest
    manifest = {
        "book_title": doc.book_title,
        "book_author": doc.book_author,
        "source_pages": doc.page_count,
        "source_words": doc.word_count,
        "distillation_words": final_distillation.total_word_count,
        "distillation_pages": final_distillation.total_word_count // 275,
        "compression_ratio": final_distillation.total_word_count / max(1, doc.word_count),
        "chapters": len(final_distillation.chapters),
        "coherence_pass": report.overall_pass,
        "fixes_applied": fix_count if not report.overall_pass else 0,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    write_json(out_dir / "manifest.json", manifest)

    # Copy to Obsidian vault if configured
    if config.export.vault:
        vault_dir = Path(config.export.vault) / "Marrow" / slug
        vault_dir.mkdir(parents=True, exist_ok=True)
        for fname in [f"{slug}.md", f"{slug}.spine.md", f"{slug}.source.md"]:
            shutil.copy2(out_dir / fname, vault_dir / fname)
        log.info("exported_to_vault", vault=str(vault_dir))

    elapsed = perf_counter() - t0
    log.info(
        "stage_completed",
        stage=STAGE_NAME,
        total_words=final_distillation.total_word_count,
        coherence_pass=report.overall_pass,
        elapsed=f"{elapsed:.1f}s",
    )

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "distillation_words": final_distillation.total_word_count,
            "distillation_pages": final_distillation.total_word_count // 275,
            "flagged_items": len(flagged),
            "critical_missing": critical_count,
            "fixes_applied": fix_count if not report.overall_pass else 0,
        },
        warnings=warnings,
        output_paths=[
            str(out_dir / f"{slug}.md"),
            str(out_dir / f"{slug}.spine.md"),
            str(out_dir / f"{slug}.source.md"),
            str(out_dir / "manifest.json"),
        ],
    )
