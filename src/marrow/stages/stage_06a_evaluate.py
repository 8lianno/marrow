"""Stage 06a: multi-metric evaluation (M6 real implementation).

Three signals, one composite verdict:

- **BooookScore** (coherence, LLM): per-chapter coherence rating 0.0-1.0,
  averaged across chapters.
- **FActScore** (atomic precision, LLM): sample brief paragraphs containing
  citations; for each, verify that the cited source chunk supports the claim.
  Returns supported / total. Length penalty applied if brief is too short
  to fairly sample.
- **HAMLET** (recall, deterministic): fraction of source structure covered
  in the brief. Three levels:
    - root: was every chapter mentioned in the brief?
    - branch: what fraction of chunks are cited?
    - leaf: what fraction of (claim_text → cited_chunk_uuid) survive in the
      brief's citations?

Composite = weighted average; verdict PASS if HAMLET leaf-recall ≥ threshold
AND BooookScore ≥ threshold AND FActScore ≥ threshold.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

from marrow.config import MarrowConfig
from marrow.io import read_json, read_jsonl, write_json
from marrow.llm import LLMCaller
from marrow.logging import get_logger
from marrow.prompts import render
from marrow.schemas.brief import (
    CITATION_PATTERN,
    BriefDraft,
    BriefSection,
    CoherenceScore,
    EvaluationReport,
    FactVerification,
)
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.claim import AtomicClaim
from marrow.schemas.document import CanonicalDocument
from marrow.schemas.run import StageResult

log = get_logger(__name__)
STAGE_NAME = "06a_evaluate"

# Cap LLM calls in M6 walking; full evaluation lands in M6.5+.
MAX_FACT_SAMPLES = 20

# Composite weights (sum to 1.0).
W_BOOOOK = 0.20
W_FACT = 0.30
W_HAMLET_LEAF = 0.30
W_HAMLET_BRANCH = 0.10
W_HAMLET_ROOT = 0.10


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    doc = read_json(working_dir / "01_ingest" / "document.json", CanonicalDocument)
    chunks = list(read_jsonl(working_dir / "02_chunk" / "chunks.jsonl", ChunkRecord))
    all_claims = list(read_jsonl(working_dir / "04_claims" / "claims.jsonl", AtomicClaim))
    claims = [c for c in all_claims if c.is_duplicate_of is None]
    final_brief = read_json(working_dir / "05b_validate" / "final_brief.json", BriefDraft)

    caller = LLMCaller(working_dir, config)

    # 1. BooookScore — per-chapter coherence.
    booookscore, booook_per_section = _booookscore(caller, final_brief)

    # 2. FActScore — sample claims supported by source.
    factscore, length_penalty = _factscore(caller, final_brief, chunks)

    # 3. HAMLET — deterministic recall metrics.
    root_recall, branch_recall, leaf_recall = _hamlet(doc, chunks, claims, final_brief)

    composite = (
        W_BOOOOK * booookscore
        + W_FACT * factscore
        + W_HAMLET_LEAF * leaf_recall
        + W_HAMLET_BRANCH * branch_recall
        + W_HAMLET_ROOT * root_recall
    )

    # Verdict.
    failure_reasons: list[str] = []
    if booookscore < config.evaluate.booookscore_threshold:
        failure_reasons.append(
            f"BooookScore {booookscore:.3f} < threshold {config.evaluate.booookscore_threshold}"
        )
    if factscore < config.evaluate.factscore_threshold:
        failure_reasons.append(
            f"FActScore {factscore:.3f} < threshold {config.evaluate.factscore_threshold}"
        )
    if leaf_recall < config.evaluate.hamlet_leaf_threshold:
        failure_reasons.append(
            f"HAMLET leaf recall {leaf_recall:.3f} < threshold "
            f"{config.evaluate.hamlet_leaf_threshold}"
        )
    verdict = "PASS" if not failure_reasons else "FAIL"

    report = EvaluationReport(
        book_slug=final_brief.book_slug,
        brief_version=final_brief.draft_version,
        booookscore=booookscore,
        factscore=factscore,
        factscore_length_penalty_applied=length_penalty,
        hamlet_root_recall=root_recall,
        hamlet_branch_recall=branch_recall,
        hamlet_leaf_recall=leaf_recall,
        composite_score=composite,
        verdict=verdict,
        failure_reasons=failure_reasons,
        evaluated_at=datetime.now(UTC),
    )

    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        out_dir / "booookscore.json", {"score": booookscore, "per_section": booook_per_section}
    )
    write_json(
        out_dir / "factscore.json",
        {
            "score": factscore,
            "length_penalty_applied": length_penalty,
        },
    )
    write_json(
        out_dir / "hamlet.json",
        {
            "root_recall": root_recall,
            "branch_recall": branch_recall,
            "leaf_recall": leaf_recall,
        },
    )
    write_json(out_dir / "composite.json", report)

    if verdict == "FAIL":
        warnings.append(f"verdict_FAIL: {len(failure_reasons)} threshold(s) missed")

    elapsed = perf_counter() - t0

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "verdict_pass": 1 if verdict == "PASS" else 0,
            "composite_pct": int(composite * 100),
            "booookscore_pct": int(booookscore * 100),
            "factscore_pct": int(factscore * 100),
            "hamlet_leaf_pct": int(leaf_recall * 100),
        },
        warnings=warnings,
        output_paths=[str(out_dir / "composite.json")],
    )


# ---- BooookScore (coherence) ----


def _booookscore(caller: LLMCaller, brief: BriefDraft) -> tuple[float, dict[str, float]]:
    if not brief.sections:
        return 0.0, {}
    per_section: dict[str, float] = {}
    for section in brief.sections:
        try:
            score = _score_one_section(caller, section)
        except Exception as e:
            log.warning("coherence_score_failed", section=section.title, error=str(e))
            score = 0.5
        per_section[section.title] = score
    avg = sum(per_section.values()) / len(per_section)
    return avg, per_section


def _score_one_section(caller: LLMCaller, section: BriefSection) -> float:
    prompt = render("coherence_score.j2", chapter_title=section.title, body_md=section.body_md)
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="validation",
        response_schema=CoherenceScore,
    )
    if isinstance(raw, CoherenceScore):
        return raw.score
    return _salvage_score(raw)


def _salvage_score(raw: str) -> float:
    if not isinstance(raw, str):
        return 0.5
    try:
        parsed = CoherenceScore.model_validate_json(raw)
        return parsed.score
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return CoherenceScore.model_validate(json.loads(raw[start : end + 1])).score
        except Exception:
            pass
    return 0.5


# ---- FActScore (atomic precision) ----


def _factscore(
    caller: LLMCaller, brief: BriefDraft, chunks: list[ChunkRecord]
) -> tuple[float, bool]:
    """Sample sentences with citations; verify each against its cited chunk.

    Length penalty applied per FActScore convention (gamma=10): if number of
    citations < gamma, scale the score down proportionally so under-developed
    briefs don't post inflated precision scores.
    """
    chunk_by_uuid = {c.chunk_uuid: c for c in chunks}

    samples = _sample_cited_sentences(brief)
    gamma = 10
    length_penalty = len(samples) < gamma

    if not samples:
        return 0.0, True

    rng = random.Random(42)
    if len(samples) > MAX_FACT_SAMPLES:
        samples = rng.sample(samples, MAX_FACT_SAMPLES)

    supported = 0
    counted = 0
    for sentence, chunk_uuid in samples:
        chunk = chunk_by_uuid.get(chunk_uuid)
        if chunk is None:
            continue  # citation points to nonexistent chunk; HAMLET catches this
        try:
            verdict = _verify_one_fact(caller, sentence, chunk)
        except Exception as e:
            log.warning("fact_verify_failed", chunk_uuid=str(chunk_uuid), error=str(e))
            continue
        counted += 1
        if verdict.is_supported:
            supported += 1

    if counted == 0:
        return 0.0, length_penalty

    raw_score = supported / counted
    if length_penalty:
        raw_score *= len(samples) / gamma
    return raw_score, length_penalty


def _verify_one_fact(caller: LLMCaller, sentence: str, chunk: ChunkRecord) -> FactVerification:
    prompt = render("fact_verify.j2", claim_text=sentence, chunk_text=chunk.text)
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="validation",
        response_schema=FactVerification,
        chunk_uuids=[chunk.chunk_uuid],
    )
    if isinstance(raw, FactVerification):
        return raw
    return _salvage_fact(raw)


def _salvage_fact(raw: str) -> FactVerification:
    if not isinstance(raw, str):
        return FactVerification(is_supported=False, rationale="parse failure")
    try:
        return FactVerification.model_validate_json(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return FactVerification.model_validate(json.loads(raw[start : end + 1]))
        except Exception:
            pass
    return FactVerification(is_supported=False, rationale="parse failure")


def _sample_cited_sentences(brief: BriefDraft) -> list[tuple[str, UUID]]:
    """Yield (sentence_text, cited_chunk_uuid) for every sentence with ≥1 citation."""
    out: list[tuple[str, UUID]] = []
    for section in brief.sections:
        for line in section.body_md.split("\n"):
            line = line.strip()
            if not line:
                continue
            uuids = [UUID(m.group(1)) for m in CITATION_PATTERN.finditer(line)]
            if not uuids:
                continue
            # Strip the [chunk:UUID] tokens out of the displayed sentence.
            cleaned = CITATION_PATTERN.sub("", line).strip()
            for sentence in cleaned.split(". "):
                sentence = sentence.strip().rstrip(".")
                if not sentence:
                    continue
                # Pair every sentence in this line with the line's first citation.
                # (Conservative — matches FActScore's "support whatever the line cited".)
                out.append((sentence + ".", uuids[0]))
    return out


# ---- HAMLET (recall, deterministic) ----


def _hamlet(
    doc: CanonicalDocument,
    chunks: list[ChunkRecord],
    claims: list[AtomicClaim],
    brief: BriefDraft,
) -> tuple[float, float, float]:
    cited_chunk_uuids: set[UUID] = set()
    for section in brief.sections:
        cited_chunk_uuids.update(section.cited_chunk_uuids)

    # Root recall: chapters covered by ≥1 brief section title or content.
    chapters_in_doc = {s.title for s in doc.toc}
    chapters_in_brief = {s.title for s in brief.sections}
    if not chapters_in_doc:
        root_recall = 1.0
    else:
        root_recall = len(chapters_in_brief & chapters_in_doc) / len(chapters_in_doc)

    # Branch recall: fraction of source chunks cited.
    if not chunks:
        branch_recall = 1.0
    else:
        all_chunk_uuids = {c.chunk_uuid for c in chunks}
        branch_recall = len(all_chunk_uuids & cited_chunk_uuids) / len(all_chunk_uuids)

    # Leaf recall: fraction of atomic claims whose source chunk is cited.
    if not claims:
        leaf_recall = 1.0
    else:
        covered = sum(
            1 for c in claims if any(u in cited_chunk_uuids for u in c.source_chunk_uuids)
        )
        leaf_recall = covered / len(claims)

    return root_recall, branch_recall, leaf_recall
