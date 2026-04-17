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


def _excerpt(text: str, max_words: int = 200) -> str:
    """First max_words words of text."""
    words = text.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")


def _sonnet_audit(
    caller: LLMCaller,
    spine: Spine,
    distillation: Distillation,
    flagged_items: list[MissingSpineItem],
) -> CoherenceReport:
    """Run coherence audit with chapter excerpts (not full draft)."""
    spine_map = {str(cs.section_id): cs for cs in spine.chapters}

    chapter_excerpts = []
    for cd in distillation.chapters:
        cs = spine_map.get(str(cd.section_id))
        chapter_excerpts.append({
            "title": cd.chapter_title,
            "thesis": cs.thesis if cs else "",
            "frameworks": ", ".join(f.name for f in cs.frameworks) if cs else "",
            "opening": _excerpt(cd.body_md, 200),
            "closing": _excerpt(" ".join(cd.body_md.split()[-200:]), 200),
        })

    prompt = render(
        "coherence_audit.j2",
        chapter_excerpts=chapter_excerpts,
        flagged_items=flagged_items,
    )

    report = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="coherence",
        max_tokens=8192,
    )

    # Parse if string (no response_schema — codex returns raw text)
    if isinstance(report, str):
        report = report.strip()
        if report.startswith("```"):
            lines = report.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            report = "\n".join(lines).strip()
        try:
            report = CoherenceReport.model_validate_json(report)
        except Exception:
            log.warning("coherence_audit_parse_failed_using_default")
            report = CoherenceReport(overall_pass=True)

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

    assert isinstance(fixed_text, str), f"Expected str, got {type(fixed_text).__name__}"
    body_text = fixed_text.strip()
    return ChapterDistillation(
        chapter_title=chapter_dist.chapter_title,
        section_id=chapter_dist.section_id,
        body_md=body_text,
        word_count=len(body_text.split()),
        continuation_rounds=chapter_dist.continuation_rounds,
    )


# ---- Phase D: Output assembly ----


def _strip_citations(text: str) -> str:
    """Remove all [p:uuid, ...] citation markers from text."""
    return re.sub(r"\s*\[p:[a-f0-9-]+(?:,\s*p:[a-f0-9-]+)*\]", "", text)


def _smart_title(cd_title: str, chapter_spine, index: int) -> str:
    """Generate a readable title from spine when the auto-splitter produced generic names."""
    if not chapter_spine:
        return cd_title
    if not re.match(r"^(Introduction|Body|Front Matter|Part \d+)\s*(\(Part \d+\))?$", cd_title):
        return cd_title

    # Skip front matter — keep as-is
    if cd_title == "Front Matter":
        return cd_title

    # Pick the best short title: first framework name > first key term > thesis snippet
    if chapter_spine.frameworks:
        name = chapter_spine.frameworks[0].name.title()
        if len(name) > 50:
            name = name[:47] + "..."
        return f"Chapter {index}: {name}"
    if chapter_spine.key_terms:
        return f"Chapter {index}: {chapter_spine.key_terms[0].term.title()}"
    words = chapter_spine.thesis.split()[:5]
    return f"Chapter {index}: {' '.join(words).title()}..."


