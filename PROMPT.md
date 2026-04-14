# Marrow — Master Development Prompt for Claude Code / Codex

> **Paste this into a fresh Claude Code or Codex session as the very first message.** It loads the project context, defines the build sequence, and tells the agent how to operate.

---

## ROLE

You are the lead implementing engineer for **Marrow** — a Python CLI that compresses 300-page non-fiction books into ~50-page conceptual briefs with zero silent omissions and 100% citation traceability to source paragraphs in Obsidian.

You are working in a fresh repository. The product is fully specified across these documents — read them in order before writing any code:

1. `docs/PRD.md` — requirements, 10 user stories, success metrics, risks
2. `docs/ARCHITECTURE.md` — system design, stage pipeline, decision log
3. `docs/DATABASE.md` — Pydantic schemas, working directory layout, storage
4. `docs/API.md` — CLI surface, internal module API, stage contract
5. `docs/REPOS.md` — every external dependency, pinned versions, license rationale
6. `CLAUDE.md` — conventions, gotchas, commands (auto-loaded each session)

Do not invent new architecture. Do not pick alternative tools. The 6-stage pipeline and tooling choices are locked. If you find a hard blocker, surface it as a question — do not silently substitute.

---

## OPERATING RULES

1. **Stage isolation.** Build one stage at a time, in order. Each stage is a self-contained module under `src/marrow/stages/stage_NN_*.py` with exactly one `run(working_dir, config) -> StageResult` function. Stages do not import each other; they communicate via files in the working directory.

2. **Schema first, code second.** Before implementing any stage, define its Pydantic models in `src/marrow/schemas/`. The schemas in `docs/DATABASE.md` are the contract — implement them exactly.

3. **Test before move-on.** No stage is "done" until:
   - Its BDD scenarios from the matching user story exist as pytest tests
   - The fixture-book end-to-end test for that stage passes
   - `make all` (lint + typecheck + test) is green

4. **LLM calls go through the wrapper.** Never `import anthropic` outside `src/marrow/llm.py`. Every model call uses `marrow.llm.call(stage=..., model_role=..., response_schema=..., chunk_uuids=...)`. This is non-negotiable — cost telemetry, retry, structured output validation, and budget enforcement all depend on it.

5. **Determinism is mandatory.** Every UUID is content-addressed (formulas in `docs/DATABASE.md` §6). Re-running any stage on identical inputs must produce byte-identical outputs. Add a determinism test for every stage.

6. **Coverage guards over trust.** Stages that could drop content (especially 03_graph and 05_synthesize) must have explicit audits that emit structured warnings on partial coverage. Audits do not raise — they log and let the next stage decide.

7. **Per-chunk failures are isolated.** If extraction fails on one chunk out of 1000, log a warning, mark that chunk as failed, and continue. Never abort a stage for a single-chunk problem. The only conditions that abort a stage are: missing inputs, malformed config, or write-permission errors.

8. **Ask before deviating.** If a user story's acceptance criteria conflict with the architecture, stop and ask. Do not invent a third option.

---

## BUILD SEQUENCE

Implement in this exact order. Each phase ends with a working, testable increment.

### Phase 0 — Project Skeleton (Day 1)

**Goal:** Empty pipeline that runs end-to-end with no-op stages.

- [ ] `pyproject.toml` with the dependency manifest from `docs/REPOS.md` §"Install Manifest"
- [ ] `src/marrow/` package layout from `CLAUDE.md` §"Key Directories"
- [ ] `src/marrow/config.py` — `MarrowConfig` Pydantic model + YAML loader with the layered override precedence (defaults < file < env < CLI)
- [ ] `configs/default.yaml`, `configs/cheap.yaml`, `configs/premium.yaml`
- [ ] `src/marrow/cli.py` — Typer entry point with `marrow run`, `marrow status`, `marrow clean` (stubs are fine)
- [ ] `src/marrow/orchestrator.py` — stage discovery, dependency check, resume logic, run manifest persistence
- [ ] `src/marrow/llm.py` — the mandatory wrapper. **Implement this fully now**, even though no stage uses it yet. Cost ledger SQLite schema from `docs/DATABASE.md` §4.
- [ ] `src/marrow/schemas/` — `RunManifest`, `StageResult`, `MarrowConfig` models
- [ ] `src/marrow/store/` — LanceDB, NetworkX, SQLite wrappers
- [ ] `src/marrow/logging.py` — structlog setup
- [ ] One no-op stage `src/marrow/stages/stage_00_noop.py` to verify discovery works
- [ ] `make all` green
- [ ] `marrow run --dry-run fake.pdf` prints the planned stage sequence

**Acceptance:** `marrow run --dry-run` shows all stages discoverable, `marrow config show` prints resolved defaults, `pytest -q` passes.

### Phase 1 — Stage 01 Ingestion (US-001) (Days 2–3)

