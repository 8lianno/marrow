# Marrow — Architecture

**Version:** 1.0 | **Date:** 2026-04-14 | **Companion to:** `PRD.md`, `DATABASE.md`, `API.md`, `REPOS.md`

---

## 1. System Overview

Marrow is a **single-machine, file-based, stage-pipeline** Python application. It is not a service. It has no database server. Every stage reads its inputs from disk, writes its outputs to disk, and is fully resumable. The only persistent state is the per-book working directory under `runs/<book-slug>/`.

This is deliberate: the user is a power user running the system locally on their own books, the pipeline runs unattended for ~90 minutes per book, and resumability is more valuable than throughput. A web service architecture would add operational complexity for zero user benefit at this stage.

## 2. Architectural Principles

| # | Principle | Why |
|---|-----------|-----|
| P1 | **File-based stage boundaries** | Every stage's inputs and outputs are inspectable JSONL/JSON on disk. Resumability and debuggability come for free. |
| P2 | **Pydantic everywhere** | No raw dicts cross stage boundaries. Schema drift is impossible without a type error. |
| P3 | **Per-stage idempotency** | Re-running a completed stage produces identical bytes. Tested via determinism tests. |
| P4 | **Single LLM call wrapper** | Every model call goes through `marrow.llm.call()`. Cost telemetry, retry, structured output validation, and prompt logging are guaranteed by construction. |
| P5 | **Coverage guards over trust** | The system never assumes a stage was lossless. Every stage that could drop content has an explicit audit. Audits emit warnings, not exceptions, but they block the run from being marked successful. |
| P6 | **Per-stage model routing** | Cheap local models for high-volume passes (claim extraction, dialogue normalization), expensive frontier models for synthesis and validation. Routed by `model_role` in config. |
| P7 | **No hidden global state** | Stages take `(working_dir, config)` and return `StageResult`. No singletons, no module-level mutable state, no env-driven side effects beyond the LLM wrapper. |

