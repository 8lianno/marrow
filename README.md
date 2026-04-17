# Marrow

> **Read the marrow. Faithful book distillation for deep readers.**

Marrow turns a 300-page non-fiction book into a ~90-page faithful distillation
that preserves the argumentative arc, every named framework, key examples, and
the author's voice — with every paragraph traceable to the source via Obsidian
`^uuid` block anchors.

Not a summary. A distillation — the same book, compressed to 30%.

## How it works

Marrow separates **selection** (what to keep) from **generation** (how to write it)
using a spine architecture:

```
book.pdf
  → 1. Ingest    Docling parses chapters, paragraphs, pages
  → 2. Classify  Flash labels sections: intro / body / appendix / ...
  → 3. Spine     Flash-thinking extracts the structural skeleton
  → 4. Distill   Pro compresses each chapter against its spine
  → 5. Coherence Sonnet audits the whole book, Flash fixes gaps
  → output/book-slug/book-slug.md (~90 pages)
```

The **spine** is the key artifact — a structured JSON skeleton listing every
framework, key example, argumentative move, key term, and voice sample per
chapter. The distillation writes against it, not from scratch. When the output
is wrong, you can see whether selection or writing failed.

### Model roles

| Stage | Model | Job |
|-------|-------|-----|
| Spine | Gemini 2.5 Flash (thinking) | Reasoning about what's load-bearing |
| Distill | Gemini 2.5 Pro | High-quality prose at 30% compression |
| Coherence | Claude Sonnet 4.6 | Whole-book audit (one call) |
| Fix-ups | Gemini 2.5 Pro | Targeted chapter rewrites |

**Cost:** ~$1.50–2.00 per book. **Runtime:** ~15–25 minutes.

## Quick start

```bash
# Install
uv venv && source .venv/bin/activate
uv pip install -e .

# Set API keys
export GEMINI_API_KEY=...
export ANTHROPIC_API_KEY=sk-ant-...

# Distill a book
marrow book.pdf
```

Output lands in `runs/<book-slug>/05_coherence/`:

```
book-slug.md          # the distillation (~90 pages of Obsidian markdown)
book-slug.spine.md    # the structural skeleton (3-5 pages)
book-slug.source.md   # original text with ^paragraph-id anchors
manifest.json         # cost, duration, model versions
coherence_report.json # the audit results
```

## Options

```bash
marrow book.pdf                        # full pipeline, default config
marrow book.pdf --compression 0.40     # 40% instead of 30%
marrow book.pdf --spine-only           # stages 1-3 only (inspect the spine)
marrow book.pdf --skip-coherence       # stages 1-4 only (faster, ~70% quality)
marrow book.pdf --force                # wipe previous run and restart
marrow book.pdf --vault ~/obsidian     # copy output to Obsidian vault
marrow clean <book-slug>               # delete working directory
```

## Configuration

Config resolution: **defaults → `configs/default.yaml` → user `--config` file
→ env vars (`MARROW_*`) → CLI flags**.

Key environment variables:

```bash
GEMINI_API_KEY=...              # Required
ANTHROPIC_API_KEY=sk-ant-...    # Required
MARROW_RUNS_DIR=./runs          # Working directory root
MARROW_OBSIDIAN_VAULT=/path     # Auto-export to vault
MARROW_COST_MAX_PER_BOOK=3.00   # Hard ceiling (aborts if exceeded)
```

## Design decisions

**Why spine/distill split?** v1 had 8 stages that all tried to compensate for
weak synthesis. The spine separates the hard decision (what's load-bearing)
from the easy job (compress it). Flash-thinking is excellent at structured
extraction; Pro is excellent at prose compression against a known target.

**Why not local models?** Quality over cost. The difference between a $0.50
local-model run and a $1.50 API run is negligible for 30 books/year. The
difference in output quality is not.

**Why deterministic verification?** v1 used quiz-based validation (HAMLET,
SummQ) that couldn't distinguish "the brief is bad" from "the quiz is bad."
v2 fuzzy-matches spine items against the distillation text — if framework X
isn't mentioned, it's missing. No LLM needed for that check.

**Why continuation loops?** A dense 15,000-word chapter compressed to 30%
needs ~4,500 words of output. Gemini's output window is ~8K tokens (~6K words).
Most chapters fit in one call, but long ones need continuation. The loop uses
`finish_reason` as the primary truncation signal, not word-count heuristics.

## Development

```bash
uv pip install -e ".[dev]"
pytest tests/ -v -k "not slow"    # 18 unit tests
ruff check . && ruff format --check .
```

## License

[MIT](LICENSE)