- [ ] `src/marrow/schemas/document.py` — `CanonicalDocument`, `SectionNode`, `ParagraphNode` from `docs/DATABASE.md` §2.1
- [ ] `src/marrow/stages/stage_01_ingest.py` using Docling
- [ ] Per-page failure isolation (try/except per page)
- [ ] Hierarchy reconstruction post-processor (handle Docling's heading flattening — see `CLAUDE.md` Known Issues)
- [ ] Determinism: same input → byte-identical `document.json`
- [ ] BDD tests from US-001
- [ ] Fixture book under `tests/fixtures/books/` (use a public-domain text — *The Art of War* is small and works)
- [ ] End-to-end test: `marrow run tests/fixtures/books/sun-tzu.pdf --to ingest`

**Acceptance:** Fixture book ingests in <30s with 100% chapter detection. All US-001 BDD scenarios pass.

### Phase 2 — Stage 02 Late Chunking (US-002) (Days 4–5)

- [ ] `src/marrow/schemas/chunk.py` — `ChunkRecord`
- [ ] `src/marrow/stages/stage_02_chunk.py` using Jina v2 + sliding window late chunking
- [ ] Sentence-aligned boundaries via NLTK
- [ ] Sliding window with 25% overlap and dedup by content-addressed UUID
- [ ] LanceDB persistence + `chunks.jsonl` mirror
- [ ] Anaphora resolution unit test (the smoking-gun test from US-002)
- [ ] Determinism test
- [ ] BDD tests from US-002

**Acceptance:** Fixture book produces stable chunks across re-runs. Anaphora test passes. `marrow run --to chunk` completes.

### Phase 3 — Stage 03 GraphRAG (US-003) (Days 6–8)

- [ ] `src/marrow/schemas/graph.py` — `EntityRecord`, `RelationshipRecord`, `CommunityRecord`, `CoverageAudit`
- [ ] `src/marrow/store/graph.py` — NanoGraphRAG adapter that mirrors into our Pydantic JSONL
- [ ] `src/marrow/stages/stage_03_graph.py` — extraction, Leiden clustering, community summaries
- [ ] **Coverage audit** — the most important guard in the pipeline. Every chunk must end up in at least one community. Orphans go into a synthetic `_orphans` community whose summary is generated separately.
- [ ] Resumability — checkpoint after every N chunks, support kill-mid-stage
- [ ] Cost telemetry verified against the cost ledger
- [ ] BDD tests from US-003

**Acceptance:** Coverage audit reports 100% on fixture book. Cost stays under $0.50 on the small fixture. Resume works after a forced kill.

### Phase 4 — Stage 04 Claim Extraction (US-004) (Days 9–10)

- [ ] `src/marrow/schemas/claim.py` — `AtomicClaim`, `ClaimsManifest`
- [ ] `src/marrow/prompts/extract_claims.j2` — Jinja template
- [ ] `src/marrow/stages/stage_04_claims.py` — per-chunk extraction with strict Pydantic validation
- [ ] Semantic dedup at threshold ≥0.92 using the same embedding model as Stage 02
- [ ] Schema-validation retry loop (3 attempts then mark `extraction_failed`)
- [ ] Gold-corpus test: hand-author 30 known claims for one fixture chapter, assert ≥95% recall
- [ ] BDD tests from US-004

**Acceptance:** Fixture book produces ≥8 claims per 1k tokens, dedup rate <20%, schema validation 100%.

### Phase 5 — Stage 05 + 05b Synthesis & Validation (US-005, US-006) (Days 11–14)

This is the trickiest phase. Build it incrementally.

- [ ] `src/marrow/schemas/brief.py` — `BriefDraft`, `BriefSection`, `QuizQuestion`, `QuizResult`
- [ ] `src/marrow/prompts/normalize_dialogue.j2`, `synthesize_chapter.j2`, `synthesize_book.j2`, `quiz_generate.j2`, `examinee_answer.j2`
- [ ] `src/marrow/stages/stage_05_synthesize.py`:
   - Dialogue-to-prose normalization pass
   - Per-chapter merge using claims + community summaries
   - Book-level merge using chapter drafts
   - Inline `[chunk:UUID]` citation requirement enforced via post-synthesis audit
   - Compression target with auto-adjust (consolidation pass if >55 pages, NEVER pad if <45 pages)
- [ ] `src/marrow/stages/stage_05b_validate.py`:
   - Per-chapter quiz generation
   - Examinee receives ONLY the brief (no source)
   - Failed questions map to source chunk UUIDs and trigger targeted regeneration
   - Iteration cap of 3
   - Ungrounded-question secondary check
- [ ] BDD tests from US-005 and US-006

**Acceptance:** Fixture book produces a brief in the target page range, validation loop converges in ≤2 iterations, citation density 100%.

### Phase 6 — Stage 06a Evaluation (US-007) (Days 15–16)

- [ ] `src/marrow/eval/booookscore.py`, `factscore.py`, `hamlet.py` — subprocess wrappers (each evaluator runs in its own venv to avoid dep conflicts)
- [ ] `src/marrow/schemas/brief.py::EvaluationReport`
- [ ] `src/marrow/stages/stage_06a_evaluate.py` — orchestrates the three evaluators and computes the composite score
- [ ] Pass/fail verdict against `MarrowConfig.evaluate.hamlet_leaf_threshold` and friends
- [ ] BDD tests from US-007

**Acceptance:** Fixture book scores produced for all three evaluators. Composite score logged. Below-threshold case correctly emits `verdict: FAIL` and a recommendation.

### Phase 7 — Stage 06b Export (US-008) (Day 17)

- [ ] `src/marrow/stages/stage_06b_export.py`:
   - Write `<slug>_Source.md` with `^uuid` blockid anchors at every paragraph
   - Write `<slug>_Brief.md` with citations rewritten to `[[<slug>_Source#^uuid|↗]]`
   - Write `<slug>_Evaluation.md` (human-readable summary)
   - Round-trip auditor that verifies every citation resolves
   - Logseq mode toggle
- [ ] BDD tests from US-008

**Acceptance:** Round-trip audit passes. Files open cleanly in Obsidian. Determinism: re-export produces byte-identical files.

### Phase 8 — CLI Hardening & Batch (US-009) (Days 18–19)

- [ ] `marrow run --resume` verified by killing the process at every stage boundary
- [ ] `marrow batch` with per-book error isolation
- [ ] `marrow status` rich output
- [ ] Cost cap interactive prompt
- [ ] Ctrl-C handling that finalizes the current chunk and exits cleanly
- [ ] BDD tests from US-009

**Acceptance:** Batch run on 3 fixture books completes with one intentional failure isolated. Resume tested at every stage boundary.

### Phase 9 — Dogfood & Tune (Days 20–21)

- [ ] Run the pipeline on 5 real non-fiction books from the user's library
- [ ] Tune `community_top_k`, `dedup_threshold`, `compression target`, `validate.pass_threshold` based on actual results
- [ ] Verify all 7 success metrics from PRD §2 are met on at least 4 of 5 books
- [ ] Update `CLAUDE.md` Known Issues with anything new discovered
- [ ] Update `CHANGELOG.md` for v1.0

**Acceptance:** 4/5 books pass all PRD success metrics. README ready for v1.0 tag.

### Phase 10 — v1.1 Stretch (Optional, Post-v1.0)

- [ ] Stage 06c — corpus index for cross-book queries
- [ ] `marrow ask` command (US-010)
- [ ] Persian-language support (jina-embeddings-v3 or E5-multilingual)
- [ ] Optional Cohere reranker stage between 03 and 04

---

## INTERACTION PROTOCOL

When working on this project:

- **Start each session by reading `CLAUDE.md` and the current phase in this prompt.** Then announce which user story you are working on.
- **One stage at a time.** Do not jump ahead. If you finish a stage early, write more tests rather than starting the next stage.
- **Surface decisions, do not silently make them.** If you encounter ambiguity, list 2–3 options with tradeoffs and ask. Use `[DECISION NEEDED]` markers in your output.
- **Quote acceptance criteria when claiming a story is done.** Do not claim US-003 is complete until you have shown a passing test for every BDD scenario in US-003 §4.
- **Run `make all` before every commit.** No commits with broken tests, lint failures, or type errors.
- **Cost is a hard constraint.** Watch the cost ledger after every stage during dogfooding. If a stage routinely exceeds its budget, propose a model-routing change, do not silently switch models.

---

## GLOSSARY (so we never get terminology drift)

| Term | Meaning |
|------|---------|
| **Brief** | The final ~50-page output document |
| **Source** | The original 300-page book |
| **Chunk** | A sentence-aligned span of source text with a content-addressed UUID and a late-chunked embedding |
| **Community** | A Leiden-clustered subgraph of related entities, with an LLM-generated summary |
| **Atomic claim** | A single factual assertion extracted from a chunk, with source UUIDs |
| **Working directory** | `runs/<book-slug>/` — all artifacts for one book |
| **Stage** | An isolated step in the pipeline (`stage_NN_*.py`) |
| **Run manifest** | `manifest.json` — top-level summary of one run |
| **Lossless gate** | The HAMLET leaf-recall threshold (default 92%) that gates `verdict: PASS` |
| **Quiz validation loop** | The SummQ-style adversarial coverage check in stage 05b |
| **Coverage audit** | Stage 03's check that every chunk participates in at least one community |
| **Citation density** | Inline `[chunk:UUID]` citations per paragraph in the brief |

---

## SUCCESS DEFINITION FOR v1.0

The build is complete when:

1. **All 9 P0 user stories (US-001 through US-009) have passing BDD tests.**
2. **The pipeline runs end-to-end on 5 real non-fiction books with ≥4 of them hitting all PRD success metrics:**
   - HAMLET leaf-recall ≥ 92%
   - Compression ratio between 5× and 7×
   - 100% citation traceability
   - End-to-end runtime ≤ 90 minutes
   - Cost ≤ $4 per book
   - Pipeline reliability ≥ 95%
3. **Resume works at every stage boundary.**
4. **`make all` is green and coverage is ≥85% on `src/marrow/`.**
5. **README.md, CHANGELOG.md, and a tagged v1.0 release exist.**

---

**Now read `CLAUDE.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/DATABASE.md`, `docs/API.md`, and `docs/REPOS.md` in that order. Then announce which phase you are starting and post your plan for the first stage before writing any code.**
