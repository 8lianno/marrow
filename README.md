# Marrow

> **Read the marrow. Lossless book briefs for people who refuse to skim.**

Marrow turns a 300-page non-fiction book into a ~50-page conceptual brief that
preserves every load-bearing idea, framework, definition, claim, example, and
counter-argument from the source — with every sentence in the brief traceable
to an exact paragraph in the original book.

Other tools summarize and silently drop. Marrow ships with a machine-checkable
receipt: HAMLET leaf-recall, SummQ adversarial validation, and 100% citation
traceability to `^uuid` block anchors in Obsidian.

## What makes it different

- **Lossless gate, not vibes.** Every stage that could drop content has an
  explicit audit. A brief that passes didn't just sound complete — it was
  graded against the source and survived.
- **Host-first by default.** Marrow runs inside Claude Code / Codex in Host
  Mode by default. Zero API keys required. Zero metered billing from Marrow.
- **Two modes, same output.** Either Marrow calls the LLM itself (`--mode api`,
  supports Anthropic / Gemini / OpenRouter / Ollama), or the host agent
  (Claude Code / Codex) does the reasoning via a file-based task protocol
  (`--mode host`).
- **File-based, resumable, inspectable.** No daemon, no database server. Every
  stage boundary is a Pydantic-validated JSONL artifact on disk. `ls` and `cat`
  are the debugger.

## Pipeline

```mermaid
flowchart LR
    PDF[📄 PDF / EPUB]:::input
    PDF --> S1

    subgraph Deterministic
        S1[01_ingest<br/>Docling<br/>hierarchy + pages]:::det
        S2[02_chunk<br/>Jina v2<br/>late chunking + LanceDB]:::det
    end

    subgraph LLM-backed
        S3[03_graph<br/>entities + relations<br/>Louvain communities]:::llm
        S4[04_claims<br/>atomic claims<br/>semantic dedup 0.92]:::llm
        S5[05_synthesize<br/>hierarchical merge<br/>per-chapter sections]:::llm
    end

    subgraph Lossless-gate
        S5b[05b_validate<br/>SummQ quiz<br/>≤3 iters, ≥0.90 pass]:::gate
        S6a[06a_evaluate<br/>BooookScore + FActScore<br/>HAMLET recall]:::gate
    end

    S6b[06b_export<br/>Obsidian Brief.md<br/>+ Source.md anchors]:::out

    S1 --> S2 --> S3 --> S4 --> S5 --> S5b --> S6a --> S6b
    S6b --> VAULT[📚 Obsidian vault]:::output

    classDef input fill:#222,stroke:#888,stroke-width:1px,color:#fff
    classDef det fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef llm fill:#3a2d4a,stroke:#5e4a7a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
    classDef out fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef output fill:#222,stroke:#888,stroke-width:1px,color:#fff
```

Every artifact crossing a stage boundary is a Pydantic v2 model serialized to
JSONL in `runs/<book-slug>/<NN>_<stage>/`. Resuming mid-pipeline (`--resume`)
skips any stage that wrote a `_complete` marker.

## Per-stage flow

Each stage below has a short diagram of its internal pipeline. Click to expand.

<details>
<summary><strong>01_ingest</strong> — PDF/EPUB → <code>CanonicalDocument</code> with hierarchy and per-paragraph provenance</summary>

```mermaid
flowchart TD
    IN[📄 PDF / EPUB]:::io --> DC[Docling DocumentConverter]:::op
    DC --> II[iterate_items]:::op
    II --> SW[Walk items, refine heading levels<br/>by text pattern when Docling flattens]:::op
    SW --> TREE[Hierarchical SectionNode tree<br/>+ ParagraphNode with page_no]:::data
    TREE --> AUD{ChapterCoverageAudit<br/>headings detected?}:::gate
    AUD -->|pass| OUT1[document.json]:::io
    TREE --> MD[Render source.md<br/>with ^paragraph_uuid anchors]:::op
    MD --> OUT2[source.md]:::io
    AUD -->|fallback| FB[pypdf plain-text<br/>+ Chapter-N heuristic]:::op
    FB --> TREE

    classDef io fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef op fill:#222,stroke:#888,color:#fff
    classDef data fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
```

</details>

<details>
<summary><strong>02_chunk</strong> — paragraph-aligned chunks with Jina v2 late-chunking embeddings</summary>

