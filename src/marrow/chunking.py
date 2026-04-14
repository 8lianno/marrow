"""Chunking primitives: sentence segmentation + chunk-boundary planning.

Stage 02 uses these to produce token-budget-respecting, sentence-aligned chunks
that the embedder (see marrow.embed) can then late-chunk-pool.

Late chunking concept (from Jina): rather than embedding each chunk in
isolation, we tokenize the entire document once, run the model on that long
context, and mean-pool token embeddings within each chunk's token range. This
preserves anaphoric context (pronouns, references) across chunk boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Lightweight regex-based sentence splitter. Replaced by NLTK punkt only when
# benchmarks justify the dependency.
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'\(])|(?<=[.!?])$")


def split_sentences(text: str) -> list[str]:
    """Split text into sentences via punctuation + capital-letter heuristics."""
    text = text.strip()
    if not text:
        return []
    parts = _SENT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def approx_token_count(text: str) -> int:
    """4 chars/token approximation. Replaced by Jina tokenizer in late chunking."""
    return max(1, len(text) // 4)


@dataclass
class PlannedChunk:
    """A chunk of sentences with stable text and provenance.

    The actual ChunkRecord (with embedding + UUID) is built in the stage.
    """

    text: str
    sentence_count: int
    token_count: int
    chapter_path: list[str]
    paragraph_ids: list[str]  # serialized UUIDs
    page_start: int
    page_end: int
    window_index: int


def plan_chunks(
    paragraphs: list[tuple[list[str], str, str, int]],  # (chapter_path, text, paragraph_id, page)
    *,
    target_tokens: int,
    overlap_pct: float,
) -> list[PlannedChunk]:
    """Group paragraphs into chunks honoring target token budget + chapter boundaries.

    Rules:
    - Never split mid-paragraph; chunks are paragraph-aligned.
    - Never bridge across chapters (chapter_path[0] differs).
    - Within a chapter, accumulate paragraphs until target_tokens exceeded.
    - Overlap: next chunk's start index moves back `overlap_pct * len(prev_chunk)`
      paragraphs from the previous chunk's end (no double-flush of pure overlap).
    """
    chunks: list[PlannedChunk] = []
    n = len(paragraphs)
    i = 0
    window_index = 0

    while i < n:
        chunk_paras: list[tuple[list[str], str, str, int]] = []
        chunk_tokens = 0
        chapter_anchor = paragraphs[i][0][0] if paragraphs[i][0] else ""
        j = i
        while j < n:
            p_chapter, p_text, _, _ = paragraphs[j]
            chapter_top = p_chapter[0] if p_chapter else ""
            if chunk_paras and chapter_top != chapter_anchor:
                break  # chapter boundary forces split
            para_tokens = approx_token_count(p_text)
            if chunk_paras and chunk_tokens + para_tokens > target_tokens:
                break
            chunk_paras.append(paragraphs[j])
            chunk_tokens += para_tokens
            j += 1

        if not chunk_paras:
            # Single paragraph exceeds budget; emit it alone.
            chunk_paras = [paragraphs[i]]
            j = i + 1

        chapter_path = chunk_paras[0][0]
        text = "\n\n".join(p[1] for p in chunk_paras)
        chunks.append(
            PlannedChunk(
                text=text,
                sentence_count=sum(max(1, len(split_sentences(p[1]))) for p in chunk_paras),
                token_count=approx_token_count(text),
                chapter_path=chapter_path,
                paragraph_ids=[p[2] for p in chunk_paras],
                page_start=min(p[3] for p in chunk_paras),
                page_end=max(p[3] for p in chunk_paras),
                window_index=window_index,
            )
        )
        window_index += 1

        if overlap_pct > 0 and len(chunk_paras) > 1:
            overlap_n = max(1, int(len(chunk_paras) * overlap_pct))
            i = j - overlap_n
        else:
            i = j

    return chunks
