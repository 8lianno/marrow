# Marrow — Host Mode

**Feature ID:** F-HOST | **Priority:** P0 | **Status:** Spec | **Date:** 2026-04-14
**Owner:** Ali | **Companion to:** `PRD.md`, `ARCHITECTURE.md`, `API.md`

> **The single most important architectural decision in v1.0.** This document specifies how Marrow runs *inside* an agentic coding session (Claude Code, Codex, Cursor, Aider) and uses the host agent's reasoning capacity — not its own API key — for every LLM-heavy stage. This is what makes Marrow free at the margin for users who already pay for Claude Code Max or a Codex subscription.

---

## 1. The Problem with API Mode

The default pipeline in `PRD.md` assumes Marrow holds an Anthropic API key and calls Claude Sonnet 4.6 directly for synthesis, validation, and evaluation. That model has three failure modes for the target user:

1. **Double-billing.** A user with a Claude Code Max subscription is already paying Anthropic for a flat-rate amount of intelligence per month. Asking them to *additionally* pay metered API fees to summarize a book — when the underlying model is the same — is hostile to the user's wallet and ideologically wrong.
2. **Key management friction.** Local-first power users hate stuffing API keys into env files. Every `ANTHROPIC_API_KEY=sk-...` is a leak vector and a setup step that scares away contributors.
3. **Lost agency.** When Marrow makes its own LLM calls, the user can't intervene, redirect, or steer the synthesis. The reasoning happens behind a curtain. In Host Mode, the user *watches* the host agent reason and can intercept at any stage.

The fix is to **invert the orchestration**: instead of Marrow being the boss that calls the LLM as a service, **the host agent (Claude Code or Codex) becomes the boss that calls Marrow as a toolkit**. The host agent does the reasoning. Marrow handles the deterministic plumbing — parsing, chunking, embedding, storage, evaluation, export.

This pattern is established. Coding agents like Serena run inside Claude Code and consume the host's tokens. MCP servers like basic-memory expose only deterministic tools and let the host do the thinking. Marrow's Host Mode follows the same playbook: **Marrow is a toolkit that an agent drives, not a black box that calls an LLM behind the user's back**.

---

## 2. Goal

Make it possible for a user with a Claude Code Max subscription (or a Codex subscription) to process a book end-to-end **without ever providing an API key, paying a metered fee, or losing visibility into the reasoning steps** — while preserving every guarantee of the original pipeline (lossless leaf-recall, citation traceability, resumability).

---

## 3. Two Modes, One Pipeline

Marrow ships with two execution modes that share the same stages, schemas, working directory layout, and final output. They differ only in **who does the reasoning**.

| Aspect | API Mode (default until v1.0, secondary after) | **Host Mode (primary from v1.0)** |
|---|---|---|
| Who runs the LLM | Marrow itself, via Anthropic SDK | The host agent (Claude Code, Codex, etc.) |
| Whose tokens are spent | The user's API key | The user's Claude Code / Codex session |
| Who pays | Metered API billing | The flat-rate subscription |
| API key needed | Yes (`ANTHROPIC_API_KEY`) | **No** |
| Who orchestrates | Marrow's `orchestrator.py` | The host agent, following a skill / playbook |
| Where reasoning happens | Inside `marrow run` | Inside the host agent's chat loop |
| User visibility into reasoning | None — black box | Full — the user reads the agent's thinking in real time |
| Best for | CI, batch processing, unattended runs | Day-to-day reading, interactive iteration |

Both modes produce **byte-identical** Obsidian outputs given the same source book (modulo LLM stochasticity). A user can switch between them per-run via `marrow run --mode host` or `--mode api`. Host Mode is the default.

---

## 4. Architectural Inversion

### 4.1 What changes

In API Mode, the call graph is:

```
user → marrow run → orchestrator → stage → marrow.llm.call() → Anthropic API
                                                              └─ pays $$
```

In Host Mode, the call graph is:

```
user → host agent (Claude Code) → reads marrow-skill/SKILL.md
                                ↓
                                follows playbook step-by-step
                                ↓
                                runs `marrow <subcommand>` for deterministic work
                                                                ↓
                                                                writes JSONL artifacts
                                ↓
                                does the LLM reasoning IN ITS OWN HEAD
                                ↓
                                writes the result to disk via `marrow <subcommand> ingest-result`
                                ↓
                                proceeds to the next playbook step
```

The host agent is the orchestrator. Marrow is a stateless toolkit. There is no `marrow.llm` module call in Host Mode — that file simply isn't reached.