```mermaid
flowchart TD
    IN[document.json]:::io --> IP[Flatten to paragraphs<br/>with chapter_path + page]:::op
    IP --> PLAN[plan_chunks:<br/>target tokens + overlap<br/>respects chapter boundaries]:::op
    PLAN --> EMB[Embedder<br/>Jina v2 late-chunk pool<br/>or StubEmbedder]:::op
    EMB --> WS[Sliding window + 25% overlap<br/>for docs > 8192 tokens]:::op
    WS --> REC[ChunkRecord<br/>UUID5 deterministic IDs]:::data
    REC --> AUD{paragraph coverage<br/>== 100%?}:::gate
    AUD --> OUT1[chunks.jsonl]:::io
    REC --> VEC[LanceDB Arrow table<br/>768-dim fixed-size]:::op
    VEC --> OUT2[vectors.lance/]:::io

    classDef io fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef op fill:#222,stroke:#888,color:#fff
    classDef data fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
```

</details>

<details>
<summary><strong>03_graph</strong> — entity/relationship extraction + Louvain communities with coverage audit</summary>

```mermaid
flowchart TD
    IN[chunks.jsonl]:::io --> EX[Per chunk:<br/>LLM extract_graph.j2<br/>→ ExtractedGraphResponse]:::llm
    EX --> MERGE[Merge entities by<br/>normalized canonical_name]:::op
    MERGE --> RES[Resolve relationship endpoints<br/>drop dangling edges]:::op
    RES --> NX[Build NetworkX graph<br/>weighted by confidence]:::op
    NX --> LOU[Louvain community detection<br/>seed=42 deterministic]:::op
    LOU --> SUM[Per community:<br/>LLM summarize_community.j2<br/>→ title + 150–300w summary]:::llm
    SUM --> CA{CoverageAudit<br/>every chunk in a community?}:::gate
    CA -->|orphans exist| ORPH[Synthetic _orphans bucket]:::op
    CA -->|100%| DONE[coverage_pct = 100]:::data
    ORPH --> DONE
    DONE --> OUT1[entities.jsonl]:::io
    DONE --> OUT2[relations.jsonl]:::io
    DONE --> OUT3[communities.jsonl]:::io
    DONE --> OUT4[graph.graphml]:::io

    classDef io fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef op fill:#222,stroke:#888,color:#fff
    classDef llm fill:#3a2d4a,stroke:#5e4a7a,color:#fff
    classDef data fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
```

</details>

<details>
<summary><strong>04_claims</strong> — SciClaims-style atomic claim extraction + semantic dedup at 0.92 cosine</summary>

```mermaid
flowchart TD
    IN[chunks.jsonl]:::io --> EX[Per chunk:<br/>LLM extract_claims.j2<br/>→ ExtractedClaimsResponse]:::llm
    EX --> FAIL{chunk failed?}:::gate
    FAIL -->|yes| LOG[failed_chunks log<br/>stage continues]:::op
    FAIL -->|no| BUILD[Build AtomicClaim<br/>UUID5 from claim_text + book_slug]:::op
    BUILD --> MERGE[Exact-text merge<br/>via claim_id collision]:::op
    MERGE --> EMBED[Embed claim texts<br/>via embedder]:::op
    EMBED --> COS[Pairwise cosine similarity<br/>threshold 0.92]:::op
    COS --> DUP[Mark is_duplicate_of<br/>keep first occurrence]:::op
    DUP --> OUT1[claims.jsonl]:::io
    DUP --> MANI[ClaimsManifest<br/>extracted / after_dedup / failed]:::data
    MANI --> OUT2[dedup_report.json]:::io

    classDef io fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef op fill:#222,stroke:#888,color:#fff
    classDef llm fill:#3a2d4a,stroke:#5e4a7a,color:#fff
    classDef data fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
```

</details>

<details>
<summary><strong>05_synthesize</strong> — hierarchical per-chapter synthesis with inline <code>[chunk:UUID]</code> citations</summary>

