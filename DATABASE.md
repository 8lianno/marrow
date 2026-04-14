# Marrow — Database & Storage Layout

**Version:** 1.0 | **Date:** 2026-04-14 | **Companion to:** `ARCHITECTURE.md`, `API.md`

> Marrow has **no database server**. Storage is file-based (JSONL + LanceDB + SQLite) inside per-book working directories. This document specifies every file artifact, its schema, and its role in the pipeline.

---

## 1. Working Directory Layout

```
runs/
└── <book-slug>/                         # e.g., "thinking-fast-and-slow"
    ├── manifest.json                    # Run manifest (resolved config + cost summary)
    ├── cost_ledger.sqlite               # Per-call cost ledger
    ├── logs/
    │   ├── run.jsonl                    # Structured run log
    │   └── llm/
    │       ├── 04_claims_<call_id>.json # Full prompt + response per LLM call
    │       └── ...
    ├── 01_ingest/
    │   ├── document.json                # CanonicalDocument
    │   ├── source.md                    # Plain MD with page anchors
    │   ├── result.json                  # StageResult
    │   └── _complete                    # Sentinel
    ├── 02_chunk/
    │   ├── chunks.jsonl                 # ChunkRecord[]
    │   ├── vectors.lance/               # LanceDB table directory
    │   ├── result.json
    │   └── _complete
    ├── 03_graph/
    │   ├── entities.jsonl               # EntityRecord[]
    │   ├── relations.jsonl              # RelationshipRecord[]
    │   ├── communities.jsonl            # CommunityRecord[] (with summaries)
    │   ├── coverage_audit.json          # Orphan chunk report
    │   ├── graph.graphml                # NetworkX dump for inspection
    │   ├── result.json
    │   └── _complete
    ├── 04_claims/
    │   ├── claims.jsonl                 # AtomicClaim[]
    │   ├── dedup_report.json
    │   ├── result.json
    │   └── _complete
    ├── 05_synthesize/
    │   ├── draft_brief.json             # BriefDraft v0
    │   ├── merge_tree.json              # Audit trail of recursive merges
    │   ├── result.json
    │   └── _complete
    ├── 05b_validate/
    │   ├── iter_01/
    │   │   ├── quiz.jsonl
    │   │   ├── results.json
    │   │   └── _complete
    │   ├── iter_02/...
    │   ├── final_brief.json             # BriefDraft vN
    │   ├── result.json
    │   └── _complete
    ├── 06a_evaluate/
    │   ├── booookscore.json
    │   ├── factscore.json
    │   ├── hamlet.json
    │   ├── composite.json               # Final EvaluationReport
    │   ├── result.json
    │   └── _complete
    └── 06b_export/
        ├── <slug>_Source.md             # Final Obsidian source file
        ├── <slug>_Brief.md              # Final Obsidian brief file
        ├── <slug>_Evaluation.md         # Human-readable score report
        ├── result.json
        └── _complete
```

The directory is **the database**. Inspectable, debuggable, resumable, version-controllable (if the user chooses).

---

## 2. Pydantic Schemas

All schemas live in `src/marrow/schemas/`. Each is a Pydantic v2 `BaseModel`. JSON Schemas are auto-emitted to `docs/schemas/` via `make schemas`.

### 2.1 CanonicalDocument (`schemas/document.py`)

Output of stage 01. The structured representation of the entire book.

```python
class ParagraphNode(BaseModel):
    paragraph_id: UUID                    # deterministic: MD5(text + chapter_path + page)
    text: str
    page_start: int
    page_end: int
    is_footnote: bool = False
    is_table: bool = False
    table_grid: list[list[str]] | None = None  # populated when is_table

class SectionNode(BaseModel):
    section_id: UUID
    title: str
    level: int                            # 1=chapter, 2=section, 3=subsection, etc.
    paragraphs: list[ParagraphNode]
    subsections: list["SectionNode"] = []

class CanonicalDocument(BaseModel):
    book_slug: str                        # "thinking-fast-and-slow"
    book_title: str
    book_author: str | None
    source_format: Literal["pdf", "epub"]
    source_path: str
    page_count: int
    word_count: int
    parser: str                           # "docling@2.x.y"
    parser_mode: Literal["auto", "force_ocr", "text_only"]
    toc: list[SectionNode]                # Top-level chapters with nested sections
    skipped_pages: list[int] = []         # Per-page failures
    extracted_at: datetime
```