### 4.2 What stays the same

- The 6-stage pipeline (ingest → chunk → graph → claims → synthesize → validate → evaluate → export)
- Every Pydantic schema in `DATABASE.md`
- The working directory layout
- Content-addressed UUIDs and determinism guarantees
- The lossless gate, the coverage audits, the citation traceability requirement
- BooookScore / FActScore / HAMLET evaluation
- Obsidian / Logseq export with `^uuid` block anchors

The contract between stages is identical. Host Mode changes only **who pulls the LLM lever**, not what the lever does.

---

## 5. Functional Requirements

### FR-H01 — No API Key Required for Host Mode

When `marrow run --mode host` is invoked, Marrow MUST NOT read `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or any other LLM provider credential. Stages that previously called `marrow.llm.call()` MUST instead emit a structured **task file** to disk and exit cleanly, leaving the host agent to pick up the task and submit a result.

If the user accidentally sets an API key, Marrow MUST ignore it in Host Mode and log: `"Host Mode active — API keys ignored. Reasoning will run inside the host agent."`

### FR-H02 — The Skill File Drives the Host Agent

Marrow ships a skill file at `marrow-skill/SKILL.md` that the host agent reads on every invocation. The skill is the playbook. It tells the host agent:

- Which `marrow` subcommands to run, in what order
- How to read each stage's task files
- How to format each stage's result file
- When to ask the user for confirmation
- How to handle failures and resume

The skill MUST be self-contained — a fresh Claude Code session with no prior context must be able to follow it. The skill is versioned alongside Marrow itself.

### FR-H03 — Task / Result Protocol

Every stage that previously made an LLM call now follows this protocol:

1. **Marrow writes a task file** to `runs/<slug>/<stage>/tasks/<task_id>.task.json` containing:
   - Task type (e.g., `extract_claims`, `synthesize_chapter`, `quiz_generate`)
   - Input artifacts (chunk text, claims, etc.)
   - Required output schema (a Pydantic JSON schema)
   - Source UUIDs for cost attribution and citation tracking
   - A natural-language instruction block for the host agent

2. **The host agent reads the task file**, performs the reasoning in its own context window, and writes a result file to `runs/<slug>/<stage>/results/<task_id>.result.json`.

3. **Marrow validates the result** against the task's declared schema. If validation fails, Marrow writes a follow-up task with explicit corrections and the host agent retries.

4. **Marrow advances** when all tasks for the stage are completed and validated.

The protocol is fully file-based. No sockets, no IPC, no hidden state. A user can open the task file in a text editor mid-run and see exactly what the host agent is being asked to do.

### FR-H04 — `marrow next` Subcommand

Host Mode introduces a single new top-level command:

```bash
marrow next <book-slug>
```

The host agent calls this whenever it needs to know what to do next. Marrow returns a JSON object describing the current state:

```json
{
  "stage": "04_claims",
  "status": "awaiting_host",
  "pending_tasks": ["claim_001.task.json", "claim_002.task.json"],
  "completed_tasks": 47,
  "total_tasks": 50,
  "next_action": "Read the pending task files, perform claim extraction, write results to runs/the-book/04_claims/results/",
  "skill_section": "§4.2 — Claim Extraction"
}
```

This command is the *only* coupling between the host agent and Marrow's internal state. Everything else flows through files.

### FR-H05 — Streaming-Friendly Task Granularity

Tasks MUST be small enough that a single host agent reasoning turn can complete one task. Hard limit: **no task may include more than 8000 input tokens or require more than 4000 output tokens**. Stages that previously processed an entire book in one LLM call (synthesize, validate) MUST decompose into per-chapter or per-section tasks.

This isn't just polite — it's required for Host Mode to work at all. A Claude Code session won't reliably hold a 100k-token book + 50k-token brief + a synthesis prompt in one reasoning turn. Decomposition into chapter-sized tasks is mandatory.

### FR-H06 — Resumability Across Host Sessions

A Marrow run started in one Claude Code session MUST be resumable in another. The user can close their laptop mid-pipeline, reopen Claude Code two days later, navigate to the same `marrow-runs/` directory, say "continue the marrow run for `the-book`," and the host agent reads `marrow next the-book` and picks up where it left off. No state lives in the host agent's context window — all state is on disk.

### FR-H07 — Host Agent Cost Telemetry (Best Effort)

Marrow cannot directly observe how many tokens a host agent burned on a task — that information lives in the agent's billing system, not Marrow's. But Marrow MUST estimate it: for every completed task, Marrow records the input size, the output size, and the model identifier the host agent reports (if any), and aggregates these into a per-run estimate.

The estimate is labeled **estimated** in all CLI output. Real cost lives in the user's Claude Code billing dashboard, which Marrow does not access.

### FR-H08 — Mode Lock

Once a run is started in Host Mode, it MUST be completed in Host Mode (or restarted from scratch with `--force`). Mixing modes mid-run is forbidden because cost attribution, telemetry, and prompt formatting differ between the two paths. The orchestrator records the mode in `manifest.json::mode` and refuses to resume a Host Mode run with `--mode api` or vice versa.

### FR-H09 — Host Agent Detection

Marrow MUST detect which host environment it's running inside, when possible:

| Host | Detection signal |
|---|---|
| Claude Code | `CLAUDECODE` env var, presence of `~/.claude/` |
| Codex CLI | `CODEX_SESSION_ID` env var |
| Cursor | `CURSOR_AGENT_ID` env var |
| Aider | `AIDER_*` env vars |
| Unknown agent | None — assume generic |

Detection results are recorded in the run manifest. They influence the skill file's "host-specific tips" section but never gate functionality.

### FR-H10 — Graceful Degradation to API Mode

If the user explicitly sets `--mode api` and provides an API key, Marrow MUST run the original API Mode pipeline unchanged. Host Mode is the default; API Mode is a first-class fallback. Users running CI/CD or unattended batch processing will continue to use API Mode. The two paths share 100% of the deterministic stages and diverge only at the LLM-call points.

---

## 6. Stage-by-Stage: What Changes

Stages 01 (ingest), 02 (chunk), 03b (coverage audit), and 06b (export) are **deterministic** — they make no LLM calls in either mode. They run unchanged in Host Mode.

The remaining stages decompose as follows:

### Stage 03 — GraphRAG Indexing

**API Mode:** NanoGraphRAG calls Claude/Llama for entity extraction and community summarization.

**Host Mode:**
1. Marrow chunks the book and writes `chunks.jsonl` (deterministic, no LLM).
2. Marrow generates one task file per chunk: `tasks/entity_<chunk_uuid>.task.json` containing the chunk text and the entity-relation schema.
3. The host agent reads each task file, extracts entities and relations using its own reasoning, writes results.
4. Marrow validates results, runs Leiden clustering on the resulting graph (deterministic), and emits one community-summary task per community.
5. The host agent writes community summaries.
6. Marrow runs the coverage audit (deterministic).

**Estimated host turns per book:** 30–80 for entity extraction, 5–15 for community summaries.

### Stage 04 — Atomic Claim Extraction

**API Mode:** Local Llama 3.1 8B via vLLM extracts claims per chunk.

**Host Mode:**
1. Marrow generates one task file per chunk: `tasks/claims_<chunk_uuid>.task.json` containing the chunk text and the `AtomicClaim` schema.
2. The host agent extracts claims, writes one result file per chunk.
3. Marrow runs semantic deduplication (deterministic, uses local embeddings only).

**Estimated host turns per book:** 30–80, identical to Stage 03 cardinality.

### Stage 05 — Hierarchical Synthesis

**API Mode:** Claude Sonnet 4.6 generates per-chapter and book-level synthesis.

**Host Mode:**
1. Marrow groups claims and community summaries by source chapter.
2. For each chapter, Marrow writes `tasks/synthesize_chapter_<n>.task.json` containing the chapter's claims, community summaries, and the `BriefSection` schema.
3. The host agent writes per-chapter synthesis with mandatory `[chunk:UUID]` citations.
4. Marrow runs the citation audit (deterministic).
5. Marrow writes a final `tasks/synthesize_book.task.json` containing all chapter syntheses + the book-level merge instructions.
6. The host agent writes the book-level merge.
7. Marrow checks compression target and either accepts, triggers a consolidation task, or warns.

**Estimated host turns per book:** 8–25 (one per chapter plus the final merge plus possible consolidation).

### Stage 05b — Adversarial Quiz Validation

**API Mode:** Claude Sonnet generates and answers the quiz.

**Host Mode:**
1. Marrow writes `tasks/quiz_generate_<chapter>.task.json` per chapter containing the source chunks and the `QuizQuestion` schema.
2. The host agent generates 5 leaf-level questions per chapter.
3. Marrow writes `tasks/examinee_<chapter>.task.json` containing **only the brief section** (not the source) and the questions.
4. The host agent attempts the quiz from the brief alone.
5. Marrow scores the responses and, on failures, writes a regeneration task targeting the specific chapter sections that lost coverage.
6. Loop up to 3 iterations.

**Estimated host turns per book:** 10–30 per validation iteration; typically 1–2 iterations.

### Stage 06a — Evaluation

**API Mode:** BooookScore, FActScore, HAMLET each call their evaluator LLMs.

**Host Mode:**
1. Marrow precomputes everything that doesn't need an LLM (atomic decomposition, length penalty, key-fact tree construction).
2. Marrow writes per-claim verdict tasks: `tasks/evaluate_claim_<id>.task.json`.
3. The host agent renders verdicts (SUPPORT / REFUTE / NEI) for each claim against the source.
4. Marrow aggregates the verdicts into the BooookScore, FActScore, and HAMLET reports.

**Estimated host turns per book:** 50–200, depending on claim density. This is the most expensive stage in Host Mode and may be the place where users explicitly opt for API Mode in CI runs.

---

## 7. The Skill File

The skill file lives at `marrow-skill/SKILL.md` inside the Marrow repo and is published as a Claude Code skill. Its structure:

```markdown
---
name: marrow
description: |
  Run the Marrow lossless book-to-brief pipeline using your own
  Claude Code session tokens. Triggers on "process this book with marrow",
  "run marrow on book.pdf", "continue the marrow run", or any request
  to compress a non-fiction book into a 50-page brief with full citations.
