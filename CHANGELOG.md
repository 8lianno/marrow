# Changelog

All notable changes to Marrow will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-04-15

First public release: the full eight-stage pipeline runs end-to-end, both
execution modes (API + Host) work, and the lossless gate is real. Everything
below is implemented, tested, and merged on `main`.

### Added

#### Pipeline (all eight stages real)

- **01_ingest** — Docling-powered PDF/EPUB parser. Walks structured items
  (`SectionHeaderItem`, `TextItem`, `TableItem`) into a hierarchical
  `SectionNode` tree with page-level provenance. Heading-level refinement by
  text pattern (`Chapter N` → 1, `Section N.M` → 2) compensates for layout
  model flattening. Chapter-detection coverage audit as an explicit gate.
  Pypdf fallback for environments without torch.
- **02_chunk** — paragraph-aligned chunk planner with configurable target
  tokens and overlap. Jina v2 late-chunking embedder (tokenize full doc,
  forward, mean-pool by chunk char-span) with sliding-window support for
  docs exceeding 8192 tokens. LanceDB vector store with fixed 768-dim
  Arrow schema. Paragraph-coverage audit ensures zero drops.
- **03_graph** — lean GraphRAG: per-chunk entity + relationship extraction
  through `marrow.llm.call()` (no nano-graphrag dep), entity merging by
  normalized `canonical_name`, NetworkX graph construction, Louvain
  community detection, per-community LLM summaries. `CoverageAudit` with
  synthetic `_orphans` bucket preserves the 100% chunk-coverage invariant.
- **04_claims** — SciClaims-style atomic claim extraction with two-pass
  dedup: `claim_id` collision merge (exact text), then cosine similarity
  at configurable threshold (default 0.92) on claim-text embeddings.
- **05_synthesize** — hierarchical per-chapter synthesis. Claims routed by
  chunk → chapter, communities routed by majority-vote of their chunks'
  chapters, per-chapter word budget proportional to claim share. LLM
  produces `body_md` with inline `[chunk:UUID]` citations. `merge_tree.json`
  records per-chapter audit (input claims, citations found, missing chunks).
- **05b_validate** — SummQ adversarial quiz loop. Quiz generated once from
  sampled source chunks (stable across iterations), examinee + grader loop
  up to `max_iterations`. Failing chapters (by quiz-failure count) get
  regenerated via stage 05 synthesis. Best-pass-rate draft kept.
- **06a_evaluate** — three orthogonal signals composed into one verdict:
  BooookScore (LLM coherence per chapter), FActScore (LLM verification of
  sampled cited sentences, with γ=10 length penalty), HAMLET (deterministic
  root/branch/leaf recall). Weighted composite + PASS/FAIL verdict against
  configurable thresholds.
- **06b_export** — Obsidian markdown export: `<slug>_Source.md` with
  `^chunk_uuid` block anchors, `<slug>_Brief.md` with `[[<slug>_Source#^UUID]]`
  wikilinks translated from internal `[chunk:UUID]` tokens,
  `<slug>_Evaluation.md` scorecard. Citation round-trip audit as final gate.

#### Two execution modes

- **API Mode** — `marrow run --mode api`. Five providers routed per
  model-role via config:
  - `anthropic` (Claude Sonnet 4.6)
  - `ollama` (local, default model `qwen3:14b`)
  - `gemini` (via `google-genai` SDK)
  - `openrouter` (OpenAI-compatible gateway)
  - `vllm` (legacy path, stub fallback)
  - `stub` (deterministic zero-cost for tests)
- **Host Mode** — `marrow run --mode host`. Marrow writes task JSON to
  `runs/<slug>/host_tasks/`, polls for results. Host agent (Claude Code,
  Codex, Cursor) reads tasks, reasons, writes `HostResult` JSON back.
  Zero API keys, zero metered cost, byte-identical output shape.

#### Claude Code skill

- `skills/claude-code/marrow/SKILL.md` — install via
  `ln -s "$(pwd)/skills/claude-code/marrow" ~/.claude/skills/marrow`.
  Invoke with `/marrow <book.pdf>` in any Claude Code session. Skill
  launches Marrow in background, then processes each task file
  autonomously. Verified end-to-end on the synthetic fixture.

#### Config presets

- `configs/default.yaml` — local Ollama everywhere, $0 metered cost
- `configs/cheap.yaml` — local-only with $0.50 cap
- `configs/openrouter.yaml` — OpenRouter gateway
- `configs/gemini.yaml` — Gemini Flash + Pro
- `configs/anthropic.yaml` — Sonnet 4.6 for synthesis + validation, Ollama
  for hot per-chunk work (claim + graph extraction)

#### Infrastructure

- Single mandatory `marrow.llm.call()` wrapper — cost ledger, retry,
  schema validation, budget enforcement, full prompt/response archiving,
  mode routing, host-mode task file I/O.
- SQLite cost ledger with `llm_calls` + `budget_events` tables.
- Deterministic UUID5 content-addressed IDs for paragraphs, chunks,
  entities, relationships, communities, claims, sections, questions.
- Structlog JSON logging with secret redaction.
- Orchestrator with stage discovery by numeric prefix, `_complete` markers,
  `--resume`, `--force`, and mode-lock enforcement (can't resume a run
  started in a different mode without `--force`).
- CLI: `marrow run`, `status`, `clean`, `next` (host-mode pivot), `version`.
- Test suite: **61 fast tests** (< 5s), plus slow integration tests
  (real Docling, real Jina, real Ollama) network-marked for opt-in
  execution.

#### Docs

- Full stack of design documents at repo root: [PRD.md](PRD.md),
  [ARCHITECTURE.md](ARCHITECTURE.md), [ROADMAP.md](ROADMAP.md),
  [HOST_MODE.md](HOST_MODE.md), [API.md](API.md), [DATABASE.md](DATABASE.md),
  [BRAND.md](BRAND.md), [REPOS.md](REPOS.md), [CLAUDE.md](CLAUDE.md).
- README with top-level pipeline diagram plus eight per-stage Mermaid
  flow diagrams (collapsible), each color-coded by node kind
  (io / op / llm / data / gate).

### Known limitations

- Tested on a 3-page synthetic fixture, not on a real 300-page book.
  Performance budgets defined in the ROADMAP have not been validated
  at full scale.
- Default local synthesis model (`qwen3:14b`) is strong for extraction
  but tends toward verbose, lightly hallucinated synthesis. The lossless
  gate correctly flags this (FActScore 0.00 on the synthetic fixture
  after M5). `configs/anthropic.yaml` or `configs/gemini.yaml` are
  recommended when a PASS verdict is required.
- Python environment is pinned to x86_64 macOS + `torch==2.2.2` +
  `transformers<4.45` + `docling<2.20` + `click<8.2` due to upstream
  wheel availability. Arm64 Python or Linux will need reworked pins.

### Verified

- 61 fast tests passing in ~2s. Real-Ollama + real-Jina + real-Docling
  slow tests pass on a warm cache.
- Host Mode verified end-to-end: Claude Code session drove 10 tasks
  through the skill loop on the synthetic fixture. Cost ledger recorded
  `provider=host` at $0.00. Every result passed Pydantic schema
  validation. Generated 14 entities + 3 clean communities with titled
  summaries.
- Citation round-trip: every `^anchor` in `Brief.md` resolves to an
  anchor in `Source.md`, enforced by the stage 06b audit.
- Determinism: re-running on identical inputs produces byte-identical
  chunk UUIDs and brief structure (temperature=0.0 + content-addressed
  UUID5).