### 2.2 ChunkRecord (`schemas/chunk.py`)

Output of stage 02. Each chunk is a sentence-aligned span with a context-aware late-chunked embedding.

```python
class ChunkRecord(BaseModel):
    chunk_uuid: UUID                      # deterministic: MD5(text + book_slug + chapter_path)
    book_slug: str
    text: str
    chapter_path: list[str]               # ["Chapter 3", "Section 2.1"]
    paragraph_ids: list[UUID]             # ParagraphNodes this chunk spans
    page_start: int
    page_end: int
    token_count: int
    sentence_count: int
    embedding_model: str                  # "jinaai/jina-embeddings-v2-base-en"
    embedding: list[float]                # 768-dim
    window_index: int                     # which sliding window this chunk came from
```

Stored as both:
1. **`chunks.jsonl`** — Pydantic JSONL for inspection and replay
2. **`vectors.lance/`** — LanceDB table for fast similarity search

### 2.3 Graph Records (`schemas/graph.py`)

Output of stage 03.

```python
class EntityRecord(BaseModel):
    entity_id: UUID                       # MD5(canonical_name + book_slug)
    canonical_name: str
    aliases: list[str]
    entity_type: Literal["person", "concept", "place", "org", "framework", "event", "other"]
    description: str                      # LLM-generated 1-line description
    chunk_uuids: list[UUID]               # chunks this entity appears in
    importance: float                     # 0.0–1.0, derived from frequency + centrality

class RelationshipRecord(BaseModel):
    relation_id: UUID
    subject_entity_id: UUID
    predicate: str                        # natural language: "argued against", "extended"
    object_entity_id: UUID
    chunk_uuids: list[UUID]               # source chunks supporting this relation
    confidence: float

class CommunityRecord(BaseModel):
    community_id: UUID
    level: int                            # Leiden hierarchy level
    title: str                            # LLM-generated short title
    summary: str                          # LLM-generated narrative summary
    entity_ids: list[UUID]
    chunk_uuids: list[UUID]               # all chunks contributing to this community
    is_orphan_bucket: bool = False        # synthetic _orphans community

class CoverageAudit(BaseModel):
    total_chunks: int
    chunks_in_communities: int
    orphan_chunk_uuids: list[UUID]
    coverage_pct: float
    orphan_bucket_created: bool
```

### 2.4 AtomicClaim (`schemas/claim.py`)

Output of stage 04. Every load-bearing fact extracted as a discrete object.

```python
class AtomicClaim(BaseModel):
    claim_id: UUID
    claim_text: str                       # rewritten as standalone sentence
    claim_type: Literal["factual", "definitional", "argumentative", "causal", "statistical"]
    source_chunk_uuids: list[UUID]        # ≥1 after dedup
    source_span: str                      # the exact substring of the source chunk
    confidence: float                     # 0.0–1.0
    entities_referenced: list[UUID] = []  # links to EntityRecord ids
    is_duplicate_of: UUID | None = None   # set on merged duplicates

class ClaimsManifest(BaseModel):
    total_extracted: int
    total_after_dedup: int
    failed_chunks: list[UUID]
    avg_claims_per_1k_tokens: float
```

### 2.5 BriefDraft & EvaluationReport (`schemas/brief.py`)

Outputs of stages 05, 05b, 06a.

