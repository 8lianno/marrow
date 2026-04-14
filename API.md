# Marrow — API Reference

**Version:** 1.0 | **Date:** 2026-04-14 | **Companion to:** `ARCHITECTURE.md`, `DATABASE.md`

> Marrow is a CLI tool, not a service. This document covers (1) the **CLI surface** users interact with, (2) the **internal Python module API** Claude Code / Codex will use when building stages, and (3) the **stage contract** every new stage must implement. There are no HTTP endpoints in v1.0.

---

## 1. CLI Reference

All commands are implemented via Typer. Run `marrow --help` or `marrow <command> --help` for runtime help.

### 1.1 `marrow run`

Run the full pipeline on a single book.

```bash
marrow run BOOK_PATH [OPTIONS]
```

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `BOOK_PATH` | path | yes | Path to `.pdf` or `.epub` |

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--config PATH` | path | `configs/default.yaml` | Override config file |
| `--resume` | flag | false | Resume from last completed stage |
| `--force` | flag | false | Delete existing working dir and restart |
| `--stage NAME` | str | none | Run a single stage only (`ingest`, `chunk`, `graph`, `claims`, `synthesize`, `validate`, `evaluate`, `export`) |
| `--from STAGE` | str | none | Run from this stage to the end |
| `--to STAGE` | str | none | Run from start (or `--from`) up to and including this stage |
| `--vault PATH` | path | from config | Override Obsidian vault path for export |
| `--cost-cap USD` | float | from config | Override per-book cost cap |
| `--dry-run` | flag | false | Validate config + show planned stages, do not execute |
| `-v / --verbose` | flag | false | DEBUG-level logging to stderr |
| `--no-progress` | flag | false | Disable Rich progress bars (for CI/log files) |

**Exit codes** (defined in `marrow.errors.MarrowExitCode`):
- `0` — success
- `1` — pipeline failure (one or more stages failed)
- `2` — config validation error
- `3` — budget cap exceeded and user declined to continue
- `4` — input file not found / unsupported format
- `5` — working directory locked by another process
- `130` — user interrupt (Ctrl-C)

**Examples:**

```bash
# Standard run with defaults
marrow run ./books/thinking-fast-and-slow.pdf

# Resume after a failed synthesis stage
marrow run ./books/thinking-fast-and-slow.pdf --resume

# Re-run only the export stage (useful after vault path change)
marrow run ./books/thinking-fast-and-slow.pdf --stage export --vault ~/Obsidian/Brain

# Cheap preset for less critical books
marrow run ./books/some-business-book.epub --config configs/cheap.yaml

