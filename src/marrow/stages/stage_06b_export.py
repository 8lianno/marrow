"""Stage 06b: real Obsidian export.

Writes three Markdown files under runs/<slug>/06b_export/ (or into the configured
vault if export.vault is set):

  <slug>_Source.md      — every chunk preceded by chapter context, suffixed by `^chunk_uuid`
  <slug>_Brief.md       — the brief, with [chunk:UUID] tokens translated to [[<slug>_Source#^UUID]]
  <slug>_Evaluation.md  — human-readable scorecard

Citation round-trip invariant: every UUID appearing in Brief.md as `^anchor` resolves
to an anchor in Source.md. The skeleton's parity test asserts this.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

from marrow.config import MarrowConfig
from marrow.io import read_json, read_jsonl, write_text
from marrow.schemas.brief import CITATION_PATTERN, BriefDraft, BriefSection, EvaluationReport
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.run import StageResult

STAGE_NAME = "06b_export"


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()

    chunks = list(read_jsonl(working_dir / "02_chunk" / "chunks.jsonl", ChunkRecord))
    brief = read_json(working_dir / "05b_validate" / "final_brief.json", BriefDraft)
    evaluation = read_json(working_dir / "06a_evaluate" / "composite.json", EvaluationReport)

    slug = brief.book_slug
    out_dir = _resolve_export_dir(working_dir, config, slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    source_md = _render_source(brief.book_title, chunks)
    brief_md = _render_brief(brief, slug)
    eval_md = _render_evaluation(evaluation)

    source_path = out_dir / f"{slug}_Source.md"
    brief_path = out_dir / f"{slug}_Brief.md"
    eval_path = out_dir / f"{slug}_Evaluation.md"

    write_text(source_path, source_md)
    write_text(brief_path, brief_md)
    write_text(eval_path, eval_md)

    # Citation round-trip audit.
    source_anchors = set(_extract_source_anchors(source_md))
    brief_anchors = set(_extract_brief_anchors(brief_md))
    unresolved = brief_anchors - source_anchors

    warnings: list[str] = []
    status = "success"
    if unresolved:
        warnings.append(f"{len(unresolved)} brief citation(s) do not resolve in Source.md")
        status = "warning"

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=perf_counter() - t0,
        status=status,
        counts={
            "source_anchors": len(source_anchors),
            "brief_citations": len(brief_anchors),
            "unresolved_citations": len(unresolved),
        },
        warnings=warnings,
        output_paths=[str(source_path), str(brief_path), str(eval_path)],
    )


def _resolve_export_dir(working_dir: Path, config: MarrowConfig, slug: str) -> Path:
    if config.export.vault:
        vault = Path(config.export.vault).expanduser()
        return vault / "Marrow" / slug
    return working_dir / STAGE_NAME


def _render_source(book_title: str, chunks: list[ChunkRecord]) -> str:
    lines = [f"# {book_title}", "", "*Source corpus, chunk-anchored for citation round-trip.*", ""]

    grouped: dict[tuple[str, ...], list[ChunkRecord]] = defaultdict(list)
    for c in chunks:
        grouped[tuple(c.chapter_path)].append(c)

    for chapter_path, chapter_chunks in grouped.items():
        depth = max(1, len(chapter_path))
        heading = " / ".join(chapter_path) if chapter_path else "Body"
        lines.append(f"{'#' * min(depth + 1, 6)} {heading}")
        lines.append("")
        for c in chapter_chunks:
            lines.append(c.text)
            lines.append(f"^{c.chunk_uuid}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_brief(brief: BriefDraft, slug: str) -> str:
    lines = [
        f"# {brief.book_title} — Brief",
        "",
        f"*Generated {brief.generated_at.isoformat()} • draft v{brief.draft_version} • "
        f"~{brief.estimated_page_count}pp • citations/¶ ≈ {brief.citation_density:.2f}*",
        "",
    ]

    def render_section(section: BriefSection, level: int) -> None:
        lines.append(f"{'#' * min(level + 1, 6)} {section.title}")
        lines.append("")
        translated = _translate_citations(section.body_md, slug)
        lines.append(translated)
        lines.append("")
        for sub in section.subsections:
            render_section(sub, level + 1)

    for section in brief.sections:
        render_section(section, section.level)

    return "\n".join(lines).rstrip() + "\n"


def _translate_citations(body_md: str, slug: str) -> str:
    def repl(m: re.Match[str]) -> str:
        uuid_str = m.group(1)
        return f"[[{slug}_Source#^{uuid_str}]]"

    return CITATION_PATTERN.sub(repl, body_md)


def _render_evaluation(report: EvaluationReport) -> str:
    pass_fail_color = "✅ PASS" if report.verdict == "PASS" else "❌ FAIL"
    return (
        f"# Evaluation: {report.book_slug}\n\n"
        f"**Verdict:** {pass_fail_color}  •  **Brief version:** {report.brief_version}  "
        f"•  *Evaluated {report.evaluated_at.isoformat()}*\n\n"
        f"| Metric | Score |\n"
        f"|---|---|\n"
        f"| BooookScore (coherence) | {report.booookscore:.3f} |\n"
        f"| FActScore (atomic precision) | {report.factscore:.3f} |\n"
        f"| HAMLET root recall | {report.hamlet_root_recall:.3f} |\n"
        f"| HAMLET branch recall | {report.hamlet_branch_recall:.3f} |\n"
        f"| HAMLET leaf recall | {report.hamlet_leaf_recall:.3f} |\n"
        f"| **Composite** | **{report.composite_score:.3f}** |\n\n"
        + (
            "## Failure reasons\n\n" + "\n".join(f"- {r}" for r in report.failure_reasons) + "\n"
            if report.failure_reasons
            else ""
        )
    )


_SOURCE_ANCHOR_RE = re.compile(r"^\^([0-9a-fA-F-]{36})\s*$", re.MULTILINE)
_BRIEF_LINK_RE = re.compile(r"\[\[[^\]#]+#\^([0-9a-fA-F-]{36})\]\]")


def _extract_source_anchors(source_md: str) -> list[UUID]:
    return [UUID(m.group(1)) for m in _SOURCE_ANCHOR_RE.finditer(source_md)]


def _extract_brief_anchors(brief_md: str) -> list[UUID]:
    return [UUID(m.group(1)) for m in _BRIEF_LINK_RE.finditer(brief_md)]