```python
class BriefSection(BaseModel):
    section_id: UUID
    title: str                            # Mirrors source chapter/section title
    level: int                            # 1=chapter, 2=section
    body_md: str                          # Markdown prose with inline [chunk:UUID] citations
    cited_chunk_uuids: list[UUID]         # parsed from body_md for fast lookup
    subsections: list["BriefSection"] = []

class BriefDraft(BaseModel):
    draft_version: int                    # 0 = initial, 1+ = post-validation iterations
    book_slug: str
    book_title: str
    sections: list[BriefSection]
    word_count: int
    estimated_page_count: int
    citation_density: float               # citations per paragraph
    generated_at: datetime
    iteration_history: list[str] = []     # log of merge/regenerate events

class QuizQuestion(BaseModel):
    question_id: UUID
    chapter_path: list[str]
    question_text: str
    expected_answer: str                  # known to quiz generator, hidden from examinee
    source_chunk_uuids: list[UUID]
    leaf_level: Literal["date", "name", "number", "definition", "causal", "example"]
    is_grounded: bool                     # passed secondary check; ungrounded excluded from pass rate

class QuizResult(BaseModel):
    iteration: int
    total_questions: int
    grounded_questions: int
    answered_correctly: int
    pass_rate: float                      # answered_correctly / grounded_questions
    failed_question_ids: list[UUID]
    regenerated_section_ids: list[UUID]

class EvaluationReport(BaseModel):
    book_slug: str
    brief_version: int
    booookscore: float                    # coherence
    factscore: float                      # atomic precision
    factscore_length_penalty_applied: bool
    hamlet_root_recall: float
    hamlet_branch_recall: float
    hamlet_leaf_recall: float
    composite_score: float
    verdict: Literal["PASS", "FAIL"]
    failure_reasons: list[str] = []
    evaluated_at: datetime
```

### 2.6 StageResult (`schemas/run.py`)

Every stage returns one of these. Persisted as `<stage_dir>/result.json`.

```python
class StageResult(BaseModel):
    stage_name: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    status: Literal["success", "warning", "failed"]
    counts: dict[str, int]                # stage-specific counts
    cost_usd: float
    tokens_in: int
    tokens_out: int
    warnings: list[str] = []
    errors: list[str] = []
    output_paths: list[str]
```

### 2.7 RunManifest (`schemas/run.py`)

Top-level summary at `runs/<slug>/manifest.json`. The single source of truth for what happened.

```python
class CostBreakdown(BaseModel):
    by_stage: dict[str, float]
    by_model_role: dict[str, float]
    total_usd: float
    total_tokens_in: int
    total_tokens_out: int

class RunManifest(BaseModel):
    book_slug: str
    book_path: str
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: float | None
    status: Literal["in_progress", "success", "failed", "partial"]
    config: MarrowConfig                     # fully resolved config
    stage_results: list[StageResult]
    cost_breakdown: CostBreakdown
    final_brief_path: str | None
    final_evaluation_path: str | None
    marrow_version: str
```

---

## 3. LanceDB Schema (Vector Store)

Stage 02 writes to `runs/<slug>/02_chunk/vectors.lance/`.

```python
import lancedb

schema = pa.schema([
    pa.field("chunk_uuid", pa.string()),       # UUID as string
    pa.field("text", pa.string()),
    pa.field("chapter_path", pa.list_(pa.string())),
    pa.field("page_start", pa.int32()),
    pa.field("page_end", pa.int32()),
    pa.field("token_count", pa.int32()),
    pa.field("embedding", pa.list_(pa.float32(), 768)),
])
```

Indexes:
- IVF-PQ index on `embedding` for ANN search (`num_partitions=256`, `num_sub_vectors=96`)
- Scalar index on `chunk_uuid` for O(1) lookups by UUID

Common queries:
```python
# Similarity search for hierarchical merge
table.search(query_embedding).limit(20).to_pydantic(ChunkRecord)

# UUID lookup during citation rewriting
table.search().where(f"chunk_uuid = '{uuid}'").to_pydantic(ChunkRecord)

# Filter by chapter for per-chapter synthesis
table.search().where("page_start >= 45 AND page_end <= 80").to_pydantic(ChunkRecord)
```

---

## 4. SQLite Schema (Cost Ledger)

Stage-agnostic, written by `marrow.llm.call()`. Located at `runs/<slug>/cost_ledger.sqlite`.