# Dry run to see what would happen
marrow run ./books/big-academic-book.pdf --dry-run
```

### 1.2 `marrow batch`

Process every book in a directory.

```bash
marrow batch BOOKS_DIR [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--config PATH` | path | `configs/default.yaml` | |
| `--resume` | flag | false | Skip books with a successful manifest |
| `--continue-on-error` | flag | true | Continue with next book if one fails (default behavior) |
| `--max-parallel N` | int | 1 | Number of books processed in parallel (v1.0 = 1; multi-process in v1.1) |
| `--report PATH` | path | `./batch_report.md` | Output batch summary |

Behavior:
- Discovers `*.pdf` and `*.epub` recursively.
- Each book gets its own working directory.
- A failure on one book never aborts the batch.
- Final report lists each book with status, cost, runtime, and final scores.

### 1.3 `marrow status`

Report the state of a working directory.

```bash
marrow status BOOK_SLUG_OR_PATH
```

Output:
```
Book: thinking-fast-and-slow
Status: in_progress (5/8 stages complete)
Started: 2026-04-14 09:23:11
Last update: 2026-04-14 10:11:42

Stage Results:
  ✓ 01_ingest       4m 12s    $0.00
  ✓ 02_chunk        6m 03s    $0.00 (local embeddings)
  ✓ 03_graph       12m 47s    $1.31
  ✓ 04_claims      18m 22s    $0.00 (local llama)
  ✓ 05_synthesize  19m 51s    $1.84
  ⏳ 05b_validate   in progress (iter 1/3)
  ⏸ 06a_evaluate   pending
  ⏸ 06b_export     pending

Total cost so far: $3.15 / $4.00 cap
```

### 1.4 `marrow clean`

Remove a working directory.

```bash
marrow clean BOOK_SLUG_OR_PATH [--keep-export] [--yes]
```

`--keep-export` preserves the final Obsidian files but removes intermediates.

### 1.5 `marrow ask` (v1.1, US-010)

Query the corpus.

```bash
marrow ask "QUESTION" [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--book SLUG` | str | none | Restrict to a single book |
| `--max-books N` | int | unlimited | Cap retrieval breadth |
| `--format FMT` | str | `markdown` | `markdown` \| `json` |

Output: synthesized answer with inline citations of the form `[<book-slug>#^<chunk-uuid>]`, which Obsidian resolves to clickable links.

### 1.6 `marrow config`

Inspect and validate config.

```bash
marrow config show               # print resolved config
marrow config validate PATH      # validate a config file
marrow config diff A.yaml B.yaml # show effective diff
```

---

## 2. Internal Python Module API

### 2.1 Public Entry Points

```python
# Top-level package surface — what stage authors can import
from marrow.config import MarrowConfig, load_config
from marrow.llm import call as llm_call
from marrow.errors import StageError, BudgetExceeded, ChunkExtractionFailed
from marrow.schemas import (
    CanonicalDocument, ChunkRecord, AtomicClaim,
    EntityRecord, RelationshipRecord, CommunityRecord,
    BriefDraft, BriefSection, EvaluationReport, StageResult,
)
from marrow.store import VectorStore, GraphStore, KVStore
from marrow.io import read_jsonl, write_jsonl, read_json, write_json
from marrow.logging import get_logger
```

Anything not in this list is internal and may change without notice.

### 2.2 The LLM Call Wrapper

**This is the single most important API surface in the codebase.** Every model call must go through it.

```python
def call(
    *,
    stage: str,
    prompt: str,
    model_role: str,
    response_schema: type[BaseModel] | None = None,
    chunk_uuids: list[UUID] | None = None,
    system: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    extra_metadata: dict[str, Any] | None = None,
) -> LLMResponse:
    """
    The mandatory wrapper for every LLM call in Marrow.

    Args:
        stage: Name of the calling stage. Used for cost attribution and logging.
        prompt: User-role prompt content.
        model_role: Logical role resolved to a concrete model via config.models.
                    Must be one of the keys in MarrowConfig.models.
        response_schema: If provided, response is validated against this Pydantic model.
                         On validation failure, the call retries up to 3 times with a
                         stricter prompt before raising SchemaValidationFailed.
        chunk_uuids: Source chunks this call relates to. Used for cost attribution.
        system: Optional system prompt (Anthropic-style).
        max_tokens: Hard cap on output tokens.
        temperature: Defaults to 0.0 for determinism.
        extra_metadata: Additional fields written to the LLM call log.

    Returns:
        LLMResponse with .text, .parsed (if schema), .tokens_in, .tokens_out, .usd, .latency_ms

    Raises:
        BudgetExceeded: Cost cap would be exceeded by this call.
        SchemaValidationFailed: Response did not conform to schema after retries.
        ProviderError: Underlying provider failed after retries.
    """
```

**Example usage:**

```python
from marrow.llm import call
from marrow.schemas import AtomicClaimList

response = call(
    stage="04_claims",
    prompt=render_template("extract_claims.j2", chunk=chunk),
    model_role="claim_extraction",
    response_schema=AtomicClaimList,
    chunk_uuids=[chunk.chunk_uuid],
    system="You are an atomic claim extractor. Output strict JSON.",
)

claims = response.parsed.claims  # list[AtomicClaim], type-checked
```

### 2.3 Store APIs

#### VectorStore (`marrow.store.VectorStore`)

```python
class VectorStore:
    @classmethod
    def open(cls, path: Path) -> "VectorStore": ...

    def insert(self, chunks: list[ChunkRecord]) -> None: ...

    def search(
        self,
        query_vector: list[float] | None = None,
        query_text: str | None = None,
        filter_expr: str | None = None,
        limit: int = 20,
    ) -> list[ChunkRecord]: ...

    def get_by_uuid(self, chunk_uuid: UUID) -> ChunkRecord | None: ...

    def get_by_uuids(self, chunk_uuids: list[UUID]) -> list[ChunkRecord]: ...

    def count(self) -> int: ...
```

#### GraphStore (`marrow.store.GraphStore`)

```python
class GraphStore:
    @classmethod
    def open(cls, path: Path) -> "GraphStore": ...

    def add_entities(self, entities: list[EntityRecord]) -> None: ...
    def add_relations(self, relations: list[RelationshipRecord]) -> None: ...
    def add_communities(self, communities: list[CommunityRecord]) -> None: ...

    def neighbors(self, entity_id: UUID, hops: int = 1) -> list[EntityRecord]: ...
    def community_for_chunk(self, chunk_uuid: UUID) -> CommunityRecord | None: ...
    def communities_for_chapter(self, chapter_path: list[str]) -> list[CommunityRecord]: ...
    def orphan_chunks(self) -> list[UUID]: ...

    def export_graphml(self, path: Path) -> None: ...
```

#### KVStore (`marrow.store.KVStore`)

Thin SQLite wrapper used by the cost ledger and run state. Stage authors generally don't touch it directly.

```python
class KVStore:
    @classmethod
    def open(cls, path: Path) -> "KVStore": ...
    def put(self, key: str, value: dict) -> None: ...
    def get(self, key: str) -> dict | None: ...
    def list(self, prefix: str) -> list[tuple[str, dict]]: ...
```

### 2.4 Logging

```python
from marrow.logging import get_logger

log = get_logger(__name__)
log.info("chunk_processed", chunk_uuid=str(chunk.chunk_uuid), tokens=chunk.token_count)
log.warning("extraction_failed", chunk_uuid=str(uuid), error=str(e))
```

All logs are structured (JSON via structlog). Never use `print()` in stage code.

---

## 3. Stage Contract

Every stage module under `src/marrow/stages/` must conform to this contract:

### 3.1 Module Layout

```python
# src/marrow/stages/stage_04_claims.py
from pathlib import Path
from marrow.config import MarrowConfig
from marrow.schemas import StageResult

STAGE_NAME = "04_claims"
STAGE_DEPENDS_ON = ["02_chunk"]   # which prior stage outputs must exist
STAGE_PRODUCES = ["claims.jsonl", "dedup_report.json"]

def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    """
    Read inputs from working_dir / "02_chunk" / *
    Write outputs to working_dir / "04_claims" / *
    Return StageResult.
    Raise StageError on unrecoverable failure.
    Never write outside working_dir.
    """
    ...
```

### 3.2 Discovery

The orchestrator discovers stages via:

```python
import pkgutil
from importlib import import_module

def discover_stages() -> list[StageModule]:
    stages = []
    for finder, name, _ in pkgutil.iter_modules(marrow.stages.__path__):
        if name.startswith("stage_"):
            mod = import_module(f"marrow.stages.{name}")
            stages.append(StageModule(
                name=mod.STAGE_NAME,
                depends_on=mod.STAGE_DEPENDS_ON,
                produces=mod.STAGE_PRODUCES,
                run=mod.run,
            ))
    return sorted(stages, key=lambda s: s.name)  # name has numeric prefix
```

### 3.3 Stage Lifecycle

For every stage `S`, the orchestrator does:

1. **Resume check** — if `working_dir / S.dir / "_complete"` exists and `--force` is not set, skip.
2. **Dependency check** — assert every `S.depends_on` stage's `_complete` marker exists. Otherwise raise `MissingDependency`.
3. **Cleanup** — if a partial output dir exists from a previous failed run, delete it.
4. **Create dir** — `mkdir runs/<slug>/<stage_dir>`.
5. **Invoke** — call `S.run(working_dir, config)` inside a try/except that catches `StageError` and `BudgetExceeded`.
6. **Persist result** — write `result.json` from the returned `StageResult`.
7. **Mark complete** — `touch runs/<slug>/<stage_dir>/_complete` (the very last operation).
8. **Update manifest** — append the result to `manifest.json::stage_results`.

### 3.4 What Stages MUST NOT Do

- Mutate any file outside their own output directory
- Call LLM providers directly (must use `marrow.llm.call`)
- Use module-level mutable state
- Use `print()` for output
- Hard-code paths
- Catch and swallow `BudgetExceeded` (must propagate)
- Skip writing the `_complete` marker on success

### 3.5 What Stages SHOULD Do

- Process inputs in deterministic order (sorted by UUID or name)
- Use `tqdm` or Rich progress bars only when `config.cli.show_progress` is true
- Emit warnings for partial failures rather than aborting
- Write incremental progress to disk where practical (so resume on Ctrl-C is possible mid-stage for long stages)
- Use `marrow.io` helpers for JSONL read/write to ensure consistent encoding

---

## 4. Configuration API

```python
from marrow.config import MarrowConfig, load_config

# Load with full layering
config = load_config(
    config_file=Path("configs/default.yaml"),
    overrides={"cost.max_per_book": 6.0},
    use_env=True,
)

# Inspect
config.cost.max_per_book          # 6.0
config.models.synthesis           # "claude-sonnet-4-6"
config.export.vault_path          # PosixPath("~/Obsidian/Brain") or None

# All Pydantic models — full type checking
```

`MarrowConfig` is a Pydantic v2 model; see `src/marrow/config.py` for the full field list. Defaults come from `configs/default.yaml`. Environment variables prefixed with `MARROW_` override file values (e.g., `MARROW_COST_MAX_PER_BOOK=6.0`). CLI flags override env vars.

---

## 5. Error Hierarchy

```python
class MarrowError(Exception):
    """Root of all Marrow errors."""

class ConfigError(MarrowError): ...
class StageError(MarrowError):
    def __init__(self, stage: str, message: str): ...

class MissingDependency(StageError): ...
class BudgetExceeded(MarrowError): ...

class LLMError(MarrowError): ...
class ProviderError(LLMError): ...
class SchemaValidationFailed(LLMError): ...

class ChunkExtractionFailed(MarrowError):
    def __init__(self, chunk_uuid: UUID, reason: str): ...
```

Stage code should raise `ChunkExtractionFailed` for per-chunk problems (which the stage catches and logs as warnings) and `StageError` for stage-wide unrecoverable failures (which propagate to the orchestrator and abort the run).

---

## 6. Versioning & Stability

| Surface | Stability |
|---------|-----------|
| CLI commands | **Stable** — semver from v1.0 |
| `marrow.config.MarrowConfig` | **Stable** — additive changes only |
| `marrow.llm.call` signature | **Stable** — additive changes only |
| Pydantic schemas in `marrow.schemas` | **Stable** — additive; schema migrations supported |
| Stage contract | **Stable** — semver |
| Internal helpers in `marrow.io`, `marrow.logging` | **Internal** — may change |
| Stage module internals | **Internal** — each stage is free to refactor |

Any breaking change to a stable surface bumps the major version of the marrow package and is recorded in `CHANGELOG.md`.

---
**End of API.md**
