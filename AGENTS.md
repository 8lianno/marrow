# Marrow — Codex Agent Instructions

> Codex reads this file before every session in this repo.

## What this project does

Marrow distills a non-fiction book (PDF/EPUB) into a ~60-page faithful
distillation with an EPUB output. It runs a 5-stage pipeline where stages
3-5 use LLM calls routed through `codex exec` by default.

## Running the pipeline

```bash
# Full pipeline (default: codex provider for LLM calls)
marrow run "input/book.epub" --force

# Gemini provider (faster, costs ~$0.25/book)
marrow run "input/book.epub" --force --config configs/gemini.yaml
```

Output appears in `runs/<book-slug>/05_coherence/`:
- `<slug>.epub` — clean readable EPUB
- `<slug>.md` — Obsidian markdown with citations
- `<slug>.spine.md` — structural skeleton
- `manifest.json` — cost, duration, metadata

## Pipeline stages (in order)

| Stage | What it does | LLM? | Time |
|-------|-------------|------|------|
| 1. Ingest | Docling parses PDF/EPUB into structured chapters | No | ~3s |
| 2. Classify | Labels sections as intro/body/conclusion/appendix | 1 call | ~3s |
| 3. Spine | Extracts structural skeleton per chapter (thesis, frameworks, examples) | 1 call/chapter | ~5-10 min |
| 4. Distill | Compresses each chapter to 30% against its spine | 1 call/chapter | ~10-15 min |
| 5. Coherence | Deterministic coverage check → LLM audit → fix-ups → EPUB export | 1-3 calls | ~1-2 min |

## When editing code

- **Stage contract**: every stage exports `run(working_dir, config) -> StageResult`
- **LLM calls**: always go through `LLMCaller` in `src/marrow/llm.py` — never call APIs directly
- **Schemas**: all inter-stage data is Pydantic v2 in `src/marrow/schemas/`
- **Tests**: run `pytest tests/ -v -k "not slow"` — must pass before committing

## Common tasks

### "Run on a book"
```bash
source .venv/bin/activate
marrow run "input/No More Mr. Nice Guy! - Robert A. Glover.epub" --force
```

### "Run tests"
```bash
pytest tests/ -v -k "not slow"
```

### "Install from scratch"
```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
uv pip install google-genai ebooklib
```

### "Check what models are configured"
```bash
python -c "from marrow.config import load_config; c = load_config(); print(f'spine={c.models.spine.provider}:{c.models.spine.model_id}'); print(f'distill={c.models.distill.provider}:{c.models.distill.model_id}'); print(f'coherence={c.models.coherence.provider}:{c.models.coherence.model_id}')"
```

## Key files

| File | Purpose |
|------|---------|
| `src/marrow/cli.py` | CLI entry point (`marrow run`, `clean`, `version`) |
| `src/marrow/orchestrator.py` | Stage discovery, `_complete` markers, pipeline execution |
| `src/marrow/llm.py` | LLM wrapper: gemini, codex, stub providers |
| `src/marrow/config.py` | `MarrowConfig` Pydantic model + YAML loader |
| `src/marrow/stages/stage_01_ingest.py` | Docling/pypdf → `CanonicalDocument` |
| `src/marrow/stages/stage_02_classify.py` | Section type classification |
| `src/marrow/stages/stage_03_spine.py` | Spine extraction (the critical stage) |
| `src/marrow/stages/stage_04_distill.py` | Distillation with continuation loop |
| `src/marrow/stages/stage_05_coherence.py` | Coverage check + audit + fix-ups + EPUB export |
| `src/marrow/prompts/*.j2` | Jinja2 prompt templates |
| `configs/default.yaml` | Default config (codex provider) |
| `configs/gemini.yaml` | Full-Gemini fallback preset |

## Do not

- Change model IDs or providers without being asked
- Add new dependencies without being asked
- Modify prompt templates unless specifically fixing a bug
- Touch `.env` or commit API keys
- Reformat code you didn't change
