# Changelog

All notable changes to Marrow will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-04-18

Codex-default pipeline with runtime optimization, smart titles, and dual mode.

### Added

- **Codex CLI provider** — spine, distill, and coherence stages routed through
  `codex exec` by default. $0 marginal cost (ChatGPT subscription). Classify
  stays on Gemini Flash Lite (~$0.001/book).
- **Parallel stages** — spine extraction and distillation run 3 chapters
  concurrently via `ThreadPoolExecutor`. Thread-safe SQLite ledger with WAL mode.
- **Auto-resume** — if a run crashes at stage 4, re-running the same command
  skips completed stages automatically. No `--resume` flag needed.
- **Brief mode** (`--brief`) — ~20% compression, skeleton-only output. Default
  full mode unchanged at ~30%.
- **Smart chapter titles** — generic auto-split names ("Introduction Part N")
  replaced with meaningful titles from the spine's first framework name.
- **Section headings** — distilled chapters include `###` headings for topic
  shifts, making output scannable.
- **Spine callouts** — each chapter in both `.md` (Obsidian collapsible) and
  `.epub` (styled div) shows thesis, frameworks, and key examples at the top.
- **EPUB export** — clean `.epub` with proper CSS, chapter navigation, spine
  callouts, and a spine appendix. No citation clutter.
- **Per-call reasoning effort override** — codex calls run at `medium` effort
  regardless of user's global config, cutting per-call time from 5-15 min to
  ~1 min without changing `~/.codex/config.toml`.
- **Coherence excerpt mode** — audit sends chapter excerpts (first/last 200
  words) instead of full 25K-word draft, reducing coherence call from 20+ min
  to ~1 min.
- **Progress logging** — codex calls emit `codex_exec_progress` every 15s
  and `codex_exec_completed` with elapsed time and response size.
- **CLI call count column** — end-of-run summary shows LLM call count per stage.
- **Auth/quota error detection** — clear error messages for codex login failures
  and subscription quota exhaustion.
- **Gemini fallback preset** — `configs/gemini.yaml` for deterministic runs
  (~$0.25/book, ~20 min).

### Changed

- **Default provider**: codex (was gemini-flash-latest)
- **Spine caps unclamped**: soft guidance (3-10 per category) replaces hard
  5/5/8/5 limits. Spines are 2-3× richer.
