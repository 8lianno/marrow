# Marrow — Product Requirements Document

**Project:** Marrow
**Version:** 1.0
**Date:** 2026-04-14
**Product Manager:** Ali Naserifar
**Status:** Draft → Ready for Build (Claude Code / Codex)
**Tagline:** Read the marrow.
**Companion docs:** `BRAND.md`, `ARCHITECTURE.md`, `DATABASE.md`, `API.md`, `REPOS.md`, `HOST_MODE.md`, `CLAUDE.md`, `PROMPT.md`

---

# PART I — PRODUCT REQUIREMENTS

## 1. Overview & Purpose (The WHY)

### Problem Statement
Knowledge workers and lifelong learners want the ideas inside 300+ page books but cannot afford the 12–20 hours per book required to read them. Existing summarization tools (Blinkist, NotebookLM Audio Overview, generic LLM "summarize this PDF" prompts) compress aggressively and silently omit load-bearing content — the FABLES benchmark documents that even Claude 3 Opus drops 10% of its claims and over-emphasizes end-of-book material. The dominant failure mode in book-length summarization is **omission**, not hallucination, and the user has no way to detect what was dropped.

A second, equally important problem: the few tools that *do* attempt high-fidelity summarization charge metered API fees on top of the user's existing AI subscriptions. A user who already pays for Claude Code Max is being asked to pay Anthropic twice — once for their coding agent, once for the book pipeline — when the underlying model is the same.

### Product Vision
Marrow is a self-hostable Python toolkit that converts any 300-page non-fiction book into a ~50-page conceptual brief that preserves **every load-bearing idea, framework, definition, claim, example, and counter-argument** from the source — with every sentence in the brief traceable to an exact paragraph in the original book. The brief lives in Obsidian/Logseq as an interconnected knowledge object the user can query, expand, and link into their broader second brain.

Marrow runs by default **inside the user's existing Claude Code or Codex session** (Host Mode), consuming the host agent's reasoning capacity instead of making its own API calls. No API key required. No double-billing. The user pays once, for one subscription, and runs Marrow on every book they read for the next decade.

### Target Audience
- **Primary:** Senior knowledge workers (PMs, researchers, founders, analysts) who read 30+ non-fiction books per year, use Obsidian/Logseq as a thinking environment, and already pay for Claude Code Max or a similar agentic coding subscription.
- **Secondary:** PhD students and academics processing dense literature where conceptual fidelity matters more than entertainment.
- **Explicitly NOT for:** Casual readers wanting a Blinkist-style 8-minute summary, or fiction readers who want narrative experience preservation.

---

## 2. Objectives & Success Metrics

| # | Objective | Key Metric | Target | Timeline |
|---|-----------|-----------|--------|----------|
| O1 | Lossless concept retention | HAMLET leaf-level recall (auto-graded vs. source) | ≥ 92% | v1.0 |
| O2 | Hit the 6× compression target | (Source page count) / (Brief page count) | 6.0× ± 1.0× | v1.0 |
| O3 | Citation traceability | % of brief sentences linked to ≥1 source chunk | 100% | v1.0 |
| O4 | Beat baseline summarizers on faithfulness | FABLES-style faithful-claim rate vs. raw "summarize this PDF" baseline | +15 pp | v1.0 |
| O5 | Time saved per book | (Source read time) − (Brief read time + pipeline runtime) | ≥ 8 hours | v1.0 |
| O6 | Zero metered API cost in default mode | $ spent on API calls during a Host Mode run | $0.00 | v1.0 |
| O7 | Pipeline reliability | % of input books that complete end-to-end without manual intervention | ≥ 95% | v1.0 |
| O8 | Setup friction | Number of secrets/keys required for first successful run in Host Mode | 0 | v1.0 |

---

## 3. Key Features & Functionality (The WHAT)

| # | Feature | Description | User Benefit | Priority |
|---|---------|-------------|--------------|----------|
| **F0** | **Host Mode (default)** | Marrow runs inside Claude Code / Codex and uses the host agent's reasoning capacity via a file-based task/result protocol. No API key required. | Zero metered cost for users who already pay for Claude Code Max. The single most important feature — see `HOST_MODE.md`. | **P0** |
| F1 | High-fidelity ingestion | Docling parses PDF/EPUB into structured `DoclingDocument` JSON with chapter hierarchy, tables, footnotes, and page anchors preserved | Nothing is lost at the parsing layer; structure survives downstream | P0 |
| F2 | Late-chunking embeddings | Full document embedded once via Jina v2/v3, then mean-pooled into chunk vectors with overlapping sliding windows for >8K-token books | Anaphora and long-range references resolve correctly during retrieval | P0 |
| F3 | GraphRAG indexing with omission guards | NanoGraphRAG builds entity/relationship graph + Leiden community summaries with stable content-addressed UUIDs and a coverage audit that ensures every chunk participates in at least one community | Multi-hop conceptual queries become possible; coverage audit prevents silent drops | P0 |
| F4 | Dialogue-to-prose normalization | Pre-synthesis pass rewrites quoted dialogue into dense third-person prose | +30% BookSum score; reduces token waste in synthesis | P1 |
| F5 | Atomic claim extraction | Per-chunk extraction of every factual claim into structured Pydantic objects with source-chunk UUIDs and semantic deduplication | Claims become the unit of compression, not paragraphs — guaranteed coverage | P0 |
| F6 | Hierarchical synthesis | Recursive merge tree over claim sets + community summaries (chunks → sections → chapters → book), NOT incremental running summary | Avoids catastrophic forgetting and recency bias | P0 |
| F7 | Adversarial quiz validation | SummQ-style examinee agent generates leaf-level quiz from source; if draft brief fails, missing facts are reinjected and brief regenerates (≤3 iterations) | Mathematically forces lossless coverage | P0 |
| F8 | Multi-level evaluation harness | BooookScore (coherence) + FActScore (atomic precision) + HAMLET (root/branch/leaf recall) run automatically on every brief | Quantified omission rate, not vibes | P0 |
| F9 | Obsidian/Logseq export with bidirectional citations | Brief written as Markdown with `^uuid` anchors; source written as a parallel file with matching anchors; one-click jump from any brief sentence to its source paragraph | Verification is a click, not a re-read | P0 |
| F10 | CLI orchestrator with resumable checkpoints | Single `marrow run <book.pdf>` command runs the full pipeline with config file + per-stage checkpoints; resumable across host sessions | Reproducible, debuggable, agent-friendly | P0 |
| F11 | Estimated cost telemetry | Per-stage estimate of host token usage in Host Mode; per-stage USD ledger in API Mode | User can see what their reading practice is costing them | P1 |
| F12 | API Mode (fallback) | Original architecture preserved as a fallback for CI/CD, batch processing, and unattended runs where Host Mode is impractical | Power users and automation pipelines retain a path | P1 |
| F13 | Multi-book corpus query | After processing N books, `marrow ask` queries across all briefs for cross-book questions | Personal knowledge graph emerges from reading practice | P2 |

