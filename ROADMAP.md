# Marrow — Build Roadmap

> Execution plan for shipping v1.0. Companion to [PRD.md](PRD.md) (what to build) and [ARCHITECTURE.md](ARCHITECTURE.md) (how it's shaped). This file answers **in what order**.

## Strategy

**Walking skeleton first, then fill stages.** Build the orchestrator, mode abstraction, file contract, and export end-to-end on a tiny fixture book before any single stage is "real." The most expensive things to retrofit are the cross-cutting concerns (mode parity, citation round-tripping, resumability, determinism) — those land in M0. After that, replace stub stages with real implementations one at a time, in the order that maximizes early signal.

**Mode parity is a continuous test, not a final feature.** Both Host Mode and API Mode are wired up in M0 and exercised in CI from day one. Every milestone adds a parity test for the newly-real stage.

## Pre-M0 — Naming Scrub (½ day)

Single PR, no functionality. Closes the LBB→Marrow rename so no import gets written under the wrong name.

- Rename `LBB` → `Marrow` and `lbb` → `marrow` across [CLAUDE.md](CLAUDE.md), [PROMPT.md](PROMPT.md), [API.md](API.md), [DATABASE.md](DATABASE.md), [REPOS.md](REPOS.md)
- Lock package name `marrow`, CLI binary `marrow`, Python module `marrow`
- Rename `LBBConfig` → `MarrowConfig` in all doc references

## M0 — Walking Skeleton (1 week)

**Goal:** End-to-end pipeline runs on *The Art of War* (~80pp public-domain) in both modes, produces an Obsidian `Brief.md` + `Source.md` with round-trippable `^uuid` anchors, even though the brief content is trivial.

**Real:**
- Project scaffold (`pyproject.toml` via uv, `src/marrow/`, `tests/`, `configs/`, `Makefile`)
- Pydantic v2 schemas: `CanonicalDocument`, `ParagraphNode`, `ChunkRecord`, `AtomicClaim`, `BriefDraft`, `StageResult`, `MarrowConfig`
- `marrow.llm.call()` wrapper with two backends:
  - **API backend** — Anthropic SDK, cost ledger to SQLite, retry + Pydantic schema validation
  - **Host backend** — writes task JSON to `runs/<slug>/host_tasks/`, polls for matching result JSON, enforces 8K-in/4K-out cap
- Orchestrator: stage discovery by numeric prefix, `_complete` markers, `--resume`, mode lock
- CLI: `marrow run`, `marrow status`, `marrow clean`, `marrow next` (Host Mode pivot)
- `06b_export` — full real implementation (Obsidian markdown, UUID5 block anchors, bidirectional links)
- Logging: `structlog` JSON to `runs/<slug>/logs/`
- Cost ledger: SQLite, per-call attribution

**Stubbed (text pass-through, schema-valid output):**
- `01_ingest` — Docling **integrated for real** (cheapest stage to get right; can't be faked believably; output drives every downstream schema)
- `02_chunk` — paragraph-per-chunk, no embeddings, deterministic UUID5
- `03_graph` — empty `entities.jsonl` + `_orphans` community containing every chunk
- `04_claims` — one claim per chunk (chunk text verbatim)
- `05_synthesize` — concatenate first sentence of each chunk
- `05b_validate` — pass-through, no quiz
- `06a_evaluate` — emit zeros for all metrics with `"stub": true` flag

**Tests:**
- E2E: full pipeline on Art of War in both modes
- Mode parity: SHA256 of `Source.md` byte-identical across modes; `Brief.md` structure (sections, anchor count, anchor format) byte-identical
- Determinism: re-run produces byte-identical chunk UUIDs and export
- Resume: kill mid-pipeline, `--resume` finishes without recomputing prior stages
- Citation round-trip: every `^uuid` in `Brief.md` resolves to a paragraph in `Source.md`
- Cassette-based LLM tests via `pytest-recording` (no network in CI by default)

**Exit criteria:**
- `make all` green
- `marrow run tests/fixtures/books/art-of-war.pdf --mode host` produces a valid Obsidian export
- `marrow run tests/fixtures/books/art-of-war.pdf --mode api` produces a byte-identical `Source.md` and structurally-identical `Brief.md`
- Coverage ≥ 85% on `src/marrow/` excluding `prompts/`

## Stage-Fill Milestones

Each milestone replaces one stub with the real implementation, adds the stage's coverage audit, adds a mode-parity test for that stage, and reports the relevant PRD metric (even if the value is bad early). Order chosen to maximize early signal: the stages most likely to leak content (claims, synthesis) come earlier than the most expensive new dependency (graph).

| # | Milestone | User Story | Real Stage | Time | Key Dependency | Headline Metric |
|---|---|---|---|---|---|---|
| **M1** | Real ingest | US-001 | `01_ingest` | 1 wk | Docling | 100% chapter detection, ≤6 min for 300pp |
| **M2** | Real chunking | US-002 | `02_chunk` | 1 wk | Jina v2 + LanceDB | +2 nDCG@10 vs naive, stable UUIDs |
| **M3** | Real claims | US-004 | `04_claims` | 1.5 wk | vLLM + Llama 3.1 8B | ≥95% gold-corpus recall, ≥8 claims/1000 tokens |
| **M4** | Real graph | US-003 | `03_graph` | 1.5 wk | NanoGraphRAG | 100% chunk coverage (audit-enforced) |
| **M5** | Real synthesis | US-005 | `05_synthesize` | 2 wk | Claude Sonnet 4.6 | Compression 45–55pp, dialogue normalization |
| **M6** | Real validate + evaluate | US-006, US-007 | `05b_validate`, `06a_evaluate` | 2 wk | SummQ + BooookScore + FActScore + HAMLET | Quiz pass ≥0.90 in ≤3 iters; HAMLET leaf-recall ≥92% |

**Why claims before graph (M3 before M4):** Synthesis (M5) depends on claims, not on the graph (graph informs *which* claims to merge, but the merge itself is claim-driven). Landing claims first means M5 can ship against a stub graph with degraded but functional output. Landing graph first would block M5 behind two heavy dependencies.

**Why evaluate (06a) lands at M6 not earlier:** HAMLET requires real synthesis output to score against. Earlier milestones get partial signal from FActScore (atomic precision on stub claims) but the lossless gate only becomes meaningful once synthesis is real.

## Cross-Cutting (every milestone)

- **Mode parity test** for the newly-real stage. Same input → byte-identical output across modes (LLM stochasticity gated by `temperature=0.0` + recorded cassettes).
- **Coverage audit** for any stage that could drop content. Audit emits warnings and **blocks** the `_complete` marker. No silent omissions.
- **Determinism test.** Re-running the milestone's stage produces byte-identical output.
- **Performance gate.** Stage runs within the per-stage cap from CLAUDE.md / PRD.
- **Cost gate.** API Mode milestone run on Art of War costs < $0.50; full 300pp run extrapolates to < $4.
- **Cassette refresh on a recurring schedule** so mocked LLM tests don't drift from real model behavior.

## Test Fixtures

Lock these in M0; do not change without explicit decision.

- `tests/fixtures/books/art-of-war.pdf` — public-domain, ~80pp, the regression book
- `tests/fixtures/papers/attention-is-all-you-need.pdf` — short academic paper, used for chapter/section/citation parsing edge cases
- `tests/cassettes/` — VCR-style LLM recordings for offline CI

The 300-page performance target is validated against a separate, larger book (chosen at M5) but **not** committed to the test corpus — it's an out-of-band benchmark.

## v1.1 (Post-Ship)

- US-010 `marrow ask` corpus query
- MCP server interface for Host Mode (file-based protocol ships in v1.0; MCP is an additive transport)
- Multilingual support (Persian first per BRAND.md hints)
- Multimodal layer (images, diagrams, figure captions)

## Verification (Whole-Roadmap)

Roadmap is "done" when all eight PRD success metrics (O1–O8) are met on a 300-page non-fiction book chosen at M5, in **both modes**, with a green CI run that includes the full mode-parity, determinism, coverage-audit, and citation-round-trip suites.

## What This Roadmap Is Not

A Gantt chart. Time estimates are nominal (single-developer weeks). Milestones can compress or stretch based on what M0 reveals about the framework dependencies (especially Docling output quality and NanoGraphRAG fidelity on real books). The **order** is the load-bearing claim, not the calendar.
