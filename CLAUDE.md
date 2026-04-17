# Marrow v2 — Claude Development Guide

> **Auto-loaded every Claude Code / Codex session.** Read this first before writing any code.

## Project One-Liner
CLI that distills a 300-page non-fiction book into a faithful ~90-page brief using a spine architecture: separate selection (what to keep) from generation (how to write it).

## Quick Commands

```bash
# Environment
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Development
pytest tests/ -v -k "not slow"    # unit tests
ruff check . && ruff format --check .
mypy src/

# Pipeline
marrow book.pdf                           # full pipeline
marrow book.pdf --compression 0.40        # 40% instead of 30%
marrow book.pdf --spine-only              # stages 1-3 only
marrow book.pdf --skip-coherence          # stages 1-4 only
marrow book.pdf --force                   # wipe and restart
marrow clean <book-slug>                  # delete working directory
```

## Architecture

```
book.pdf
  → 1. Ingest   (Docling, no LLM)                → document.json
  → 2. Classify (Flash, one call)                 → classification.json
  → 3. Spine    (Flash-thinking, one call/chapter) → spine.json
  → 4. Distill  (Pro, one call/chapter + continuation) → distillation.json
  → 5. Coherence (deterministic + Pro-thinking audit + Pro fix-ups) → final output
```

### Model Roles (provider mix — default: codex + gemini)
| Role | Provider | Model | Why |
|------|----------|-------|-----|
| Classify | Gemini | `gemini-flash-lite-latest` | One cheap call; not worth routing to codex |
| Spine | Codex CLI | `gpt-5.1-codex` | 10-17 calls/book; subscription-free |
| Distill | Codex CLI | `gpt-5.1-codex` | 10-15 calls/book; the cost hog, now free |
| Coherence | Codex CLI | `gpt-5.1-codex` | One whole-book call; free |

### Key Design Decisions
- **Codex default, Gemini fallback**: Heavy stages use Codex CLI (subscription, $0 marginal). Use `--config configs/gemini.yaml` for speed/determinism.
- **Spine/distill split**: Selection (spine) is separate from generation (distill). The spine is a first-class artifact.
- **Length by construction**: `target_words = source_words * compression_ratio`. No prompted hopes.
- **Deterministic verification**: Spine items fuzzy-matched against distillation text. Not an LLM vibes check.

## Key Directories

```
marrow/
├── src/marrow/
│   ├── cli.py                 # Typer: marrow <book.pdf>
│   ├── config.py              # MarrowConfig + YAML loader
│   ├── orchestrator.py        # Stage discovery, checkpointing
│   ├── llm.py                 # Gemini + Codex + stub providers
│   ├── schemas/
│   │   ├── document.py        # CanonicalDocument, SectionNode, ParagraphNode
│   │   ├── classify.py        # SectionClassification, BookClassification
│   │   ├── spine.py           # ChapterSpine, Framework, Example, KeyTerm, Spine
│   │   ├── distill.py         # ChapterDistillation, Distillation
│   │   ├── coherence.py       # CoherenceReport, MissingSpineItem
│   │   └── run.py             # StageResult, RunManifest, CostBreakdown
│   ├── stages/
│   │   ├── stage_01_ingest.py    # Docling → structured chapters
│   │   ├── stage_02_classify.py  # Flash classifies sections by role
│   │   ├── stage_03_spine.py     # Flash-thinking extracts structural skeleton
│   │   ├── stage_04_distill.py   # Pro distills against spine + continuation
│   │   └── stage_05_coherence.py # Deterministic check + Sonnet audit + output
│   ├── prompts/               # Jinja2 templates (6 total)
│   ├── store/ledger.py        # SQLite cost ledger
│   ├── ids.py                 # Content-addressed UUID5
│   ├── slug.py                # Book slug derivation
│   ├── io.py                  # JSON/JSONL helpers
│   ├── errors.py              # Error hierarchy
│   └── logging.py             # Structlog config
├── configs/default.yaml       # Default config (only one)
├── runs/                      # Per-book working directories (git-ignored)
└── tests/
```

## Code Style & Conventions

### Naming
- **Files:** `snake_case.py`
- **Classes:** `PascalCase`
- **Functions / variables:** `snake_case`
- **Constants:** `UPPER_SNAKE_CASE`
- **Stage modules:** `stage_NN_name.py` — numeric prefix for discovery order

### Stage Contract
Every stage module exports exactly one function:

```python
def run(working_dir: Path, config: MarrowConfig) -> StageResult:
    """
    Reads previous stage's outputs from working_dir,
    writes its own outputs to working_dir / f"{NN}_{name}/",
    returns a StageResult with metrics and cost.
    """
```

### LLM Calls — Mandatory Wrapper
**Never call `genai.Client` or `Anthropic` directly.** Use `LLMCaller`:

```python
from marrow.llm import LLMCaller

caller = LLMCaller(working_dir, config)

# High-level: returns validated schema or string
spine = caller.call(
    stage="03_spine",
    prompt=prompt,
    model_role="spine",           # routes to Flash-thinking
    response_schema=ChapterSpine, # validates + parses JSON
)

# Low-level: returns LLMResponse with finish_reason (for continuation loop)
raw = caller.call_raw(
    stage="04_distill",
    prompt=prompt,
    model_role="distill",         # routes to Pro
)
if raw.finish_reason == "MAX_TOKENS":
    # continue...
```

### Pydantic-First Data Model
Every artifact crossing a stage boundary is a Pydantic v2 model serialized to JSON. No raw dicts.

## Environment Variables

```bash
GEMINI_API_KEY=...              # Required (Stage 2 classify)
MARROW_RUNS_DIR=./runs
MARROW_LOG_LEVEL=INFO
MARROW_OBSIDIAN_VAULT=/path
MARROW_COST_MAX_PER_BOOK=3.00
```

Codex authentication: uses your existing `codex` CLI auth (ChatGPT login or API key). Run `codex login` once if needed.

## Testing

```bash
pytest tests/ -v -k "not slow"   # fast unit tests
pytest tests/ -v                  # all tests including Docling + Codex
```

## Cost Targets (default: codex + gemini mix)
- Classify (Gemini Flash Lite): ~$0.001/book
- Spine + Distill + Coherence (Codex CLI): $0 marginal (subscription)
- **Total: ~$0.001/book on subscription. Runtime: 50-75 min.**

Fallback to full-Gemini (`--config configs/gemini.yaml`):
- **Total: ~$0.25/book. Runtime: 20-25 min.**

---
**Last updated:** 2026-04-17
**Owner:** Ali Naserifar