---

## 4. Out of Scope (v1.0)

- Audio book ingestion (no Whisper pipeline)
- Multimodal handling of in-book images, diagrams, marginalia (text-only flattening)
- Fiction / narrative books (Marrow optimizes for non-fiction conceptual content)
- Web UI or hosted SaaS (CLI + Obsidian only)
- Real-time collaborative editing of briefs
- Fine-tuning custom synthesis models
- Languages other than English at v1.0 (Persian and others deferred to v1.1)
- Mobile app
- Anti-DRM / EPUB unlocking (user must supply clean files)
- MCP server interface (deferred to v1.1; file-based protocol ships first)

---

## 5. Non-Functional Requirements

- **Performance:** End-to-end pipeline completes a 300-page book in ≤ 90 minutes in API Mode and ≤ 4 hours of wall-clock time in Host Mode (the difference is the host agent's per-task latency).
- **Reliability:** Resumable from any failed stage via on-disk checkpoint; per-page failures in ingestion must not abort the run; Host Mode runs are resumable across host sessions, machines, and OS reboots.
- **Determinism:** Same input + same config = same chunk UUIDs and same brief structure (modulo LLM stochasticity). All UUIDs are content-addressed.
- **Cost (Host Mode):** $0.00 in metered API fees. Estimated host token usage reported separately.
- **Cost (API Mode):** ≤ $4 per book using Claude Sonnet 4.6 for synthesis and a local model for claim extraction.
- **Privacy:** Fully self-hostable. In Host Mode, Marrow's own subprocess makes no outbound network calls. In API Mode, only the Anthropic API is contacted by default.
- **Observability:** Every task and result logged with stage, host estimate, and chunk UUIDs touched.
- **License hygiene:** No AGPL dependencies in the default stack (rules out MinerU as primary; relegates it to optional fallback).
- **Setup friction:** Host Mode requires zero secrets. API Mode requires only `ANTHROPIC_API_KEY`.

---

## 6. Assumptions & Constraints

**Assumptions**
- User has clean, OCR-able PDFs or EPUBs (no DRM, no scans of handwritten margin notes).
- For Host Mode: user has an active Claude Code Max or Codex subscription with sufficient session capacity.
- For API Mode: user accepts ~$4/book API cost OR has a local 8B–70B model available.
- The locked 6-stage architecture is correct and not revisited in v1.0.
- Claude Sonnet 4.6 (or successor) is the most faithful book-length synthesizer available via Claude Code or API.

**Constraints**
- **Technical:** Jina embeddings v2 capped at 8192 tokens — sliding-window seams must be handled; NanoGraphRAG lacks a "covariates" feature, requiring a custom omission-guard.
- **Host Mode tasks:** Hard cap of 8000 input tokens / 4000 output tokens per task — required for reliability inside host agent context windows.
- **Resource:** Solo build via Claude Code + Codex; no dedicated team.
- **Time:** v1.0 build target = 5 weeks of focused agentic coding.
- **Regulatory:** None (self-hosted, personal use); user is responsible for respecting source-book copyright and not redistributing briefs.

---

## 7. Timeline & Milestones

| Phase | Deliverables | Duration | Target |
|-------|--------------|----------|--------|
| M1 — Foundations | Repo scaffold, config schema, Docling ingest (US-001), source-export (US-008-A) | Week 1 | End W1 |
| M2 — Retrieval Spine | Late chunking (US-002), GraphRAG indexing (US-003) | Week 2 | End W2 |
| M3 — Host Mode Plumbing | Task/result protocol, `marrow next` command, host playbooks v0 (F0) | Week 2.5 | Mid W3 |
| M4 — Synthesis Loop | Claim extraction (US-004), hierarchical merge (US-005), quiz validation (US-006) — both Host and API paths | Week 3–4 | End W4 |
| M5 — Evaluation & Export | BooookScore + FActScore + HAMLET harness (US-007), Obsidian export with citations (US-008), CLI hardening (US-009) | Week 4 | End W4 |
| M6 — Hardening & Dogfood | Cost telemetry, Host Mode resume across sessions, dogfood on 5 reference books, tune K-communities and chunk-overlap, record demo screencast | Week 5 | End W5 |

---

## 8. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|-----------|
| R1 | NanoGraphRAG drops minor characters / niche frameworks via top-K community filter | High | High | US-006 quiz validation runs against full source chunk set; coverage audit creates synthetic `_orphans` community for any chunk not assigned. |
| R2 | Late-chunking seam artifacts at 8192-token boundaries degrade chunk vectors | Medium | High | Sliding window with 25% overlap; deduplicate chunks by content-addressed UUID after pooling. |
| R3 | Host agent drifts from skill instructions over long runs (HOST MODE specific) | High | Medium | Skill includes self-check prompts every 10 tasks; coverage audits run after every stage and abort the run on failure. |
| R4 | Host Mode tasks exceed context window mid-run | High | Medium | Hard 8k input / 4k output cap per task (FR-H05); recursive decomposition into per-section tasks if a chapter is too large. |
| R5 | HAMLET leaf-recall plateaus below 92% target | High | Medium | Increase quiz iteration budget; second-pass "missing claim sweep" that retrieves uncovered chunks by UUID and forces them into the next merge round. |
| R6 | Claude Code session rate-limits mid-run | Medium | Medium | Resumability across sessions; user can wait out the rate limit and continue from `marrow next`. |
| R7 | API Mode cost exceeds $4/book (only matters in API Mode) | Medium | Medium | Per-stage model routing — local Llama for claim extraction, Sonnet for synthesis. |
| R8 | Obsidian block IDs collide across briefs | Low | Low | Use full UUID4, not 6-char hashes. |
| R9 | Scope creep into multimodal / fiction | High | Medium | Out-of-scope list is explicit; defer to v2. |

---

# PART II — USER STORIES

> All user stories use BDD format. Technical Notes, Test Scenarios, and Dependencies sections are intentionally left blank — to be filled by the implementing agent (Claude Code / Codex) and a human reviewer during grooming.

> **Host Mode note:** Every user story below applies to **both** Host Mode and API Mode unless explicitly stated otherwise. Where the two modes diverge, the story calls out the difference. Host Mode is the default.

---

## US-000 — Host Mode (THE MOST IMPORTANT STORY)

### 1) Story Information
- **Title:** Run Marrow Inside Claude Code Without an API Key
- **ID:** US-000
- **Author:** Product Manager — Ali
- **Created Date:** 2026-04-14
- **Priority:** **P0 (the single most important feature)**
- **Sprint:** M3 — Host Mode Plumbing
- **Status:** Ready for Development
- **Linked Epic:** MARROW-HOST
- **Spec:** `HOST_MODE.md`

### 2) User Story
*As a* **Claude Code Max subscriber who already pays Anthropic monthly**
*I want* **to run Marrow inside my existing Claude Code session and have it use my session's reasoning capacity instead of a separate API key**
*So that* **I never pay metered API fees on top of my subscription, never manage a second secret, and can watch the host agent reason through every stage of the pipeline in real time**

**JTBD:** "When I want to compress a book I just downloaded, I want to type two sentences in my open Claude Code session and watch the brief get built using the subscription I'm already paying for, so reading more books never costs me a cent more."

### 3) Business Context
- **Problem / Opportunity:** Forcing users to pay metered API fees on top of an existing $200/month Claude Code Max subscription is hostile to the user's wallet and ideologically wrong. It also adds API-key management friction that scares away contributors and creates a leak vector.
- **Goal:** Marrow runs by default inside Claude Code or Codex with zero API keys, zero metered cost, and full reasoning visibility.
- **Scope (In):** File-based task/result protocol; `marrow next` orchestration command; host playbooks at `skills/claude-code/marrow/SKILL.md` and `skills/codex/marrow/SKILL.md`; resumability across host sessions; mode lock; estimated host-token telemetry.
- **Out of Scope:** MCP server interface (v1.1); enforcing that the host is a paid subscription; integration with billing dashboards.
- **Success Metrics:**
  - $0.00 in metered API fees on a successful Host Mode run
  - Zero environment variables required to start a Host Mode run
  - Skill file ≤ 30k tokens
  - Per-task input ≤ 8000 tokens, output ≤ 4000 tokens
- **Assumptions / Constraints:** User has an active Claude Code or Codex session with capacity for ~150 task turns per book.

### 4) Acceptance Criteria (BDD)

**Scenario: First-Time Host Mode Run with No API Key**
**Given** the user has `marrow` installed and no `ANTHROPIC_API_KEY` set
**And** they are in an active Claude Code or Codex session in a directory containing `book.pdf`
**When** they type "process book.pdf with marrow"
**Then** the host agent reads the appropriate Marrow host playbook
**And** runs `marrow run book.pdf --mode host` (the default)
**And** loops on `marrow next <slug>` to receive task batches
**And** performs reasoning for each task in its own context window
**And** writes result files to the working directory
**And** the pipeline completes without any outbound HTTP calls from Marrow itself
**And** the final brief is written to the configured Obsidian vault

**Scenario: API Key Is Ignored in Host Mode**
**Given** the user has `ANTHROPIC_API_KEY=sk-ant-...` set in their environment
**When** they run a Host Mode pipeline
**Then** the system logs "Host Mode active — API keys ignored. Reasoning will run inside the host agent."
**And** no calls are made to the Anthropic API
**And** the run completes using only the host agent's reasoning

**Scenario: Resume Across Host Sessions**
**Given** a Host Mode run was interrupted at stage 05_synthesize after completing 7 of 12 chapter tasks
**When** the user opens a fresh Claude Code or Codex session two days later in the same directory
**And** says "continue the marrow run for the-book"
**Then** the host agent runs `marrow status`, identifies the in-progress run
**And** runs `marrow next the-book`, receives the remaining 5 chapter tasks
**And** completes them
**And** the pipeline continues to validation, evaluation, and export

**Scenario: Mode Lock Prevents Mode Mixing**
**Given** a run was started in Host Mode with `marrow run book.pdf --mode host`
**When** the user later runs `marrow run book.pdf --mode api --resume`
**Then** the system displays "Mode lock: this run was started in Host Mode and cannot be resumed in API Mode. Use `--force` to restart from scratch."
**And** exits without modifying the working directory

**Scenario: Task Validation Failure Triggers Corrective Loop**
**Given** the host agent writes a result file with malformed JSON
**When** Marrow validates the result against the task's Pydantic schema
**Then** the system writes a corrective task file referencing the original task plus the validation error
**And** the next call to `marrow next` returns the corrective task
**And** the host agent retries with the explicit correction
**And** after 3 failed retries, the chunk is marked as `extraction_failed` and the pipeline continues

**Scenario: Estimated Cost Reporting**
**Given** a Host Mode run has completed
**When** the user inspects the run manifest and token ledger
**Then** the system displays estimated host token usage by stage
**And** clearly labels the values as "estimated"

**Scenario: Detection of Host Environment**
**Given** the user is running inside a Claude Code session
**When** Marrow starts a Host Mode run
**Then** the system detects the host environment via the `CLAUDECODE` environment variable
**And** records `host_environment: claude-code` in the run manifest
**And** loads any host-specific tips from the relevant playbook

**Scenario: Unattended Batch Run Falls Back to API Mode**
**Given** the user wants to process 10 books overnight without sitting in front of Claude Code
**When** they run `marrow batch ./books/ --mode api`
**Then** the system uses the original API Mode pipeline
**And** requires `ANTHROPIC_API_KEY` to be set
**And** completes all 10 books unattended with metered cost reported per book

### 5) Functional Requirements
See `HOST_MODE.md` §5 for the complete FR-H01 through FR-H10 list. Key requirements:
- **FR-H01:** No API key required for Host Mode
- **FR-H02:** Host playbook drives the host agent
- **FR-H03:** File-based task/result protocol with Pydantic schema validation
- **FR-H04:** `marrow next <slug>` is the host agent's loop pivot
- **FR-H05:** Per-task hard cap: 8000 input tokens / 4000 output tokens
- **FR-H06:** Resumability across host sessions and machines
- **FR-H07:** Estimated host-token telemetry
- **FR-H08:** Mode lock prevents mid-run mode switching
- **FR-H09:** Host environment detection via env vars
- **FR-H10:** Graceful degradation to API Mode when explicitly requested

### 6) UX / UI Requirements
- The host playbook must read like a runbook a senior engineer could follow without prior context.
- Task files must be human-readable JSON when opened in a text editor.
- `marrow next` output must be a single JSON object the host agent can parse without ambiguity.
- The CLI banner at the start of every run must say which mode is active.

### 7) Edge Cases
- Host agent rate-limits mid-run → user resumes after waiting
- Host agent refuses a task on safety grounds → log as `failed_task`, continue, report in summary
- Host playbook too large for some host loaders → modular sub-files loaded on demand
- User has both `CLAUDECODE` and `CODEX_SESSION_ID` set → log warning, proceed with first match
- Two host agents try to run `marrow next` on the same slug concurrently → file lock prevents corruption

### 8) Non-Functional Requirements
- ≤ 4 hours wall clock for a 300-page book in Host Mode
- Skill file ≤ 30k tokens
- Zero outbound network calls from Marrow's subprocess in Host Mode
- Byte-identical Obsidian output to API Mode (modulo LLM stochasticity)

### 9) Definition of Done
- All 9 P0 user stories pass in Host Mode on the 5-book reference corpus
- HAMLET leaf-recall ≥ 92% on at least 4 of 5 books, achieved through host agent reasoning alone
- Resume tested by killing the host session at every stage boundary
- Skill file loadable in both Claude Code and Codex CLI without modification
- README documents Host Mode as the default with a 3-command quickstart
- One full end-to-end run recorded as an asciinema and embedded in the README

---

## US-001 — Lossless Book Ingestion

### 1) Story Information
- **Title:** Lossless Book Ingestion via Docling
- **ID:** US-001
- **Priority:** P0
- **Sprint:** M1 — Foundations
- **Status:** Ready for Development
- **Linked Epic:** MARROW-INGEST

### 2) User Story
*As a* **knowledge worker who wants to compress books**
*I want* **to point Marrow at a PDF or EPUB and receive a structured, lossless representation of the entire book**
*So that* **no chapter, footnote, table, or heading is lost before downstream processing begins**

**JTBD:** "When I download a 300-page non-fiction book, I want to ingest it into the pipeline once and trust that nothing was silently dropped, so I never have to re-read the original to check what's missing."

### 3) Business Context
- **Problem:** Naive PDF parsing destroys ~30% of structural metadata (heading hierarchy, footnotes, tables, multi-column layout). Every dropped element becomes an unrecoverable downstream omission.
- **Goal:** A single command transforms a book file into a canonical structured representation that preserves chapter tree, page numbers, tables, formulas, and footnotes.
- **Scope (In):** PDF, EPUB; born-digital and force-OCR modes; chapter/section/paragraph hierarchy; tables; footnotes; page-number metadata.
- **Out of Scope:** Audio books; image content extraction; OCR of handwritten margin notes.
- **Success Metrics:**
  - 100% of chapters from a known reference book detected (verified against published ToC)
  - ≥97% table-cell accuracy on the Docling benchmark suite
  - 300-page book ingested in ≤ 6 minutes on M-series Mac
- **Mode note:** This stage makes no LLM calls. Identical in Host Mode and API Mode.

### 4) Acceptance Criteria (BDD)

**Scenario: Successful PDF Ingestion**
**Given** the user has a clean 300-page non-fiction PDF
**When** the ingestion stage runs
**Then** the system produces a canonical document object containing every chapter, section, paragraph, table, and footnote
**And** every text element carries its parent chapter title and original page number
**And** the run completes in under 6 minutes

**Scenario: EPUB Ingestion**
**Given** the user provides an EPUB with a multi-level table of contents
**When** ingestion runs
**Then** the document object preserves the full ToC tree as nested hierarchy
**And** chapter titles match the EPUB's declared ToC exactly

**Scenario: Scanned PDF with Force OCR**
**Given** a scanned PDF with no embedded text layer
**When** the user enables force-OCR mode
**Then** the system runs OCR over every page
**And** returns a document with a warning that tables and formulas may have reduced accuracy

**Scenario: Per-Page Failure Resilience**
**Given** a book where page 147 has corrupted layout data
**When** ingestion runs
**Then** the system isolates the failure to page 147
**And** continues processing pages 148–300
**And** emits a structured warning listing the skipped page

**Scenario: Unsupported Format**
**Given** the user provides a `.mobi` file
**When** ingestion runs
**Then** the system displays "Format not supported in v1.0. Convert to EPUB or PDF and retry."
**And** exits without creating a partial output

### 5) Functional Requirements
- **FR-01:** Accept `.pdf` and `.epub` files up to 500 MB
- **FR-02:** Support `auto`, `force_ocr`, and `text_only` modes
- **FR-03:** Output preserves chapter → section → subsection → paragraph hierarchy
- **FR-04:** Tables extracted as structured cell grids
- **FR-05:** Every text node carries its source page number
- **FR-06:** Per-page failures isolated and logged; the run does not abort
- **FR-07:** Re-ingesting the same file produces a byte-identical canonical document

### 6) UX / UI Requirements
- CLI progress bar by page count
- Per-stage timing on completion
- Output to `runs/<book-slug>/01_ingest/`

### 7) Edge Cases
- Non-standard ToC ordering • multi-column academic layouts • footnotes spanning page breaks • mixed scanned + born-digital pages

### 8) Non-Functional Requirements
- ≤ 6 min for 300 pages on M-series Mac
- 0% full-run failures from single-page corruption
- Byte-identical re-runs

### 9) Definition of Done
- 5 reference books ingest with 100% chapter detection
- Per-page failure isolation verified by injection test
- Schema documented

*Sections 10–12 left blank for tech and QA leads.*

---

## US-002 — Context-Preserving Late Chunking

### 1) Story Information
- **ID:** US-002 | **Priority:** P0 | **Sprint:** M2 | **Epic:** MARROW-RETRIEVAL

### 2) User Story
*As a* **system that must answer multi-hop conceptual queries about a book**
*I want* **chunk embeddings that encode the entire document's context, not just the local paragraph**
*So that* **a chunk containing "she argued the opposite" still resolves to the correct prior speaker 200 pages earlier**

### 3) Business Context
- **Problem:** Naive chunking severs anaphoric references and produces context-blind embeddings.
- **Goal:** Every chunk vector mathematically contains evidence of the full document.
- **Scope (In):** Sliding-window late chunking; sentence-boundary pooling; stable chunk UUIDs.
- **Mode note:** This stage uses local embedding models only. No LLM calls in either mode.
- **Success Metrics:**
  - +2 pp nDCG@10 vs. naive recursive chunking
  - Chunk UUIDs stable across re-runs

### 4) Acceptance Criteria (BDD)

**Scenario: Document Under 8192 Tokens**
**Given** a 5000-token chapter
**When** chunking runs
**Then** the system embeds the full chapter in a single forward pass
**And** produces sentence-aligned chunk vectors carrying the chapter's global context

**Scenario: Document Exceeds 8192 Tokens**
**Given** a 100,000-token book
**When** chunking runs
**Then** the system applies a sliding window of 8192 tokens with 25% overlap
**And** deduplicates overlapping chunks by stable UUID
**And** produces a chunk vector for every sentence

**Scenario: Stable Chunk Identity**
**Given** the same source document is chunked twice
**When** both runs complete
**Then** every chunk has identical UUIDs across runs
**And** identical embedding vectors within floating-point tolerance

**Scenario: Anaphora Resolution Test**
**Given** a chunk containing "He rejected this entirely" with the antecedent 8000 tokens earlier
**When** the user retrieves chunks similar to the antecedent's full sentence
**Then** the late-chunked vector ranks in the top-10 results
**And** a baseline naive-chunked vector does not

**Scenario: Embedding Service Unavailable**
**Given** the embedding model fails to load
**When** chunking runs
**Then** the system displays "Embedding model unavailable. Check config." and exits non-zero

### 5) Functional Requirements
- **FR-01:** Sliding window 8192 / 25% overlap, configurable
- **FR-02:** Sentence-aligned boundaries via NLTK
- **FR-03:** Chunk UUIDs derived from `MD5(chunk_text + book_slug + chapter_path)`
- **FR-04:** Each chunk inherits chapter chain from US-001
- **FR-05:** LanceDB persistence + `chunks.jsonl` mirror

### 6–9) UX / Edge Cases / NFRs / DoD
- CLI progress bar by chunk count
- Edge cases: ultra-long single sentences, very short chapters, mixed-language books
- ≤ 8 min for 100K-token book on M-series Mac
- Memory ≤ 8 GB peak
- Determinism test passes

---

## US-003 — GraphRAG Indexing with Omission Guards

### 1) Story Information
- **ID:** US-003 | **Priority:** P0 | **Sprint:** M2 | **Epic:** MARROW-RETRIEVAL

### 2) User Story
*As a* **synthesis engine that must answer "what is this book really about"**
*I want* **a knowledge graph of every entity, relationship, and community in the book, plus pre-computed community summaries**
*So that* **multi-hop conceptual queries return cohesive subgraphs instead of disconnected paragraphs**

### 3) Business Context
- **Problem:** Vector search alone fails on conceptual queries that require traversing entity relationships. NanoGraphRAG provides this graph cheaply but risks dropping peripheral entities via top-K community filtering.
- **Goal:** A complete entity-relationship graph with community summaries plus an explicit "no chunk left behind" coverage guarantee.
- **Mode note:** **In Host Mode**, entity extraction, relation extraction, and community summarization are decomposed into per-chunk and per-community tasks emitted to the host agent. **In API Mode**, NanoGraphRAG's native LLM calls are used.

### 4) Acceptance Criteria (BDD)

**Scenario: Successful Graph Build (mode-agnostic)**
**Given** a chunked book from US-002
**When** the graph stage runs
**Then** the system extracts entities and relationships from every chunk
**And** clusters them into communities via Leiden
**And** generates a natural-language summary for every community
**And** persists the graph and summaries to the working directory

**Scenario: Host Mode — Per-Chunk Entity Tasks**
**Given** Host Mode is active and a chunked book is ready
**When** stage 03 starts
**Then** Marrow writes one entity-extraction task per chunk to `03_graph/tasks/`
**And** `marrow next` returns task batches to the host agent
**And** the host agent's results are validated against the entity schema
**And** the graph is built from the host-provided entities

**Scenario: Coverage Audit Passes**
**Given** the graph build is complete
**When** the coverage audit runs
**Then** every source chunk UUID appears in at least one community
**And** the audit reports "Coverage: 100%"

**Scenario: Coverage Audit Fails — Orphan Chunks**
**Given** the audit detects 12 orphan chunks
**When** detection completes
**Then** the system creates a synthetic `_orphans` community containing those chunks
**And** generates a summary for it (via host agent in Host Mode)
**And** logs a warning naming the orphaned chunk UUIDs

**Scenario: Token Budget Exceeded (API Mode only)**
**Given** API Mode is active and graph construction exceeds the token budget
**When** the limit is hit
**Then** the system pauses and prompts the user to continue
**And** can resume from checkpoint without re-processing completed chunks

### 5–9) FRs / UX / Edge Cases / NFRs / DoD
- LLM-extracted entities and relations per chunk
- Leiden clustering with configurable resolution
- Community summaries preserve named entities verbatim
- 100% chunk coverage enforced by post-build audit
- ≤ 15 min for 300-page book in API Mode
- Coverage audit verified at 100% on 5-book corpus

---

## US-004 — Atomic Claim Extraction

### 1) Story Information
- **ID:** US-004 | **Priority:** P0 | **Sprint:** M4 | **Epic:** MARROW-SYNTHESIS

### 2) User Story
*As a* **brief generator that must not skip any load-bearing fact**
*I want* **an exhaustive list of atomic factual claims extracted from every chunk before any prose is written**
*So that* **synthesis becomes a coverage problem over claims, not a creative writing problem over paragraphs**

### 3) Business Context
- **Problem:** LLMs asked to "summarize this chunk" naturally over-abstract and drop dense empirical content.
- **Goal:** A typed, deduplicated, source-anchored claim set covering every chunk in the book.
- **Mode note:** **Host Mode** decomposes into per-chunk claim-extraction tasks. **API Mode** uses local Llama 3.1 8B via vLLM.

### 4) Acceptance Criteria (BDD)

**Scenario: Successful Claim Extraction**
**Given** a chunk of source text
**When** extraction runs
**Then** the system returns atomic claims as structured objects
**And** each claim has fields: `claim_text`, `source_chunk_uuid`, `claim_type`, `confidence`
**And** every claim is grounded in a span of the source chunk

**Scenario: Deduplication Across Chunks**
**Given** two chunks both contain "Compound interest grows exponentially"
**When** extraction completes across both
**Then** the resulting claim set contains one canonical claim referencing both source chunk UUIDs

**Scenario: Refusal on Pure Narrative**
**Given** a chunk containing only a personal anecdote
**When** extraction runs
**Then** the system returns an empty claim list
**And** logs the chunk as "narrative-only"

**Scenario: Schema Validation Failure**
**Given** the LLM (or host agent) returns malformed JSON
**When** the parser fails validation
**Then** the system retries with a stricter prompt up to 3 times
**And** if still failing, marks the chunk as `extraction_failed` and continues

### 5–9)
- Strict Pydantic schema with required `source_chunk_uuid`
- Semantic deduplication via embedding similarity ≥ 0.92
- ≥ 95% gold-corpus key-claim recall on 3-book test set
- ≥ 8 claims per 1000 source tokens average

---

## US-005 — Hierarchical Synthesis

### 1) Story Information
- **ID:** US-005 | **Priority:** P0 | **Sprint:** M4 | **Epic:** MARROW-SYNTHESIS

### 2) User Story
*As a* **user who wants a 50-page brief that reads as a coherent document, not a list of bullet points**
*I want* **claims and community summaries merged hierarchically into chapter-level then book-level prose**
*So that* **the resulting brief preserves both granular facts and the overarching argumentative arc**

### 3) Business Context
- **Problem:** Incremental running summaries forget early content. Hierarchical merging maintains higher coherence (25.66 vs 21.90 ROUGE-1).
- **Goal:** A draft brief organized by source chapter with claim coverage and narrative flow.
- **Mode note:** **Host Mode** issues per-chapter synthesis tasks plus a final book-merge task. **API Mode** uses Claude Sonnet 4.6 directly. Per-chapter task size cap is enforced in both modes.

### 4) Acceptance Criteria (BDD)

**Scenario: Successful Hierarchical Synthesis**
**Given** the claim set from US-004 and community summaries from US-003
**When** synthesis runs
**Then** the system normalizes dialogue-heavy chunks into third-person prose
**And** generates a per-chapter draft synthesis from the chapter's chunks and claims
**And** generates a book-level draft from the chapter drafts
**And** every paragraph contains at least one inline `[chunk:UUID]` citation

**Scenario: Compression Target Met**
**Given** a 300-page source book
**When** synthesis completes
**Then** the brief is between 45 and 55 pages
**And** the system reports the actual compression ratio

**Scenario: Compression Overshoot**
**Given** the first-pass brief is 78 pages
**When** the system detects overshoot
**Then** it triggers a consolidation pass that merges redundant claims
**And** if still over target, surfaces "Brief exceeds target by N pages — review claim density" without truncating content

**Scenario: Compression Undershoot**
**Given** the first-pass brief is 22 pages
**When** undershoot is detected
**Then** the system surfaces "Brief is below target — likely indicates omission. Quiz validation will check." and proceeds to US-006
**And** does NOT pad with filler

**Scenario: Citation Coverage Failure**
**Given** a draft paragraph contains no inline citation
**When** the citation audit runs
**Then** the system flags that paragraph
**And** re-runs synthesis for the affected section with a stricter citation prompt

### 5–9)
- Dialogue normalization preprocessing
- Recursive merge: chunks → sections → chapters → book
- Inline `[chunk:UUID]` citation requirement enforced post-synthesis
- Compression target with auto-adjust
- Compression hit on ≥ 4/5 reference books on first pass

---

## US-006 — Adversarial Quiz Validation

### 1) Story Information
- **ID:** US-006 | **Priority:** P0 | **Sprint:** M4 | **Epic:** MARROW-SYNTHESIS

### 2) User Story
*As a* **user who needs proof that nothing important was dropped**
*I want* **the system to generate leaf-level quiz questions from the source and verify the brief can answer them**
*So that* **any failed question triggers automatic regeneration of the affected brief section**

### 3) Business Context
- **Problem:** Even after hierarchical synthesis, leaf-level facts (specific dates, names, numbers) are systematically dropped.
- **Goal:** A pass/fail coverage gate that retries until ≥ 90% of leaf-level quiz questions are answerable from the brief alone.
- **Mode note:** Both quiz generation and examinee answering are decomposed into per-chapter tasks in Host Mode.

### 4) Acceptance Criteria (BDD)

**Scenario: Successful First-Pass Validation**
**Given** a draft brief from US-005 and the source chunks
**When** validation runs
**Then** the quiz agent generates 5 leaf-level questions per chapter
**And** the examinee attempts to answer using only the brief
**And** if ≥ 90% pass, the brief is validated and the loop exits

**Scenario: Validation Failure Triggers Targeted Regeneration**
**Given** the examinee fails 7 of 30 quiz questions
**When** failure is detected
**Then** the system identifies which source chunks contain the missing facts
**And** injects those chunks into a regeneration task for the affected sections
**And** regenerates only those sections, not the full brief
**And** re-runs validation

**Scenario: Iteration Cap Reached**
**Given** the validation loop has run 3 iterations without reaching 90%
**When** the cap is hit
**Then** the system exits the loop
**And** writes the best-scoring brief to disk
**And** emits a structured warning listing the still-failing quiz questions

**Scenario: Ungrounded Question Detection**
**Given** the quiz generator produces a question whose answer is not in the source
**When** the examinee fails it
**Then** the validation harness flags the question as "ungrounded" via a secondary check
**And** excludes it from the pass-rate calculation

### 5–9)
- Examinee receives ONLY the brief, never the source
- Failed questions map to source chunk UUIDs for targeted regeneration
- Default 3 iterations; configurable
- ≥ 90% pass rate verified on 5 reference books

---

## US-007 — Multi-Level Evaluation Harness

### 1) Story Information
- **ID:** US-007 | **Priority:** P0 | **Sprint:** M5 | **Epic:** MARROW-EVAL

### 2) User Story
*As a* **user who refuses to trust vibes**
*I want* **every brief automatically scored on coherence, atomic factual precision, and root/branch/leaf recall**
*So that* **I have hard numbers to decide whether to accept the brief or rerun with different settings**

### 3) Business Context
- **Goal:** A single evaluation report with three independent quality scores plus a composite verdict.
- **Mode note:** Per-claim verdict tasks are issued to the host agent in Host Mode; this is the most expensive Host Mode stage and may be the place where users choose API Mode for unattended runs.

### 4) Acceptance Criteria (BDD)

**Scenario: Successful Evaluation Run**
**Given** a validated brief and the source chunks
**When** evaluation runs
**Then** the system computes BooookScore coherence, FActScore precision, and HAMLET root/branch/leaf recall
**And** writes a structured evaluation report
**And** displays a summary table with all three scores plus a pass/fail verdict

**Scenario: Below-Threshold Brief**
**Given** the brief scores below the configured HAMLET leaf threshold of 92%
**When** evaluation completes
**Then** the system marks the brief as "FAILED"
**And** emits a recommendation: "Increase quiz iterations or reduce compression ratio"
**And** still writes the brief and report for inspection

**Scenario: FActScore Length Penalty**
**Given** the brief is artificially short and abstract
**When** FActScore runs with γ = 10
**Then** the precision score is mathematically penalized
**And** the report flags "Brief may be too compressed"

### 5–9)
- BooookScore with batch size 10
- FActScore via SpaCy with γ = 10, source as knowledge corpus
- HAMLET root/branch/leaf decomposition
- Composite weighted score
- All three evaluators run end-to-end on 5 reference books

---

## US-008 — Obsidian / Logseq Export with Citation Traceability

### 1) Story Information
- **ID:** US-008 | **Priority:** P0 | **Sprint:** M5 | **Epic:** MARROW-EXPORT

### 2) User Story
*As a* **knowledge worker who lives in Obsidian**
*I want* **the brief and the source written to my vault as two linked Markdown files where every brief sentence jumps to the exact source paragraph**
*So that* **I can read the brief and one-click verify any claim in seconds**

### 3) Business Context
- **Goal:** Two Markdown files per book — `<book>_Brief.md` and `<book>_Source.md` — with bidirectional `[[Source#^uuid]]` links.
- **Mode note:** Deterministic. No LLM calls in either mode.

### 4) Acceptance Criteria (BDD)

**Scenario: Successful Export**
**Given** a validated brief and the canonical document
**When** export runs
**Then** the system writes `<book>_Source.md` with `^uuid` blockid anchors at every paragraph
**And** writes `<book>_Brief.md` where every inline `[chunk:UUID]` is rewritten as `[[<book>_Source#^uuid|↗]]`
**And** both files open cleanly in Obsidian

**Scenario: Block ID Collision Prevention**
**Given** two paragraphs produce the same UUID hash collision (vanishingly rare)
**When** export runs
**Then** the system detects the collision
**And** appends a disambiguation suffix
**And** updates all citations referencing that UUID

**Scenario: Logseq Export Mode**
**Given** the user has configured `export_format: logseq`
**When** export runs
**Then** the system writes Logseq-compatible files using `id::` block properties
**And** rewrites citations as `(((uuid)))` references

**Scenario: Citation Round-Trip Verification**
**Given** export is complete
**When** the round-trip auditor runs
**Then** every citation in the brief resolves to a real anchor in the source
**And** the auditor reports "100% citation integrity"

### 5–9)
- Default Obsidian; Logseq mode toggle
- Round-trip audit before declaring success
- ≤ 30 seconds export step
- Byte-identical re-runs

---

## US-009 — CLI Orchestrator with Resumable Checkpoints

### 1) Story Information
- **ID:** US-009 | **Priority:** P0 | **Sprint:** M5 | **Epic:** MARROW-INFRA

### 2) User Story
*As a* **user who wants to process books across sessions**
*I want* **a single command that runs all stages in either mode and resumes from any failed stage**
*So that* **I can queue 10 books overnight (API Mode) or pick up a Host Mode run two days later in a fresh Claude Code session**

### 3) Business Context
- **Goal:** Reproducible, resumable, observable orchestrator with per-book working directories and clear stage boundaries.
- **Mode note:** Resume logic is identical across modes. Mode is locked per-run.

### 4) Acceptance Criteria (BDD)

**Scenario: Single-Command Full Run**
**Given** the user has a book file and a valid config
**When** they run `marrow run book.pdf`
**Then** the system executes all stages in order in the active mode
**And** writes a final summary with brief path, source path, evaluation scores, and runtime

**Scenario: Resume After Failure**
**Given** a previous run failed at synthesize
**When** the user runs `marrow run book.pdf --resume`
**Then** the system detects the existing working directory
**And** skips completed stages
**And** restarts from synthesize using persisted artifacts

**Scenario: Force Restart**
**Given** an existing working directory
**When** the user runs `marrow run book.pdf --force`
**Then** the system deletes the existing directory and runs from scratch

**Scenario: Batch Run (API Mode)**
**Given** 10 books in a directory
**When** the user runs `marrow batch ./books/ --mode api`
**Then** the system processes each book sequentially
**And** continues if any single book fails
**And** writes a final batch report

**Scenario: Configuration Error**
**Given** the user's config file is malformed
**When** they run any command
**Then** the system displays a clear validation error pointing to the offending field
**And** exits with code 2 without creating any output

### 5–9)
- Typer-based CLI with `--mode host|api`, `--resume`, `--force`, `--stage`
- Per-stage checkpoints via `_complete` sentinel
- Batch mode with per-book error isolation
- Cost telemetry by stage
- Exit codes: 0=success, 1=pipeline failure, 2=config error, 3=budget exceeded, 4=input error
- 95% unattended completion rate

---

## US-010 — Cross-Book Corpus Query (v1.1 Stretch)

### 1) Story Information
- **ID:** US-010 | **Priority:** P1 | **Sprint:** Post-M6 (v1.1) | **Epic:** MARROW-CONSUMPTION

### 2) User Story
*As a* **user who has processed 30 books**
*I want* **to ask cross-book questions via my Claude Code session and get answers with citations across my entire personal corpus**
*So that* **my reading turns into a queryable second brain**

### 3) Business Context
- **Goal:** `marrow ask "<question>"` queries every processed book's graph index and synthesizes an answer with citations.
- **Mode note:** Host Mode by default; the host agent does the synthesis. Marrow handles retrieval.

### 4) Acceptance Criteria (BDD)

**Scenario: Cross-Book Query**
**Given** 5 processed books in the corpus
**When** the user runs `marrow ask "What do these books say about compound interest?"`
**Then** the system queries every book's graph index
**And** returns retrieved chunks to the host agent
**And** the host agent synthesizes an answer with citations to specific brief sections and source paragraphs

**Scenario: No Relevant Content**
**Given** an unrelated question
**When** retrieval returns nothing
**Then** the system replies "No relevant content found" and lists searched books

**Scenario: Single-Book Scoping**
**Given** the user wants to query only one book
**When** they run `marrow ask "..." --book "thinking-fast-and-slow"`
**Then** retrieval restricts to that book's index

### 5–9)
- Cross-book retrieval over local graph stores
- Mandatory inline citations
- ≤ 10 seconds median query latency
- Citation accuracy ≥ 90%

---

# PART III — APPENDIX: STAGE-TO-STORY MAPPING

| Pipeline Stage | Primary Story | Mode Difference | Repos / Tools |
|---|---|---|---|
| 0 — Host Mode plumbing | **US-000** | Host only | Custom skill + task/result protocol |
| 1 — Ingestion | US-001 | Identical | Docling (primary), MinerU (opt-in extra), Marker (fallback) |
| 2 — Late Chunking | US-002 | Identical | Jina v2, NLTK, custom late-chunking |
| 3 — GraphRAG | US-003 | Host Mode decomposes per-chunk | NanoGraphRAG, NetworkX, Leiden |
| 4 — Claims | US-004 | Host Mode decomposes per-chunk | SciClaims pattern, Pydantic |
| 5 — Synthesis | US-005 | Host Mode decomposes per-chapter | Claude Sonnet (API) / host agent (Host) |
| 5b — Validation | US-006 | Both stages decomposed in Host Mode | SummQ adversarial loop |
| 6a — Evaluation | US-007 | Per-claim verdicts in Host Mode | BooookScore, FActScore, HAMLET |
| 6b — Export | US-008 | Identical | Custom Obsidian/Logseq writer |
| Cross-cutting | US-009 | Mode lock | Typer, Rich, structlog |
| v1.1 corpus query | US-010 | Host Mode default | Custom retrieval over per-book graphs |

---

# PART IV — STAKEHOLDER SIGN-OFF

| Role | Name | Date | Approval |
|------|------|------|----------|
| Product (you) | Ali | 2026-04-14 | ☐ |
| Engineering (Claude Code agent) | — | — | ☐ |
| QA (manual on reference corpus) | Ali | — | ☐ |

---

**End of PRD.md.** This PRD plus the 8 companion documents (`BRAND.md`, `HOST_MODE.md`, `ARCHITECTURE.md`, `DATABASE.md`, `API.md`, `REPOS.md`, `CLAUDE.md`, `PROMPT.md`) is the complete v1.0 specification for **Marrow**. Hand all 9 files to Claude Code or Codex as the project's source-of-truth specification. Each user story is independently pickable in a fresh agentic session.

**Read the marrow.**