- **Retry prompt**: asks for valid JSON instead of shorter output.
- **Voice instruction**: ghostwriting-grade ("copy the author's register,
  tics, sentence rhythm") instead of generic "match the voice."
- **Cost estimator**: only counts metered (Gemini) stages in projection,
  preventing false $3 ceiling aborts on codex runs.
- **Codex timeout**: 60 min (was 20 min) to handle dense chapters.
- **Distill prompts**: stripped `[^pid:uuid]` prefixes and citation output
  rules for smaller prompts and cleaner output.
- **Coherence fix-up**: asserts `body_text` is str.

### Performance

| Metric | v0.2.0 | v0.3.0 |
|--------|--------|--------|
| Runtime | 21 min | **14 min** |
| Cost | $0.24 | **$0.001** |
| Spine success | 10/12 | **12/12** |
| Output words | 17,637 | **25,512** |
| Output pages | 64 | **92** |

### Fixed

- Stale `spine_result` reference in parallel spine stage (NameError after
  all futures completed)
- `gpt-5.1-codex` model ID not available on ChatGPT accounts — now uses
  codex default model from `~/.codex/config.toml`

---

## [0.2.0] — 2026-04-17

Complete rebuild of the distillation pipeline. Replaces the 8-stage v0.1.0
architecture with a 5-stage spine architecture that separates selection from
generation.

### Added

- **Spine architecture** — new two-phase approach: extract a structural
  skeleton (spine) per chapter, then distill against it. The spine is a
  first-class inspectable artifact listing frameworks, key examples,
  argumentative moves, key terms, and a voice sample.
- **Stage 02: Classify** — single Gemini Flash call classifies top-level
  sections as intro / body / conclusion / appendix / foreword / other.
  Each type gets a different compression ratio (intro 12%, body 30%,
  appendix 70%).
- **Stage 03: Spine** — Gemini 2.5 Flash with thinking mode extracts the
  structural skeleton per chapter. Thinking mode enables extended
  reasoning before answering, producing better selection decisions.
- **Stage 04: Distill** — Gemini 2.5 Pro compresses each chapter against
  its spine at the configured compression ratio. Continuation loop with
  `finish_reason` detection handles chapters that exceed the output
  window. Overlap-aware merge prevents duplication at boundaries.
- **Stage 05: Coherence** — four-phase final stage:
  (A) deterministic fuzzy-match of spine items against distillation text,
  (B) Claude Sonnet 4.6 audit for voice drift / broken threads / redundancy,
  (C) targeted Pro fix-ups for flagged chapters,
  (D) Obsidian markdown output assembly with wikilink citations.
- **Thinking mode support** in `LLMCaller` — Gemini calls can enable
  `ThinkingConfig` via `thinking: true` in `ModelRoute`. Thinking parts
  are stripped from the response; only the final answer is returned.
- **`LLMResponse` class** — `call_raw()` returns structured response with
  `finish_reason` field, enabling the continuation loop.
- **Code fence stripping** in `_validate()` — handles models that wrap
  JSON output in markdown code fences.
- **New schemas**: `ChapterSpine`, `Framework`, `Example`, `KeyTerm`,
  `Spine`, `ChapterDistillation`, `Distillation`, `CoherenceReport`,
  `MissingSpineItem`, `VoiceDrift`, `BrokenThread`, `Redundancy`,
  `SectionClassification`, `BookClassification`.
- **6 prompt templates**: `classify_sections.j2`, `spine_extract.j2`,
  `distill_chapter.j2`, `distill_continue.j2`, `coherence_audit.j2`,
  `coherence_fix.j2`.

### Changed

- **Default models**: Gemini 2.5 Flash (thinking) for spine, Gemini 2.5
  Pro for distillation, Claude Sonnet 4.6 for coherence. No local models.
- **Config shape**: stripped `ChunkConfig`, `GraphConfig`, `ClaimsConfig`,
  `SynthesizeConfig`, `ValidateConfig`, `EvaluateConfig`, `HostConfig`,
  `MonitorConfig`. Added `ClassifyConfig`, `SpineConfig`, `DistillConfig`,
  `CoherenceConfig`. `ModelRoute` gains `thinking` and `thinking_budget`
  fields. Provider restricted to `anthropic | gemini | stub`.
- **Cost ceiling**: default reduced from $4.00 to $3.00 per book.
- **CLI**: simplified to `marrow <book.pdf>`, `clean`, `version`. Added
  `--compression`, `--spine-only`, `--skip-coherence`, `--vault` flags.
- **Output format**: single output directory at `05_coherence/` with
  `<slug>.md`, `<slug>.spine.md`, `<slug>.source.md`, `manifest.json`.
- **`pyproject.toml`**: version 2.0.0. Added `google-genai`, `jinja2`.
  Removed `reportlab`.

### Removed

- **7 stages**: `02_chunk`, `03_graph`, `04_claims`, `05_synthesize`,
  `05b_validate`, `06a_evaluate`, `06b_export`.
- **Host Mode**: `host.py`, `HostTask`, `HostResult`, `HostTaskClaim`,
  file-based task polling, `marrow next`, `marrow submit`, `marrow tasks`.
- **Watch daemon**: `watch.py`, `marrow watch`, `MonitorConfig`.
- **Progress reporting**: `progress.py`, `RichProgressReporter`,
  `PlainProgressReporter`.
- **Local model providers**: ollama, vLLM, openrouter call paths.
- **Evaluation harness**: HAMLET, BooookScore, FActScore.
- **Quiz validation**: SummQ adversarial loop.
- **Embeddings / vector store**: Jina v2 embedder, LanceDB, late-chunking.
- **Graph infrastructure**: NetworkX, Louvain, community summaries.
- **Config presets**: `ollama.yaml`, `openrouter.yaml`, `cheap.yaml`,
  `express.yaml`, `express_stub.yaml`, `anthropic.yaml`, `gemini.yaml`.
- **Schemas**: `ChunkRecord`, `EntityRecord`, `RelationshipRecord`,
  `CommunityRecord`, `AtomicClaim`, `BriefDraft`, `BriefSection`,
  `QuizQuestion`, `EvaluationReport`, and related models.
- **9 prompt templates**: all v0.1.0 Jinja2 templates.
- **~8,500 lines of code** net removed.

---

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