---

# Marrow — Host Mode Skill

## Overview
You are running Marrow inside a Claude Code session. You will drive the
pipeline by calling `marrow next <slug>` and responding to the tasks it
returns. All reasoning happens in this session — Marrow itself never
calls an LLM in Host Mode.

## Workflow
1. Start a run: `marrow run <book.pdf> --mode host`
2. Loop:
   a. Run `marrow next <slug>`
   b. Read the returned task type and section reference
   c. Read the pending task files
   d. Perform the reasoning (you are the LLM here)
   e. Write result files in the format declared in the task
   f. Run `marrow next <slug>` again
3. When `marrow next` returns `{"status": "complete"}`, the brief is in
   the configured Obsidian vault.

## Task Types (each documented in detail below)
- entity_extract — Stage 03
- relation_extract — Stage 03
- community_summary — Stage 03
- claim_extract — Stage 04
- synthesize_chapter — Stage 05
- synthesize_book — Stage 05
- quiz_generate — Stage 05b
- examinee_answer — Stage 05b
- evaluate_claim — Stage 06a

## Per-task instructions
[detailed schemas and prompt templates for each task type]

## Resumption
If the user says "continue the marrow run", run `marrow status` to find
in-progress runs, then `marrow next <slug>` to pick up where you left off.

## Failure handling
Every task that fails schema validation is written back as a corrective
task. Do not retry without reading the new task file — the corrections
matter.

