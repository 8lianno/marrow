# Marrow — Claude Development Guide

> **Auto-loaded every Claude Code / Codex session.** Read this first before writing any code.

## Project One-Liner
Self-hosted Python CLI that compresses a 300-page non-fiction book into a ~50-page conceptual brief with **zero silent omissions** and **100% citation traceability** to source paragraphs in Obsidian.

## Quick Commands

```bash
# Environment (uv preferred, pip fallback)
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Development
make test                     # pytest -q
make lint                     # ruff check . && ruff format --check .
make typecheck                # mypy src/
make all                      # lint + typecheck + test

# Pipeline (single book)
marrow run path/to/book.pdf                    # full pipeline, default config
marrow run book.pdf --resume                   # resume from last completed stage
marrow run book.pdf --force                    # delete existing run, restart
marrow run book.pdf --stage ingest             # run one stage only
marrow run book.pdf --config configs/cheap.yaml

# Pipeline (batch)
marrow batch ./books/                          # process every book in dir
marrow status <book-slug>                      # show stage completion
marrow clean <book-slug>                       # delete working directory

# Querying (US-010, v1.1)
marrow ask "What do these books say about compound interest?"
marrow ask "..." --book "thinking-fast-and-slow"

# Local model serving (synthesis fallback)
make serve-local              # vLLM serving Llama-3.1-8B on :8000
```

## Code Style & Conventions

### Naming
- **Files:** `snake_case.py`
- **Classes:** `PascalCase`
- **Functions / variables:** `snake_case`
- **Constants:** `UPPER_SNAKE_CASE`
- **Pydantic models:** `PascalCase`, suffixed with what they represent (`ChunkRecord`, `AtomicClaim`, `BriefDraft`)
- **Stage modules:** `src/marrow/stages/01_ingest.py`, `02_chunk.py`, … `06_export.py` — numeric prefix matters; the orchestrator discovers stages by it.

### Import Order
```python
# 1. Standard library
# 2. Third-party
# 3. marrow internals (absolute: from marrow.core import ...)
# 4. Relative (rare; only inside a single stage package)
```

### Pydantic-First Data Model
**Every artifact crossing a stage boundary is a Pydantic v2 model serialized to JSONL.** No raw dicts, no positional tuples. See `src/marrow/schemas/`.

```python
from pydantic import BaseModel, Field
from uuid import UUID

class ChunkRecord(BaseModel):
    chunk_uuid: UUID
    text: str
    chapter_path: list[str] = Field(description="['Chapter 3', 'Section 2.1']")
    page_start: int
    page_end: int
    token_count: int
    embedding: list[float] | None = None  # populated by stage 02
```

### Stage Contract
Every stage module exports exactly one function:

```python
def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    """
    Idempotent. Reads previous stage's outputs from working_dir,
    writes its own outputs to working_dir / f"{NN}_{name}/", returns
    a StageResult with metrics and cost. Never mutates inputs.
    """
```

### LLM Calls — Mandatory Wrapper
**Never call `anthropic.Client` or `openai.Client` directly.** Use `marrow.llm.call()`:

```python
from marrow.llm import call

response = call(
    stage="04_claims",
    prompt=prompt,
    model_role="claim_extraction",  # routes to local Llama or Sonnet per config
    response_schema=AtomicClaimList, # Pydantic; validates + retries on failure
    chunk_uuids=[chunk.chunk_uuid],  # for cost attribution
)
```

The wrapper provides: cost logging, token counting, automatic retry with backoff, structured-output validation, deterministic seeding (temperature=0.0), and full prompt/response logging to `runs/<book-slug>/logs/llm/`.

### Error Handling Pattern
```python
from marrow.errors import StageError, ChunkExtractionFailed

try:
    claims = extract_claims(chunk)
except ChunkExtractionFailed as e:
    log.warning("chunk_extraction_failed", chunk_uuid=chunk.chunk_uuid, error=str(e))
    failed_chunks.append(chunk.chunk_uuid)
    continue  # NEVER abort the run on a single chunk failure
```

**Single-chunk failures are isolated, not propagated.** The only conditions that abort a stage are: missing inputs from the prior stage, malformed config, or write-permission errors on the working directory.

## Architecture Overview

```
                    ┌──────────────┐
   PDF / EPUB ─────▶│ 01_ingest    │ Docling → DoclingDocument JSON
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ 02_chunk     │ Late chunking via Jina v2 → ChunkRecord[]
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ 03_graph     │ NanoGraphRAG → entities, communities, summaries
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ 04_claims    │ SciClaims-style → AtomicClaim[]
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ 05_synthesize│ Hierarchical merge → BriefDraft
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ 05b_validate │ SummQ adversarial loop (≤3 iters)
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ 06a_evaluate │ BooookScore + FActScore + HAMLET
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │ 06b_export   │ Obsidian Brief.md + Source.md w/ ^uuid anchors
                    └──────────────┘
```