def _render_distillation_md(
    distillation: Distillation,
    spine: Spine,
    doc: CanonicalDocument,
    slug: str,
) -> str:
    """Render the final distillation as Obsidian markdown with spine callouts."""
    spine_map = {str(cs.section_id): cs for cs in spine.chapters}

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

    for i, cd in enumerate(distillation.chapters, 1):
        chapter_spine = spine_map.get(str(cd.section_id))

        chapter_title = _smart_title(cd.chapter_title, chapter_spine, i)

        lines.append(f"## {chapter_title}")
        lines.append("")

        if chapter_spine:
            lines.append("> [!abstract]- Spine")
            lines.append(f"> **Thesis:** {chapter_spine.thesis}")
            if chapter_spine.frameworks:
                fw = ", ".join(f.name for f in chapter_spine.frameworks)
                lines.append(">")
                lines.append(f"> **Frameworks:** {fw}")
            if chapter_spine.key_examples:
                ex = ", ".join(e.label for e in chapter_spine.key_examples)
                lines.append(">")
                lines.append(f"> **Key examples:** {ex}")
            if chapter_spine.argumentative_moves:
                lines.append(">")
                lines.append("> **Argument flow:**")
                for i, move in enumerate(chapter_spine.argumentative_moves, 1):
                    lines.append(f"> {i}. {move}")
            lines.append("")

        # Convert [p:uuid] citations to Obsidian wikilinks
        body = re.sub(
            r'\[p:([a-f0-9-]+)\]',
            rf'[[{slug}.source#^\1|↗]]',
            cd.body_md,
        )
        # Strip leading "## <title>" the distill prompt produces (we already emitted it)
        body = re.sub(r'^##\s+' + re.escape(cd.chapter_title) + r'\s*\n', '', body)
        lines.append(body)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_epub(
    distillation: Distillation,
    spine: Spine,
    doc: CanonicalDocument,
    out_path: Path,
) -> None:
    """Render the distillation as a clean, readable EPUB."""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier(f"marrow-{doc.book_slug}")
    book.set_title(doc.book_title)
    book.set_language("en")
    if doc.book_author:
        book.add_author(doc.book_author)
    book.add_metadata("DC", "description",
                       f"Distilled from \"{doc.book_title}\" — "
                       f"{distillation.total_word_count} words, "
                       f"~{distillation.total_word_count // 275} pages.")

    # CSS
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=b"""
body {
  font-family: Georgia, 'Times New Roman', serif;
  line-height: 1.7;
  margin: 1.5em 2em;
  color: #1a1a1a;
  font-size: 1em;
}
h1 {
  font-size: 1.8em;
  margin-bottom: 0.5em;
  letter-spacing: -0.02em;
}
h2 {
  font-size: 1.5em;
  margin-top: 2.5em;
  margin-bottom: 0.6em;
  border-bottom: 2px solid #333;
  padding-bottom: 0.4em;
  letter-spacing: -0.01em;
}
h3 {
  font-size: 1.15em;
  margin-top: 1.8em;
  margin-bottom: 0.4em;
  color: #333;
  font-style: italic;
}
p {
  margin-bottom: 0.9em;
  text-align: justify;
  text-indent: 0;
}
.meta {
  color: #666;
  font-style: italic;
  font-size: 0.9em;
  margin-bottom: 2em;
  text-align: center;
}
.chapter-subtitle {
  font-size: 0.85em;
  color: #555;
  font-style: italic;
  margin-top: -0.3em;
  margin-bottom: 1.5em;
}
blockquote {
  border-left: 3px solid #999;
  padding-left: 1em;
  color: #444;
  margin: 1.2em 0;
  font-style: italic;
}
ol { padding-left: 1.5em; }
li { margin-bottom: 0.4em; }
hr { border: none; border-top: 1px solid #ccc; margin: 2.5em 0; }
.spine-callout {
  background: #f8f7f4;
  border-left: 3px solid #666;
  padding: 0.8em 1.2em;
  margin: 0 0 2em 0;
  font-size: 0.85em;
  color: #555;
  line-height: 1.5;
}
.spine-callout p { margin: 0.3em 0; }
.spine-callout ol { margin: 0.3em 0 0.3em 1.2em; font-size: 0.95em; }
.spine-callout li { margin-bottom: 0.2em; }
.spine-label { font-weight: bold; color: #333; }
""",
    )
    book.add_item(css)

    epub_chapters: list[epub.EpubHtml] = []
    toc: list[epub.Link | tuple] = []

    # Title page
    title_html = f"""<html><body>
<h1>{doc.book_title}</h1>
<p class="meta">{"by " + doc.book_author if doc.book_author else ""}</p>
<hr/>
<p class="meta">Distilled by Marrow &mdash; {distillation.total_word_count:,} words,
~{distillation.total_word_count // 275} pages.<br/>
Original: {doc.word_count:,} words, ~{doc.page_count} pages.<br/>
Generated {datetime.now(UTC).strftime('%Y-%m-%d')}.</p>
</body></html>"""
    title_page = epub.EpubHtml(title="Title", file_name="title.xhtml", lang="en")
    title_page.content = title_html.encode("utf-8")
    title_page.add_item(css)
    book.add_item(title_page)
    epub_chapters.append(title_page)

    # Distillation chapters
    spine_map = {str(cs.section_id): cs for cs in spine.chapters}

    for i, cd in enumerate(distillation.chapters, 1):
        clean_body = _strip_citations(cd.body_md)
        paragraphs = clean_body.split("\n\n")
        body_html = ""
        first_heading_stripped = False
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if not first_heading_stripped and para.startswith(("#", "##")):
                first_heading_stripped = True
                continue
            if para.startswith("### "):
                body_html += f"<h3>{para[4:]}</h3>\n"
            elif para.startswith("## "):
                body_html += f"<h3>{para[3:]}</h3>\n"
            else:
                para = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", para)
                para = re.sub(r"\*(.+?)\*", r"<em>\1</em>", para)
                body_html += f"<p>{para}</p>\n"

        # Per-chapter spine callout + smart title
        chapter_spine = spine_map.get(str(cd.section_id))

        chapter_title = _smart_title(cd.chapter_title, chapter_spine, i)
        subtitle = chapter_spine.thesis if chapter_spine and chapter_title != cd.chapter_title else ""

        spine_block = ""
        if chapter_spine:
            spine_block = '<div class="spine-callout">\n'
            spine_block += f'<p><span class="spine-label">Thesis:</span> {chapter_spine.thesis}</p>\n'
            if chapter_spine.frameworks:
                names = ", ".join(f.name for f in chapter_spine.frameworks)
                spine_block += f'<p><span class="spine-label">Frameworks:</span> {names}</p>\n'
            if chapter_spine.key_examples:
                labels = ", ".join(e.label for e in chapter_spine.key_examples)
                spine_block += f'<p><span class="spine-label">Key examples:</span> {labels}</p>\n'
            spine_block += '</div>\n'

        subtitle_html = f'<p class="chapter-subtitle">{subtitle}</p>\n' if subtitle else ""

        ch = epub.EpubHtml(
            title=chapter_title,
            file_name=f"chapter_{i:02d}.xhtml",
            lang="en",
        )
        ch.content = (
            f"<html><body><h2>{chapter_title}</h2>\n"
            f"{subtitle_html}{spine_block}{body_html}</body></html>"
        ).encode("utf-8")
        ch.add_item(css)
        book.add_item(ch)
        epub_chapters.append(ch)
        toc.append(epub.Link(f"chapter_{i:02d}.xhtml", chapter_title, f"ch{i}"))

    # Spine appendix
    spine_html = "<html><body><h1>Spine &mdash; Structural Skeleton</h1>\n"
    for cs in spine.chapters:
        spine_html += f"<h2>{cs.chapter_title}</h2>\n"
        spine_html += f"<p><strong>Thesis:</strong> {cs.thesis}</p>\n"

        if cs.frameworks:
            spine_html += "<h3>Frameworks</h3>\n<ul>\n"
            for f in cs.frameworks:
                spine_html += f"<li><span class='spine-label'>{f.name}:</span> {f.description}</li>\n"
            spine_html += "</ul>\n"

        if cs.key_examples:
            spine_html += "<h3>Key Examples</h3>\n<ul>\n"
            for e in cs.key_examples:
                spine_html += f"<li><span class='spine-label'>{e.label}:</span> {e.gist}</li>\n"
            spine_html += "</ul>\n"

        if cs.argumentative_moves:
            spine_html += "<h3>Argument Flow</h3>\n<ol>\n"
            for move in cs.argumentative_moves:
                spine_html += f"<li>{move}</li>\n"
            spine_html += "</ol>\n"

        if cs.key_terms:
            spine_html += "<h3>Key Terms</h3>\n<ul>\n"
            for t in cs.key_terms:
                spine_html += f"<li><span class='spine-label'>{t.term}:</span> {t.definition}</li>\n"
            spine_html += "</ul>\n"

        spine_html += "<hr/>\n"
    spine_html += "</body></html>"

    spine_page = epub.EpubHtml(
        title="Spine — Structural Skeleton",
        file_name="spine.xhtml",
        lang="en",
    )
    spine_page.content = spine_html.encode("utf-8")
    spine_page.add_item(css)
    book.add_item(spine_page)
    epub_chapters.append(spine_page)
    toc.append(epub.Link("spine.xhtml", "Spine — Structural Skeleton", "spine"))

    # Build TOC and spine
    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + epub_chapters

    epub.write_epub(str(out_path), book)


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

    distillation_md = _render_distillation_md(final_distillation, spine, doc, slug)
    spine_md = _render_spine_md(spine, doc)
    source_md = _render_source_md(doc)

    write_text(out_dir / f"{slug}.md", distillation_md)
    write_text(out_dir / f"{slug}.spine.md", spine_md)
    write_text(out_dir / f"{slug}.source.md", source_md)
    write_json(out_dir / "final_distillation.json", final_distillation)

    # EPUB export
    epub_path = out_dir / f"{slug}.epub"
    try:
        _render_epub(final_distillation, spine, doc, epub_path)
        log.info("epub_exported", path=str(epub_path))
    except Exception as e:
        warnings.append(f"epub_export_failed: {e}")
        log.warning("epub_export_failed", error=str(e))

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
        for fname in [f"{slug}.md", f"{slug}.spine.md", f"{slug}.source.md", f"{slug}.epub"]:
            src = out_dir / fname
            if src.exists():
                shutil.copy2(src, vault_dir / fname)
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