## Cost reporting
After each stage, run `marrow cost <slug>` to see the estimated host
token usage. This is an estimate; real cost lives in your Claude Code
billing.
```

The full skill file is 800–1500 lines and includes one detailed section per task type with input schema, output schema, and a worked example. It is the longest single document in the Marrow repo and the most user-facing surface after the README.

---

## 8. New CLI Surface

Host Mode adds the following commands to the CLI specified in `API.md`:

| Command | Purpose |
|---|---|
| `marrow run <book> --mode host` | Start a Host Mode run. Default behavior from v1.0. |
| `marrow next <slug>` | Return the next pending task batch as JSON. The host agent's loop pivot point. |
| `marrow submit <slug> <task_id> <result_path>` | Submit a result for a specific task. (Optional convenience — host agent can also write the result file directly and call `next` again.) |
| `marrow cost <slug>` | Print the estimated host-token usage for the run. |
| `marrow tasks <slug>` | List all task files (pending + complete) for inspection. |
| `marrow validate-result <slug> <result_path>` | Manually validate a result file against its task schema (debug helper). |

The original `marrow run`, `marrow batch`, `marrow status`, `marrow clean`, and `marrow ask` commands all continue to work in both modes.

---

## 9. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-H01 | A Host Mode run on a 300-page book MUST complete in ≤ 4 hours of wall-clock time when the host agent is actively processing tasks. |
| NFR-H02 | A Host Mode run MUST be resumable across host sessions, machines, and operating-system reboots, given the same `runs/` directory. |
| NFR-H03 | A Host Mode run MUST NOT require any network access beyond what the host agent itself uses. Marrow's own subprocess MUST NOT make outbound HTTP calls. |
| NFR-H04 | The skill file MUST be ≤ 30k tokens so it loads efficiently into Claude Code's skill system. |
| NFR-H05 | Task files MUST be valid JSON, validated against the published task schema, and human-readable when opened in a text editor. |
| NFR-H06 | The host agent MUST be able to operate on tasks one at a time without ever needing the entire book in its context window. |
| NFR-H07 | A Host Mode run MUST produce byte-identical Obsidian output to an API Mode run *given identical reasoning*. (Stochasticity from the LLM is allowed; deterministic post-processing is not.) |

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Host agents drift from the skill instructions over long runs | High — lossless guarantee broken | Skill includes self-check prompts every 10 tasks; coverage audits run after every stage and abort the run on failure |
| Host agent context window fills up before a stage completes | Medium | Tasks are small (≤8k input tokens); the host agent never needs cross-task memory because all state is on disk |
| User accidentally runs API Mode and Host Mode on the same book | Low | Mode lock (FR-H08) prevents resume across modes |
| Host agents produce invalid JSON for results | High — pipeline blocks | Strict Pydantic validation + corrective task loop with up to 3 retries; failure to validate after retries marks the chunk as failed and continues |
| Estimated cost in `marrow cost` diverges wildly from real Claude Code billing | Low | Label as estimate, link to Claude Code billing in the CLI output |
| Skill file too large to fit in some host agents' skill loaders | Medium | Hard cap at 30k tokens; modularize into sub-files loaded on demand |
| Host agent refuses a task on safety grounds (e.g., misreads a chapter as harmful) | Low | Marrow logs the refusal as a `failed_task`, continues, and reports it in the final summary |

---

## 11. Acceptance Criteria

Host Mode is considered **shipped** when all of the following are true:

- [ ] `marrow run book.pdf --mode host` produces a complete brief on the 5-book reference corpus without any API key being set in the environment.
- [ ] The skill file at `marrow-skill/SKILL.md` is loadable into Claude Code and into Codex CLI without modification.
- [ ] A Host Mode run can be killed mid-stage and resumed in a fresh Claude Code session with `marrow next <slug>`.
- [ ] All 9 P0 user stories from `PRD.md` pass in Host Mode (US-001 through US-009).
- [ ] HAMLET leaf-recall ≥ 92% on at least 4 of 5 reference books, achieved through the host agent's reasoning alone.
- [ ] `marrow cost` reports a non-trivial estimate after a successful run.
- [ ] The mode lock prevents mixing API and Host runs on the same book slug.
- [ ] The README documents Host Mode as the default and explains how a Claude Code Max user gets started in three commands.
- [ ] At least one full end-to-end run is recorded as a screencast / asciinema and embedded in the README.

---

## 12. Open Questions

These need answers before implementation begins. Each is a `[DECISION NEEDED]` for the build phase.

1. **`marrow next` polling vs. blocking.** Should `marrow next` block until tasks are ready (cleaner host loop) or always return immediately with current state (simpler implementation)? Leaning toward immediate return + the host agent re-polls.
2. **Task batching.** Should `marrow next` return one task at a time or a batch of N tasks? Batching reduces host turns but risks context window overflow. Default: batch of 5, configurable.
3. **Per-task vs. per-stage validation.** Should Marrow validate each result the moment it's written, or only at stage end? Per-task validation gives faster feedback loops but more I/O.
4. **MCP server alternative.** Should Host Mode also expose an MCP server interface for clients that prefer that pattern over the file-based protocol? Defer to v1.1.
5. **Codex compatibility.** Codex CLI's session model differs from Claude Code's. The skill file may need a Codex-specific section. Empirically test on both before v1.0.
6. **Subscription enforcement.** Does Marrow check whether the host agent is actually a paid Claude Code Max session vs. a free-tier session that will rate-limit? No — out of scope; user's responsibility.

---

## 13. Why This Is the Most Important Feature

Every other feature in `PRD.md` is about **what the brief looks like**. Host Mode is about **whether anyone will actually run Marrow more than twice**.

A user who has to set up an API key, fund it, watch a meter tick, and worry about whether their experiment with a new book will cost $4 or $40 will run Marrow once, get a brief, and then never run it again. A user who can type "process `book.pdf` with marrow" inside their existing Claude Code session and watch the brief get built using capacity they're already paying for will run Marrow on every book they read for the next decade.

Host Mode is the difference between a research demo and a tool that becomes part of someone's reading practice. It is non-negotiable. Build it first, ship it default, document it loudly, and keep API Mode around only for the CI cases that genuinely need unattended automation.

---
**End of HOST_MODE.md**