### Key Directories
```
marrow/
├── src/marrow/
│   ├── cli.py                 # Typer entry point
│   ├── config.py              # MarrowConfig Pydantic model + YAML loader
│   ├── orchestrator.py        # Stage discovery, checkpointing, resume logic
│   ├── llm.py                 # The MANDATORY LLM wrapper (cost, retry, logging)
│   ├── schemas/               # Pydantic models crossing stage boundaries
│   │   ├── document.py        # CanonicalDocument, ChapterNode, ParagraphNode
│   │   ├── chunk.py           # ChunkRecord
│   │   ├── graph.py           # EntityRecord, RelationshipRecord, CommunityRecord
│   │   ├── claim.py           # AtomicClaim, AtomicClaimList
│   │   └── brief.py           # BriefDraft, BriefSection, EvaluationReport
│   ├── stages/
│   │   ├── stage_01_ingest.py
│   │   ├── stage_02_chunk.py
│   │   ├── stage_03_graph.py
│   │   ├── stage_04_claims.py
│   │   ├── stage_05_synthesize.py
│   │   ├── stage_05b_validate.py
│   │   ├── stage_06a_evaluate.py
│   │   └── stage_06b_export.py
│   ├── prompts/               # Versioned prompt templates (Jinja2)
│   ├── store/                 # Vector + graph + KV abstractions
│   │   ├── vector.py          # LanceDB wrapper
│   │   ├── graph.py           # NetworkX + JSON persistence
│   │   └── kv.py              # SQLite for run state
│   └── eval/                  # Wrappers around BooookScore, FActScore, HAMLET
├── configs/
│   ├── default.yaml
│   ├── cheap.yaml             # local-model-only
│   └── premium.yaml           # Sonnet for everything
├── runs/                      # Per-book working directories (git-ignored)
├── tests/
└── docs/
    ├── PRD.md
    ├── ARCHITECTURE.md
    ├── API.md
    ├── DATABASE.md
    ├── REPOS.md
    └── PROMPT.md
```

## Tech Stack
- **Language:** Python 3.11+ (typing requires it)
- **CLI:** Typer + Rich
- **Schema:** Pydantic v2
- **Config:** YAML via `pyyaml`, validated through Pydantic
- **Logging:** `structlog` with JSON renderer; logs to `runs/<slug>/logs/`
- **Ingestion:** `docling >= 2.0` (primary), `marker-pdf` (fallback, opt-in)
- **Embeddings:** `transformers` + `jinaai/jina-embeddings-v2-base-en` (local) or Jina API
- **Vector store:** LanceDB (embedded, no server)
- **Graph:** NanoGraphRAG with NetworkX backend; JSON persistence
- **Chunking:** custom late-chunking implementation derived from `ndgigliotti/afterthoughts` patterns
- **Synthesis LLMs:** Claude Sonnet 4.6 via Anthropic SDK; local Llama 3.1 8B via vLLM OpenAI-compatible server
- **Evaluation:** `booookscore` (PyPI), `factscore` (cloned), `HAMLET` scripts (cloned)
- **Testing:** pytest, pytest-recording (for VCR-style LLM tests), hypothesis
- **Linting:** ruff, mypy strict
- **Env:** uv

**Hard rule:** No AGPL dependencies in the default install. MinerU is allowed only as an opt-in extra: `pip install marrow[mineru]`.

## Development Workflow

### Building a Stage (Standard Procedure)
1. Read the matching user story in `docs/PRD.md` (US-001 through US-009).
2. Define / extend Pydantic schemas in `src/marrow/schemas/` first.
3. Write the stage module in `src/marrow/stages/stage_NN_*.py` with a single `run(...)` function.
4. Add unit tests under `tests/stages/test_NN_*.py` — BDD scenarios from the user story map 1:1 to test names.
5. Add an integration test that runs the stage in isolation against a fixture book.
6. Update `configs/default.yaml` with any new tunables.
7. Run `make all`. Commit only when green.

### Commit Format
```
[stage-NN]: <imperative description>

Example: [stage-04]: add semantic dedup with 0.92 threshold
```