```sql
CREATE TABLE llm_calls (
    call_id        TEXT PRIMARY KEY,           -- UUID4
    stage          TEXT NOT NULL,
    model_role     TEXT NOT NULL,              -- claim_extraction, synthesis, ...
    model_id       TEXT NOT NULL,              -- claude-sonnet-4-6, llama-3.1-8b
    provider       TEXT NOT NULL,              -- anthropic, vllm, jina
    tokens_in      INTEGER NOT NULL,
    tokens_out     INTEGER NOT NULL,
    usd            REAL NOT NULL,
    latency_ms     INTEGER NOT NULL,
    chunk_uuids    TEXT,                       -- JSON list
    success        INTEGER NOT NULL,           -- 1 = success, 0 = failure
    retry_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL               -- ISO8601
);

CREATE INDEX idx_llm_calls_stage ON llm_calls(stage);
CREATE INDEX idx_llm_calls_created_at ON llm_calls(created_at);

CREATE TABLE budget_events (
    event_id       TEXT PRIMARY KEY,
    event_type     TEXT NOT NULL,              -- 'check', 'exceeded', 'resumed'
    cost_so_far    REAL NOT NULL,
    cost_cap       REAL NOT NULL,
    created_at     TEXT NOT NULL
);
```

Common queries:
```sql
-- Total cost for the run
SELECT SUM(usd) FROM llm_calls;

-- Cost breakdown by stage
SELECT stage, SUM(usd) AS cost, COUNT(*) AS calls
FROM llm_calls GROUP BY stage ORDER BY cost DESC;

-- Most expensive single calls
SELECT call_id, stage, model_id, usd, tokens_in + tokens_out AS total_tokens
FROM llm_calls ORDER BY usd DESC LIMIT 10;

-- Failure rate by model
SELECT model_id, AVG(1 - success) AS failure_rate
FROM llm_calls GROUP BY model_id;
```

---

## 5. Graph Storage (NanoGraphRAG + JSON)

NanoGraphRAG's native persistence is reused for the in-memory NetworkX graph plus its own JSON KV store. We add a thin adapter that:

1. Mirrors the entities, relations, and communities into our Pydantic-typed JSONL files (for cross-stage portability).
2. Dumps the full NetworkX graph to GraphML at `03_graph/graph.graphml` for inspection in Gephi or Cytoscape.

The Pydantic JSONL is the source of truth for downstream stages. NanoGraphRAG's internal store is treated as cache — deletable without losing data.

---

## 6. Determinism Rules

All UUIDs are content-addressed, not random:

| ID | Formula |
|----|---------|
| `paragraph_id` | `UUID5(MD5(text + chapter_path + page_start))` |
| `chunk_uuid` | `UUID5(MD5(text + book_slug + chapter_path))` |
| `entity_id` | `UUID5(MD5(canonical_name + book_slug))` |
| `relation_id` | `UUID5(MD5(subject_id + predicate + object_id + book_slug))` |
| `community_id` | `UUID5(MD5(sorted_entity_ids + book_slug))` |
| `claim_id` | `UUID5(MD5(claim_text + book_slug))` |
| `section_id` (brief) | `UUID5(MD5(title + level + parent_path))` |
| `question_id` | `UUID5(MD5(question_text + chapter_path))` |

Re-running any stage on identical inputs produces byte-identical outputs. This is enforced by `tests/test_determinism.py`.

---

## 7. Cross-Book Storage (v1.1 — US-010)

For multi-book corpus queries, no new database is introduced. The `marrow ask` command discovers all `runs/*/03_graph/` directories and fans out queries across them in parallel. Each book's graph remains isolated; only the synthesized answer crosses book boundaries.

Optionally (config: `corpus.mirror_to_central: true`), entity records can be mirrored to a central LanceDB table at `runs/_corpus/entities.lance/` for cross-book entity resolution. This is opt-in because it complicates determinism.

---

## 8. Backup & Portability

- **Working directories are self-contained.** A `runs/<slug>/` tarball is sufficient to restore the run on any machine running the same `marrow` version.
- **No absolute paths persisted.** All paths in manifests are relative to `runs/<slug>/`.
- **Schema versioning.** `RunManifest.marrow_version` records the version that produced the run. Future versions read older manifests via `marrow.schemas.compat`.

---
**End of DATABASE.md**
