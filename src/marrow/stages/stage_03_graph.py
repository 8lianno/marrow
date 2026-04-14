"""Stage 03: knowledge-graph extraction (M4 real implementation).

Pipeline:
1. Read chunks.jsonl from stage 02.
2. For each chunk, call the configured LLM (default: ollama/qwen3:14b) with
   the combined `extract_graph.j2` prompt and `ExtractedGraphResponse` schema.
3. Merge entities across chunks by normalized canonical_name. Each entity
   accumulates chunk_uuids where it appears. Aliases are unioned.
4. Build a NetworkX graph: nodes=entities, edges=relationships (weighted by
   confidence). Resolve relationship endpoints by canonical_name lookup; drop
   dangling edges.
5. Community detection via Louvain (networkx built-in). One community per
   cluster at top level.
6. For each community: render community_summary prompt, call LLM, parse title
   + summary.
7. Coverage audit — any chunk with zero entities lands in a synthetic
   `_orphans` community. Audit emits warning + blocks `success` status if
   coverage < 100%.
8. Write entities/relations/communities JSONL + coverage_audit.json + graph.graphml.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

from marrow.config import MarrowConfig
from marrow.errors import LLMError
from marrow.ids import community_id as derive_community_id
from marrow.ids import entity_id as derive_entity_id
from marrow.ids import relation_id as derive_relation_id
from marrow.io import read_jsonl, write_json, write_jsonl
from marrow.llm import LLMCaller
from marrow.logging import get_logger
from marrow.prompts import render
from marrow.schemas.chunk import ChunkRecord
from marrow.schemas.graph import (
    CommunityRecord,
    CommunitySummaryResponse,
    CoverageAudit,
    EntityRecord,
    ExtractedEntity,
    ExtractedGraphResponse,
    ExtractedRelationship,
    RelationshipRecord,
)
from marrow.schemas.run import StageResult

log = get_logger(__name__)
STAGE_NAME = "03_graph"

# ROADMAP M4 budget: ≤ 15 min for 300 pages = 3s/page.
PERF_SECONDS_PER_PAGE_BUDGET = 15 * 60 / 300


def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    started = datetime.now(UTC)
    t0 = perf_counter()
    warnings: list[str] = []

    chunks = list(read_jsonl(working_dir / "02_chunk" / "chunks.jsonl", ChunkRecord))
    if not chunks:
        warnings.append("no_chunks_to_extract_from")

    caller = LLMCaller(working_dir, config)
    book_slug = chunks[0].book_slug if chunks else "unknown"

    # Per-chunk extraction.
    per_chunk_entities: dict[UUID, list[ExtractedEntity]] = {}
    per_chunk_relationships: dict[UUID, list[ExtractedRelationship]] = {}
    failed_chunks: list[UUID] = []

    for chunk in chunks:
        try:
            response = _extract_from_chunk(caller, chunk)
        except LLMError as e:
            log.warning(
                "chunk_graph_extraction_failed",
                chunk_uuid=str(chunk.chunk_uuid),
                error=str(e),
            )
            failed_chunks.append(chunk.chunk_uuid)
            continue
        except Exception as e:  # isolate per-chunk crashes
            log.warning(
                "chunk_graph_extraction_crashed",
                chunk_uuid=str(chunk.chunk_uuid),
                error_type=type(e).__name__,
                error=str(e),
            )
            failed_chunks.append(chunk.chunk_uuid)
            continue

        per_chunk_entities[chunk.chunk_uuid] = response.entities
        per_chunk_relationships[chunk.chunk_uuid] = response.relationships

    # Merge entities across chunks by normalized canonical_name.
    entities_by_key: dict[str, EntityRecord] = {}
    for chunk_uuid, extracted in per_chunk_entities.items():
        for e in extracted:
            key = _normalize(e.canonical_name)
            if not key:
                continue
            if key in entities_by_key:
                _merge_entity(entities_by_key[key], e, chunk_uuid)
            else:
                entities_by_key[key] = EntityRecord(
                    entity_id=derive_entity_id(key, book_slug),
                    canonical_name=e.canonical_name,
                    aliases=list(dict.fromkeys(e.aliases)),
                    entity_type=e.entity_type,
                    description=e.description,
                    chunk_uuids=[chunk_uuid],
                    importance=e.importance,
                )

    entity_records = list(entities_by_key.values())

    # Build relationships with entity_id lookups. Drop dangling edges.
    relationship_records: list[RelationshipRecord] = []
    chunk_relations: dict[UUID, list[RelationshipRecord]] = defaultdict(list)
    dangling = 0
    for chunk_uuid, extracted in per_chunk_relationships.items():
        for r in extracted:
            subj = entities_by_key.get(_normalize(r.subject_canonical_name))
            obj = entities_by_key.get(_normalize(r.object_canonical_name))
            if subj is None or obj is None:
                dangling += 1
                continue
            rel = RelationshipRecord(
                relation_id=derive_relation_id(
                    subj.entity_id, r.predicate, obj.entity_id, book_slug
                ),
                subject_entity_id=subj.entity_id,
                predicate=r.predicate,
                object_entity_id=obj.entity_id,
                chunk_uuids=[chunk_uuid],
                confidence=r.confidence,
            )
            # Dedup by relation_id, union chunk_uuids.
            existing = next(
                (x for x in relationship_records if x.relation_id == rel.relation_id),
                None,
            )
            if existing is not None:
                if chunk_uuid not in existing.chunk_uuids:
                    existing.chunk_uuids.append(chunk_uuid)
            else:
                relationship_records.append(rel)
            chunk_relations[chunk_uuid].append(rel)

    if dangling:
        warnings.append(f"dropped_{dangling}_dangling_relationships")

    # Community detection.
    graph_obj, community_assignments = _detect_communities(entity_records, relationship_records)

    # Generate community summaries.
    community_records: list[CommunityRecord] = []
    for community_idx, entity_ids in community_assignments.items():
        community_entity_records = [e for e in entity_records if e.entity_id in entity_ids]
        if not community_entity_records:
            continue
        chunk_uuids = _chunk_uuids_for_community(community_entity_records)
        community_rels = [
            {
                "subject": entities_by_key_id(entity_records, r.subject_entity_id).canonical_name,
                "predicate": r.predicate,
                "object": entities_by_key_id(entity_records, r.object_entity_id).canonical_name,
            }
            for r in relationship_records
            if r.subject_entity_id in entity_ids and r.object_entity_id in entity_ids
        ][:20]  # cap for prompt size

        try:
            summary = _summarize_community(caller, community_entity_records, community_rels)
            title, summary_text = summary.title, summary.summary
        except Exception as e:
            log.warning("community_summary_failed", community_idx=community_idx, error=str(e))
            title = f"Community {community_idx}"
            summary_text = "Summary generation failed. Entities: " + ", ".join(
                e.canonical_name for e in community_entity_records[:5]
            )

        community_records.append(
            CommunityRecord(
                community_id=derive_community_id(list(entity_ids), book_slug),
                level=0,
                title=title,
                summary=summary_text,
                entity_ids=list(entity_ids),
                chunk_uuids=chunk_uuids,
                is_orphan_bucket=False,
            )
        )

    # Coverage audit + orphan bucket.
    covered_chunk_uuids: set[UUID] = set()
    for cr in community_records:
        covered_chunk_uuids.update(cr.chunk_uuids)

    all_chunk_uuids = {c.chunk_uuid for c in chunks}
    orphans = sorted(all_chunk_uuids - covered_chunk_uuids, key=str)
    orphan_bucket_created = False
    if orphans:
        orphan_bucket_created = True
        community_records.append(
            CommunityRecord(
                community_id=derive_community_id([], book_slug + ":_orphans"),
                level=0,
                title="_orphans",
                summary=(
                    f"{len(orphans)} chunk(s) produced no entities or were not clustered "
                    "into any community. Routed here to preserve the 100% coverage invariant."
                ),
                entity_ids=[],
                chunk_uuids=orphans,
                is_orphan_bucket=True,
            )
        )
        warnings.append(f"{len(orphans)}_orphan_chunks_routed_to__orphans_bucket")

    coverage_pct = (
        100.0 * (len(all_chunk_uuids) - len(orphans)) / len(all_chunk_uuids)
        if all_chunk_uuids
        else 0.0
    )

    audit = CoverageAudit(
        total_chunks=len(all_chunk_uuids),
        chunks_in_communities=len(all_chunk_uuids),  # inc. _orphans bucket → always total
        orphan_chunk_uuids=orphans,
        coverage_pct=coverage_pct,
        orphan_bucket_created=orphan_bucket_created,
    )

    if failed_chunks:
        warnings.append(f"graph_extraction_failed on {len(failed_chunks)} chunk(s)")

    # Persist.
    out_dir = working_dir / STAGE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "entities.jsonl", entity_records)
    write_jsonl(out_dir / "relations.jsonl", relationship_records)
    write_jsonl(out_dir / "communities.jsonl", community_records)
    write_json(out_dir / "coverage_audit.json", audit)
    _write_graphml(out_dir / "graph.graphml", graph_obj)

    elapsed = perf_counter() - t0
    pages = max(c.page_end for c in chunks) if chunks else 1
    if elapsed / pages > PERF_SECONDS_PER_PAGE_BUDGET:
        warnings.append(
            f"performance_budget_exceeded: {elapsed / pages:.2f}s/page > "
            f"{PERF_SECONDS_PER_PAGE_BUDGET:.2f}s/page"
        )

    return StageResult(
        stage_name=STAGE_NAME,
        started_at=started,
        completed_at=datetime.now(UTC),
        duration_seconds=elapsed,
        status="warning" if warnings else "success",
        counts={
            "chunks_processed": len(chunks),
            "chunks_failed": len(failed_chunks),
            "entities": len(entity_records),
            "relationships": len(relationship_records),
            "communities": len(community_records),
            "orphan_chunks": len(orphans),
        },
        warnings=warnings,
        output_paths=[
            str(out_dir / "entities.jsonl"),
            str(out_dir / "relations.jsonl"),
            str(out_dir / "communities.jsonl"),
            str(out_dir / "coverage_audit.json"),
            str(out_dir / "graph.graphml"),
        ],
    )


# ---- LLM calls ----


def _extract_from_chunk(caller: LLMCaller, chunk: ChunkRecord) -> ExtractedGraphResponse:
    prompt = render(
        "extract_graph.j2",
        chunk_uuid=str(chunk.chunk_uuid),
        chapter_path=chunk.chapter_path,
        chunk_text=chunk.text,
    )
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="graph_extraction",
        response_schema=ExtractedGraphResponse,
        chunk_uuids=[chunk.chunk_uuid],
    )
    if isinstance(raw, ExtractedGraphResponse):
        return raw
    try:
        return ExtractedGraphResponse.model_validate_json(raw)
    except Exception:
        return _salvage_graph_json(raw)


def _salvage_graph_json(text: str) -> ExtractedGraphResponse:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return ExtractedGraphResponse()
    try:
        return ExtractedGraphResponse.model_validate(json.loads(text[start : end + 1]))
    except Exception:
        return ExtractedGraphResponse()


def _summarize_community(
    caller: LLMCaller,
    entities: list[EntityRecord],
    relationships: list[dict[str, str]],
) -> CommunitySummaryResponse:
    prompt = render(
        "summarize_community.j2",
        entities=entities,
        relationships=relationships,
    )
    raw = caller.call(
        stage=STAGE_NAME,
        prompt=prompt,
        model_role="graph_extraction",
        response_schema=CommunitySummaryResponse,
    )
    if isinstance(raw, CommunitySummaryResponse):
        return raw
    try:
        return CommunitySummaryResponse.model_validate_json(raw)
    except Exception:
        # Salvage
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return CommunitySummaryResponse.model_validate(json.loads(raw[start : end + 1]))
            except Exception:
                pass
        return CommunitySummaryResponse(title="Unnamed community", summary=raw[:1000])


# ---- Entity merging ----


def _normalize(name: str) -> str:
    return " ".join(name.lower().strip().split())


def _merge_entity(existing: EntityRecord, new: ExtractedEntity, chunk_uuid: UUID) -> None:
    # Accumulate chunk coverage.
    if chunk_uuid not in existing.chunk_uuids:
        existing.chunk_uuids.append(chunk_uuid)
    # Union aliases.
    seen = {_normalize(a) for a in existing.aliases}
    seen.add(_normalize(existing.canonical_name))
    for alias in (*new.aliases, new.canonical_name):
        if _normalize(alias) not in seen and _normalize(alias) != _normalize(
            existing.canonical_name
        ):
            existing.aliases.append(alias)
            seen.add(_normalize(alias))
    # Keep higher importance.
    existing.importance = max(existing.importance, new.importance)


def entities_by_key_id(entities: list[EntityRecord], eid: UUID) -> EntityRecord:
    """O(n) lookup — acceptable for community sizes."""
    return next(e for e in entities if e.entity_id == eid)


# ---- Graph construction + community detection ----


def _detect_communities(
    entities: list[EntityRecord],
    relationships: list[RelationshipRecord],
) -> tuple[Any, dict[int, set[UUID]]]:
    """Build NetworkX graph + return (graph, community_idx → entity_ids).

    Falls back to a single trivial community containing every entity if
    networkx or a community-detection algorithm isn't available.
    """
    try:
        import networkx as nx
    except ImportError:
        log.warning("networkx_not_available_single_trivial_community")
        return None, {0: {e.entity_id for e in entities}}

    g = nx.Graph()
    for e in entities:
        g.add_node(str(e.entity_id), canonical_name=e.canonical_name, entity_type=e.entity_type)
    for r in relationships:
        u, v = str(r.subject_entity_id), str(r.object_entity_id)
        if g.has_edge(u, v):
            g[u][v]["weight"] += r.confidence
        else:
            g.add_edge(u, v, weight=r.confidence, predicate=r.predicate)

    if g.number_of_nodes() == 0:
        return g, {}

    # NetworkX 3.x ships Louvain. Fall back to connected components if unavailable.
    try:
        from networkx.algorithms.community import louvain_communities

        communities = louvain_communities(g, seed=42, weight="weight")
    except Exception:
        communities = list(nx.connected_components(g))

    return g, {i: {UUID(nid) for nid in comm} for i, comm in enumerate(communities)}


def _chunk_uuids_for_community(entities: list[EntityRecord]) -> list[UUID]:
    seen: set[UUID] = set()
    out: list[UUID] = []
    for e in entities:
        for u in e.chunk_uuids:
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out


def _write_graphml(path: Path, graph: Any) -> None:
    if graph is None:
        path.write_text("<?xml version='1.0'?>\n<graphml/>\n")
        return
    try:
        import networkx as nx

        nx.write_graphml(graph, str(path))
    except Exception as e:
        log.warning("graphml_write_failed", error=str(e))
        path.write_text("<?xml version='1.0'?>\n<graphml/>\n")