## Testing Strategy
- **Unit tests** — every public function in `src/marrow/`. Mock LLM calls via `pytest-recording` cassettes stored in `tests/cassettes/`.
- **Stage tests** — each stage runs against a fixture book in `tests/fixtures/books/` (start with one short public-domain book, e.g., *The Art of War*).
- **End-to-end tests** — full pipeline on the fixture book, asserts file outputs and evaluation thresholds.
- **Determinism tests** — re-running the pipeline twice produces byte-identical chunk UUIDs and brief structure.
- **Coverage target:** ≥ 85% on `src/marrow/` excluding `prompts/` (Jinja templates).

```bash
pytest tests/stages/test_04_claims.py::test_dedup_across_chunks -v
pytest -k "determinism"
pytest --record-mode=once  # refresh LLM cassettes
```

## Environment Variables

```bash
# Required (when using API models)
ANTHROPIC_API_KEY=sk-ant-...
JINA_API_KEY=jina_...           # only if using Jina hosted embeddings

# Optional
MARROW_RUNS_DIR=./runs              # default working directory root
MARROW_LOG_LEVEL=INFO               # DEBUG | INFO | WARNING | ERROR
MARROW_LOCAL_LLM_URL=http://localhost:8000/v1   # vLLM endpoint
MARROW_OBSIDIAN_VAULT=/path/to/vault            # if set, exports go here
```

Setup: `cp .env.example .env && $EDITOR .env`. Never commit `.env`.

## Known Issues & Gotchas

### Late Chunking Seam Artifacts (R2 in PRD)
- **Problem:** Jina v2 caps at 8192 tokens. Books exceed this. Sliding windows create boundary chunks with degraded embeddings.
- **Workaround:** 25% overlap with deterministic dedup by `MD5(chunk_text + book_slug + chapter_path)`. Boundary chunks live in two windows; the dedup keeps the one with more upstream context.

### NanoGraphRAG Top-K Community Filter (R1 in PRD)
- **Problem:** Default top-K=512 silently drops peripheral entities — fatal for the lossless guarantee.
- **Workaround:** After graph build, run a coverage audit (`stage_03_graph.py::audit_coverage`). Any chunk UUID not in any community gets bundled into a synthetic `_orphans` community whose summary is generated separately. Logged as a warning so we can tune K per book.

### Docling Heading Flattening
- **Problem:** Docling sometimes flattens nested headings into uniform `##`.
- **Workaround:** Post-process the `DoclingDocument` JSON in `stage_01_ingest.py::reconstruct_hierarchy` using the ToC pass. If the ToC has 4 levels and Docling produced 1, we re-derive nesting from font size and indent metadata in the layout JSON.

### vLLM Cold Start
- **Problem:** First request after `make serve-local` takes 30–90s while the model loads.
- **Workaround:** `marrow run` does a `/health` warmup ping before stage 04 if `model_role: claim_extraction` resolves to local.

### Claude API Cost Spikes (R3 in PRD)
- **Problem:** Hierarchical merge can spike to $6+ per book on dense academic books.
- **Workaround:** `MarrowConfig.cost.max_per_book` enforced by `marrow.llm.call()`. Hitting the cap pauses the run and prompts. Default = $4.00.

### Obsidian Block ID Collisions (R5)
- **Problem:** 6-char block IDs have ~1 in 16M collision risk per pair.
- **Workaround:** We use full UUID4, not 6-char hashes. Tradeoff: ugly Markdown source, but zero collisions and the user never reads the raw `^uuid` anyway.

## Performance Targets
- End-to-end pipeline: ≤ 90 min on M-series Mac for 300-page book
- Per-stage caps: ingest ≤ 6 min, chunk ≤ 8 min, graph ≤ 15 min, claims ≤ 20 min, synthesize ≤ 25 min, validate ≤ 30 min (3 iters), evaluate ≤ 15 min, export ≤ 30s
- Cost: ≤ $4 / book at default config
- HAMLET leaf-recall: ≥ 92%

## Security Checklist
- [ ] No secrets in code or committed configs
- [ ] `.env` in `.gitignore`
- [ ] LLM call wrapper redacts API keys from logs
- [ ] Working directories never written outside `MARROW_RUNS_DIR` or the configured Obsidian vault
- [ ] No network calls in the default pipeline when `cheap.yaml` is used (verify with `pytest --no-network`)

## Useful Resources
- **Requirements & user stories:** `docs/PRD.md`
- **System design:** `docs/ARCHITECTURE.md`
- **CLI + module APIs:** `docs/API.md`
- **Working directory layout & schemas:** `docs/DATABASE.md`
- **All upstream repos:** `docs/REPOS.md`
- **Master kickoff prompt:** `docs/PROMPT.md`

---
**Last updated:** 2026-04-14
**Owner:** Ali Naserifar