## 3. High-Level Component Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              CLI Layer (Typer)                            │
│  marrow run | marrow batch | marrow status | marrow clean | marrow ask | marrow resume     │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         Orchestrator (marrow.orchestrator)                   │
│  - Discovers stages by numeric prefix                                     │
│  - Loads/validates MarrowConfig                                              │
│  - Manages working directory + checkpoints                                │
│  - Enforces stage ordering + dependency resolution                        │
│  - Emits run-level cost + telemetry summary                               │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│  Stage Layer  │◀───────▶│  Schema Layer │◀───────▶│  Store Layer  │
│  stages/*.py  │         │  schemas/*.py │         │  store/*.py   │
└───────┬───────┘         └───────────────┘         └───────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       LLM Call Wrapper (marrow.llm)                          │
│  - call(stage, prompt, model_role, response_schema, chunk_uuids)          │
│  - Routes to: Anthropic SDK | local vLLM | Jina API                       │
│  - Retries with backoff; validates JSON against Pydantic schema           │
│  - Logs cost, tokens, latency, prompt, response per call                  │
│  - Enforces MarrowConfig.cost.max_per_book budget cap                        │
└──────────────────────────────────────────────────────────────────────────┘
```

## 4. Data Flow (Single Book, Happy Path)

```
book.pdf (300 pages)
    │
    ▼  [Stage 01_ingest]  ── Docling DocLayNet + TableFormer
    │
    └──▶ runs/<slug>/01_ingest/document.json     # CanonicalDocument
         runs/<slug>/01_ingest/source.md         # Plain MD with page anchors
    │
    ▼  [Stage 02_chunk]   ── Jina v2 + sliding window late chunking
    │
    └──▶ runs/<slug>/02_chunk/chunks.jsonl       # ChunkRecord[]
         runs/<slug>/02_chunk/vectors.lance/     # LanceDB table
    │
    ▼  [Stage 03_graph]   ── NanoGraphRAG entity/relation extraction
    │
    └──▶ runs/<slug>/03_graph/entities.jsonl     # EntityRecord[]
         runs/<slug>/03_graph/relations.jsonl    # RelationshipRecord[]
         runs/<slug>/03_graph/communities.jsonl  # CommunityRecord[] (with summaries)
         runs/<slug>/03_graph/coverage_audit.json
    │
    ▼  [Stage 04_claims]  ── Llama-3.1 8B local OR Claude Haiku
    │
    └──▶ runs/<slug>/04_claims/claims.jsonl      # AtomicClaim[]
         runs/<slug>/04_claims/dedup_report.json
    │
    ▼  [Stage 05_synthesize]  ── Claude Sonnet 4.6 (hierarchical merge)
    │
    └──▶ runs/<slug>/05_synthesize/draft_brief.json   # BriefDraft v0
         runs/<slug>/05_synthesize/merge_tree.json    # Audit trail
    │
    ▼  [Stage 05b_validate]   ── SummQ adversarial loop (≤3 iterations)
    │
    └──▶ runs/<slug>/05b_validate/iter_01/quiz.jsonl
         runs/<slug>/05b_validate/iter_01/results.json
         runs/<slug>/05b_validate/iter_02/...   # if needed
         runs/<slug>/05b_validate/final_brief.json
    │
    ▼  [Stage 06a_evaluate]   ── BooookScore + FActScore + HAMLET
    │
    └──▶ runs/<slug>/06a_evaluate/booookscore.json
         runs/<slug>/06a_evaluate/factscore.json
         runs/<slug>/06a_evaluate/hamlet.json
         runs/<slug>/06a_evaluate/composite.json
    │
    ▼  [Stage 06b_export]  ── Obsidian / Logseq writer
    │
    └──▶ <vault>/<slug>_Source.md         # Every paragraph has ^uuid
         <vault>/<slug>_Brief.md          # Every sentence cites [[..#^uuid]]
         <vault>/<slug>_Evaluation.md     # Human-readable scores
```

## 5. Component Responsibilities

### 5.1 CLI Layer (`src/marrow/cli.py`)
- Typer-based command dispatch
- Loads config (CLI flags > env vars > config file > defaults)
- Resolves working directory from book path / slug
- Hands control to the orchestrator
- Renders Rich-formatted progress and final summary
- Sets exit code per `MarrowExitCode` enum

### 5.2 Orchestrator (`src/marrow/orchestrator.py`)
- Discovers stage modules by filename prefix (`stage_NN_*.py`)
- Validates that each stage's input artifacts exist before invoking it
- Skips completed stages on `--resume` (presence of stage's output dir + `_complete` marker)
- Rolls up per-stage `StageResult` objects into a run-level summary
- Persists the run manifest at `runs/<slug>/manifest.json`

### 5.3 Stage Layer (`src/marrow/stages/`)
Each stage is a standalone module exporting a single `run(working_dir, config) -> StageResult`. Stages **do not import each other**. They communicate exclusively through the file artifacts described in `DATABASE.md`. This rule makes the pipeline trivially parallelizable across books and trivially debuggable per stage.

### 5.4 Schema Layer (`src/marrow/schemas/`)
Pydantic v2 models for every artifact crossing a stage boundary. JSON schemas auto-emitted to `docs/schemas/` for external tooling. No schema is ever hand-written in JSON.

### 5.5 Store Layer (`src/marrow/store/`)
- **`vector.py`** — LanceDB wrapper. Embedded, single-file, no server. Schema enforced via Pydantic.
- **`graph.py`** — NetworkX in-memory graph + JSON serialization. Wraps NanoGraphRAG's persistence layer.
- **`kv.py`** — SQLite for run state, telemetry events, cost ledger.

### 5.6 LLM Call Wrapper (`src/marrow/llm.py`)
The single most important file in the codebase. Every model call routes through it. Responsibilities:

1. **Routing:** `model_role` → concrete model + provider, resolved from config.
2. **Cost tracking:** every call logs `{tokens_in, tokens_out, usd, stage, chunk_uuids}` to the SQLite cost ledger.
3. **Budget enforcement:** before each call, checks `cost.max_per_book`. If exceeded, raises `BudgetExceeded` which the orchestrator catches and prompts the user.
4. **Retry:** 3 attempts with exponential backoff for transient errors (rate limits, 5xx, timeouts).
5. **Structured output:** if `response_schema` is provided, validates response against it. On failure, retries with a stricter prompt up to 3 times, then raises `SchemaValidationFailed`.
6. **Determinism:** temperature=0.0 by default; seed propagated where supported.
7. **Logging:** full prompt + response written to `runs/<slug>/logs/llm/<stage>_<call_id>.json` (gitignored).

### 5.7 Evaluation Layer (`src/marrow/eval/`)
Thin wrappers around three external evaluators (BooookScore, FActScore, HAMLET) that conform their outputs to a unified `EvaluationReport` schema. Each evaluator runs in its own subprocess to isolate dependency conflicts.

## 6. Stage Specifications

| # | Stage | Inputs | Outputs | Primary Tools | LLM Role | User Story |
|---|-------|--------|---------|---------------|----------|-----------|
| 01 | ingest | `book.pdf\|epub` | `CanonicalDocument` | Docling | none | US-001 |
| 02 | chunk | `CanonicalDocument` | `ChunkRecord[]` + LanceDB vectors | Jina v2, NLTK | embedding | US-002 |
| 03 | graph | `ChunkRecord[]` | `EntityRecord[]`, `RelationshipRecord[]`, `CommunityRecord[]` | NanoGraphRAG, Leiden | `graph_extraction` (local 8B) | US-003 |
| 04 | claims | `ChunkRecord[]` | `AtomicClaim[]` | SciClaims pattern | `claim_extraction` (local 8B) | US-004 |
| 05 | synthesize | `AtomicClaim[]`, `CommunityRecord[]` | `BriefDraft v0` | hierarchical merge | `synthesis` (Sonnet) | US-005 |
| 05b | validate | `BriefDraft`, `ChunkRecord[]` | `BriefDraft vN` | SummQ loop | `quiz_generation`, `examinee` | US-006 |
| 06a | evaluate | `BriefDraft vN`, `ChunkRecord[]` | `EvaluationReport` | BooookScore, FActScore, HAMLET | `evaluation` (Sonnet) | US-007 |
| 06b | export | `BriefDraft vN`, `CanonicalDocument` | Obsidian/Logseq files | custom writer | none | US-008 |

## 7. Configuration Strategy

Config is layered: **defaults → config file → environment variables → CLI flags**. Each layer overrides the prior. The fully resolved `MarrowConfig` is serialized into the run manifest, so any run can be exactly reproduced from `runs/<slug>/manifest.json`.

```yaml
# configs/default.yaml
ingest:
  parser: docling          # docling | marker | mineru (extra)
  mode: auto               # auto | force_ocr | text_only
  max_size_mb: 500

chunk:
  embedding_model: jinaai/jina-embeddings-v2-base-en
  embedding_provider: local  # local | jina_api
  window_tokens: 8192
  overlap_pct: 0.25

graph:
  community_top_k: 512
  resolution: 1.0
  coverage_audit: true

claims:
  dedup_threshold: 0.92
  min_claims_per_1k_tokens: 8

synthesize:
  target_pages: 50
  page_tolerance: 5
  citation_required: true

validate:
  max_iterations: 3
  questions_per_chapter: 5
  pass_threshold: 0.90

evaluate:
  hamlet_leaf_threshold: 0.92
  factscore_gamma: 10
  composite_weights:
    booookscore: 0.2
    factscore: 0.3
    hamlet_leaf: 0.5

export:
  format: obsidian          # obsidian | logseq
  vault_path: null          # null = use ./runs/<slug>/06b_export/

cost:
  max_per_book: 4.00
  prompt_on_overrun: true

models:
  graph_extraction: local-llama-3.1-8b
  claim_extraction: local-llama-3.1-8b
  synthesis: claude-sonnet-4-6
  quiz_generation: claude-sonnet-4-6
  examinee: claude-sonnet-4-6
  evaluation: claude-sonnet-4-6
```

## 8. Resumability Model

Every stage writes a `_complete` sentinel file at the end of its run as the **last** operation. The orchestrator's resume logic is:

```python
for stage in discovered_stages:
    stage_dir = working_dir / stage.dir_name
    if (stage_dir / "_complete").exists():
        log.info(f"skipping {stage.name} — already complete")
        continue
    if stage_dir.exists():
        log.info(f"removing partial output for {stage.name}")
        shutil.rmtree(stage_dir)
    stage_dir.mkdir()
    result = stage.run(working_dir, config)
    write_result(stage_dir, result)
    (stage_dir / "_complete").touch()  # last write
```

Stage 05b is special: it iterates internally, and each iteration is its own resumable sub-stage under `05b_validate/iter_NN/`.

## 9. Cost & Token Telemetry

Every LLM call writes a row to `runs/<slug>/cost_ledger.sqlite`:

| column | type | description |
|--------|------|-------------|
| `call_id` | TEXT | UUID4 |
| `stage` | TEXT | stage name |
| `model_role` | TEXT | resolved from config |
| `model_id` | TEXT | concrete model identifier |
| `tokens_in` | INTEGER | |
| `tokens_out` | INTEGER | |
| `usd` | REAL | computed via per-model price table |
| `latency_ms` | INTEGER | |
| `chunk_uuids` | TEXT | JSON list |
| `created_at` | TEXT | ISO8601 |

The run manifest aggregates this by stage and writes a summary table to `runs/<slug>/manifest.json::cost_breakdown`.

## 10. Observability

- **Structured logs:** `structlog` with JSON renderer to `runs/<slug>/logs/run.jsonl`. Every entry has `stage`, `event`, `chunk_uuid` where applicable.
- **Stage results:** `runs/<slug>/<NN>_*/result.json` — `StageResult` Pydantic dump including duration, cost, warnings, and counts.
- **Cost ledger:** `runs/<slug>/cost_ledger.sqlite` (queryable with any SQLite client).
- **LLM call archive:** `runs/<slug>/logs/llm/<stage>_<call_id>.json` — full prompt + response for forensic debugging.
- **Coverage warnings:** any chunk-level failure emits a structured warning visible in the final summary table.

## 11. Failure Modes & Recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Single-page parse error in Docling | per-page try/except in 01_ingest | log + continue; user can retry that page manually |
| Single-chunk LLM extraction failure | retry 3× then mark `extraction_failed` | continue stage; flagged in StageResult |
| LLM provider outage | retry with exponential backoff up to 60s | if still failing, abort stage with clear error; user runs `marrow run --resume` later |
| Budget exceeded mid-run | budget check before each call | pause + interactive prompt (or hard-fail in batch mode) |
| Disk full | OS error on write | abort cleanly; user frees space and resumes |
| Coverage audit fails (orphan chunks) | post-stage audit in 03_graph | bundle into `_orphans` synthetic community + warn |
| Validation iteration cap reached | counter in 05b | write best-scoring brief + structured warning listing failed quiz items |

## 12. Out-of-Scope Architecture Decisions (v1.0)

- No web UI, no REST API, no daemon mode
- No multi-machine orchestration
- No queue manager beyond the in-process batch loop
- No real-time progress streaming
- No GPU scheduling — assumed user manages their own GPU resources for vLLM
- No multi-tenant access control

## 13. Decision Log

| ID | Decision | Rationale | Alternatives Rejected |
|----|----------|-----------|----------------------|
| D1 | LanceDB over Qdrant | Embedded, no server, single-file, native Apache Arrow | Qdrant (server overhead), Chroma (less mature schema), pgvector (Postgres dep) |
| D2 | NanoGraphRAG over MS GraphRAG | 1100 LOC, hackable, async, ~10% the token cost | MS GraphRAG (token bloat, reasoning bottleneck), LightRAG (dilutes thematic arcs per literature) |
| D3 | Docling default, MinerU opt-in | Docling is MIT-licensed; MinerU is AGPL | Marker (GPL/research, slowing pace), Unstructured (over-extraction issue) |
| D4 | Per-stage model routing | $4/book budget impossible if Sonnet runs everything | Sonnet-only ($8–12/book), local-only (synthesis quality drops below threshold) |
| D5 | Pydantic v2 over dataclasses | Free JSON schema + validation + serialization | dataclasses (no validation), attrs (less ecosystem) |
| D6 | Typer over Click | Type hints become CLI args automatically | Click (more boilerplate), argparse (no type integration) |
| D7 | uv over pip/poetry | 10× faster, pip-compatible, lockfile included | poetry (slow, lockfile drama), pip (no lock) |
| D8 | structlog over stdlib logging | JSON-first, contextvars-aware, structured by default | stdlib logging (string-only, ergonomic pain) |
| D9 | Subprocess isolation for evaluators | BooookScore, FActScore, HAMLET have conflicting deps | Single env (dep hell), Docker (heavy for local CLI) |
| D10 | UUID4 block IDs over 6-char hashes | Zero collision risk across briefs in same vault | 6-char hash (1 in 16M collision per pair, eventually bites) |

## 14. Future Architecture (v1.1+)

- **Cross-book corpus query (US-010):** A second-tier orchestrator over multiple `runs/<slug>/03_graph/` indices. Adds `marrow ask` command. Same file-based principles.
- **Multimodal layer:** Page-image embeddings via CLIP for figure-aware retrieval. Optional, opt-in via `multimodal: true` in config.
- **Web UI:** Read-only viewer for briefs and audit reports. Static site generated from `runs/`. Still no server.
- **Plugin system:** Stage modules become discoverable via entry points so third parties can drop in alternative implementations (e.g., a Cohere reranker stage between 03 and 04).

---
**End of ARCHITECTURE.md**