```mermaid
flowchart TD
    C[claims.jsonl]:::io --> GRP[Group claims by chapter<br/>via source_chunk_uuids → chunk → chapter]:::op
    COM[communities.jsonl]:::io --> GRPC[Route communities by<br/>majority-vote on chunk chapters]:::op
    DOC[document.json]:::io --> ORD[Chapter order from ToC]:::op
    GRP --> BUDG[Allocate word budget<br/>proportional to claim share]:::op
    BUDG --> SYN[Per chapter:<br/>LLM synthesize_chapter.j2<br/>→ ChapterSynthesisResponse]:::llm
    GRPC --> SYN
    ORD --> SYN
    SYN --> PARSE[Parse body_md for<br/>chunk:UUID citations]:::op
    PARSE --> COV{Every source chunk<br/>cited in output?}:::gate
    COV --> DRAFT[BriefDraft<br/>sections + word_count + citation_density]:::data
    DRAFT --> OUT1[draft_brief.json]:::io
    COV --> AUDIT[merge_tree.json<br/>per-chapter audit:<br/>input_claims, citations_found, missing_chunks]:::data
    AUDIT --> OUT2[merge_tree.json]:::io

    classDef io fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef op fill:#222,stroke:#888,color:#fff
    classDef llm fill:#3a2d4a,stroke:#5e4a7a,color:#fff
    classDef data fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
```

</details>

<details>
<summary><strong>05b_validate</strong> — SummQ adversarial quiz loop with per-chapter regeneration</summary>

```mermaid
flowchart TD
    C[chunks.jsonl]:::io --> SAMP[Sample ≤ 30 chunks<br/>seeded RNG]:::op
    SAMP --> QG[Per chunk:<br/>LLM quiz_generate.j2<br/>→ GeneratedQuiz]:::llm
    QG --> QUIZ[QuizQuestion set<br/>stable across iterations]:::data
    D[draft_brief.json]:::io --> LOOP

    subgraph LOOP[Iteration up to max_iterations]
        EX[Per question:<br/>LLM examinee_answer.j2]:::llm
        GR[Per answer:<br/>LLM quiz_grade.j2]:::llm
        EX --> GR
        GR --> PASS{pass_rate ≥<br/>threshold?}:::gate
        PASS -->|no, not max| REGEN[Identify chapters by<br/>failure count → re-run<br/>stage_05 synthesis on them]:::op
        REGEN --> EX
    end

    QUIZ --> LOOP
    LOOP --> BEST[Keep best-pass-rate draft]:::op
    BEST --> OUT1[final_brief.json]:::io
    LOOP --> ITER[iter_NN/<br/>quiz.jsonl + results.json]:::io

    classDef io fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef op fill:#222,stroke:#888,color:#fff
    classDef llm fill:#3a2d4a,stroke:#5e4a7a,color:#fff
    classDef data fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
```

</details>

<details>
<summary><strong>06a_evaluate</strong> — BooookScore + FActScore + HAMLET composite with PASS/FAIL verdict</summary>

```mermaid
flowchart TD
    B[final_brief.json]:::io --> BS[BooookScore:<br/>LLM coherence per chapter<br/>→ avg 0.0–1.0]:::llm
    B --> FS[FActScore:<br/>sample ≤ 20 cited sentences<br/>LLM verify against source chunk]:::llm
    C[chunks.jsonl]:::io --> FS
    B --> HAM
    D[document.json]:::io --> HAM
    CL[claims.jsonl]:::io --> HAM
    subgraph HAM[HAMLET deterministic recall]
        R[root: chapters covered]:::op
        BR[branch: chunks cited]:::op
        L[leaf: claims whose chunk is cited]:::op
    end
    BS --> COMP[Composite = weighted sum<br/>0.20·B + 0.30·F + 0.30·leaf<br/>+ 0.10·branch + 0.10·root]:::op
    FS --> COMP
    HAM --> COMP
    COMP --> V{All three above<br/>configured thresholds?}:::gate
    V -->|yes| PASS[verdict = PASS]:::data
    V -->|no| FAIL[verdict = FAIL<br/>+ failure_reasons]:::data
    PASS --> OUT[composite.json<br/>EvaluationReport]:::io
    FAIL --> OUT

    classDef io fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef op fill:#222,stroke:#888,color:#fff
    classDef llm fill:#3a2d4a,stroke:#5e4a7a,color:#fff
    classDef data fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
```

</details>

<details>
<summary><strong>06b_export</strong> — Obsidian Markdown with citation round-trip audit</summary>

```mermaid
flowchart TD
    C[chunks.jsonl]:::io --> SRC[Render Source.md<br/>chunk-by-chunk<br/>each preceded by ^chunk_uuid]:::op
    B[final_brief.json]:::io --> BRF[Render Brief.md<br/>translate chunk:UUID →<br/>slug_Source#^UUID wikilinks]:::op
    E[composite.json]:::io --> EV[Render Evaluation.md<br/>verdict + metrics table]:::op
    SRC --> RT{Citation round-trip:<br/>every brief anchor resolves<br/>in Source.md?}:::gate
    BRF --> RT
    RT -->|pass| OUT1[slug_Source.md]:::io
    RT -->|pass| OUT2[slug_Brief.md]:::io
    EV --> OUT3[slug_Evaluation.md]:::io
    RT -->|unresolved| WARN[warning in StageResult<br/>status becomes warning]:::op
    WARN --> OUT2

    classDef io fill:#2d3a4a,stroke:#4a5e7a,color:#fff
    classDef op fill:#222,stroke:#888,color:#fff
    classDef data fill:#2d4a2d,stroke:#4a7a4a,color:#fff
    classDef gate fill:#4a3a2d,stroke:#7a5e4a,color:#fff
```

</details>

## Quick start

```bash
# Install
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Run it inside a host agent (host mode, zero API keys)

```bash
# Claude Code: install the skill once
ln -s "$(pwd)/skills/claude-code/marrow" ~/.claude/skills/marrow

# Then in any Claude Code session:
/marrow /path/to/book.pdf

# Codex: use the Codex playbook in this repo
cat skills/codex/marrow/SKILL.md
```

In host mode, the host agent launches Marrow, claims batches of work via
`marrow next`, delegates independent tasks to helper agents when appropriate,
and submits `HostResult` JSON back with `marrow submit`. The same task protocol
works in Claude Code and Codex. $0.00 metered cost.

### Run it with an LLM provider (API mode)

```bash
# Local Ollama (qwen3:14b on localhost:11434)
marrow run path/to/book.pdf --config configs/ollama.yaml

# OpenRouter / Gemini / Anthropic presets
marrow run path/to/book.pdf --config configs/openrouter.yaml
marrow run path/to/book.pdf --config configs/gemini.yaml
marrow run path/to/book.pdf --config configs/anthropic.yaml

# Resume after interruption
marrow run path/to/book.pdf --resume

# Inspect per-stage progress
marrow status <book-slug>
```

## Configuration

Config resolution: **defaults → `configs/default.yaml` → user `--config` file
→ env vars (`MARROW_*`) → CLI flags** (later overrides earlier).

Presets in `configs/`:

| File | Purpose |
|---|---|
| [`default.yaml`](configs/default.yaml) | Host Mode default; host agent does all reasoning |
| [`ollama.yaml`](configs/ollama.yaml) | Explicit local API-mode preset via Ollama |
| [`cheap.yaml`](configs/cheap.yaml) | Host-first low-cost profile with tighter budget cap |
| [`openrouter.yaml`](configs/openrouter.yaml) | OpenRouter gateway (needs `OPENROUTER_API_KEY`) |
| [`gemini.yaml`](configs/gemini.yaml) | Gemini Flash + Pro (needs `GEMINI_API_KEY`) |
| [`anthropic.yaml`](configs/anthropic.yaml) | Sonnet 4.6 for synthesis + validation (needs `ANTHROPIC_API_KEY`) |

Model routing is per-role. For example, `anthropic.yaml` uses local Ollama for
the hot per-chunk work (claims + graph) but Sonnet for synthesis + validation
where quality matters most, and `openrouter.yaml` swaps the hot per-chunk work
to OpenRouter while keeping Anthropic for the quality-critical stages.

## Docs

- [PRD.md](PRD.md) — product requirements, user stories, acceptance metrics
- [ARCHITECTURE.md](ARCHITECTURE.md) — principles, stage contract, decisions
- [ROADMAP.md](ROADMAP.md) — M0 walking skeleton → M1–M6 stage-fill milestones
- [HOST_MODE.md](HOST_MODE.md) — task/result protocol + skill install
- [API.md](API.md) — CLI surface + internal module APIs + stage contract
- [DATABASE.md](DATABASE.md) — working-directory layout + SQLite cost ledger
- [BRAND.md](BRAND.md) — name, voice, positioning
- [REPOS.md](REPOS.md) — upstream open-source inventory
- [CLAUDE.md](CLAUDE.md) — per-session dev guide for Claude Code / Codex

## Status

All eight stages real end-to-end. 61 fast tests passing. Host Mode
verified — drove a full pipeline through stages 01→03 via the skill,
cost ledger recorded provider=`host` at $0.00, schema validation passed
on every result.

**Known gaps:**
- Tested on tiny synthetic fixtures, not a real 300-page book yet.
- The Ollama API preset (`qwen3:14b`) is strong for extraction
  but verbose for synthesis; `configs/anthropic.yaml` or
  `configs/gemini.yaml` produce cleaner briefs when you need PASS
  verdicts from the lossless gate.

## License

[MIT](LICENSE)
