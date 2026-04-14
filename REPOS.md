# Marrow — Repositories & Tooling Inventory

**Version:** 1.0 | **Date:** 2026-04-14

> Every external repository Marrow depends on, considers as an alternative, or borrows patterns from. Organized by pipeline stage. The "Use in Marrow" column states whether each repo is a **direct dependency** of the default install, an **opt-in extra**, a **fallback**, a **pattern source** (we copy ideas, not code), or **rejected** with rationale.

---

## Stage 1 — Ingestion & Structural Parsing

| # | Repo | URL | License | Use in Marrow | Why |
|---|------|-----|---------|------------|-----|
| 1 | docling-project/docling | https://github.com/docling-project/docling | MIT | **Direct dep (default)** | DocLayNet + TableFormer; 97.9% table-cell accuracy; permissive license; enterprise-backed by IBM Research |
| 2 | docling-project/docling-core | https://github.com/docling-project/docling-core | MIT | **Direct dep (transitive)** | DoclingDocument schema |
| 3 | opendatalab/MinerU | https://github.com/opendatalab/MinerU | AGPL-3.0 | **Opt-in extra** (`pip install marrow[mineru]`) | Best-in-class formula extraction, but AGPL is incompatible with default install |
| 4 | datalab-to/marker | https://github.com/datalab-to/marker | GPL-3.0 + commercial | **Fallback** | Fast local PDF→Markdown; complements Docling on speed-sensitive runs |
| 5 | Unstructured-IO/unstructured | https://github.com/Unstructured-IO/unstructured | Apache 2.0 | **Pattern source** | Element extraction approach; we don't depend on it (over-extraction issues) |
| 6 | VikParuchuri/surya | https://github.com/VikParuchuri/surya | GPL-3.0 + commercial | **Transitive (via Marker)** | OCR engine for the Marker fallback path |
| 7 | VikParuchuri/texify | https://github.com/VikParuchuri/texify | GPL-3.0 + commercial | **Transitive (via Marker)** | LaTeX OCR for math-heavy fallback |
| 8 | facebookresearch/nougat | https://github.com/facebookresearch/nougat | MIT | **Pattern source** | Academic PDF transformer; Docling covers this case |
| 9 | py-pdf/pypdf | https://github.com/py-pdf/pypdf | BSD-3 | **Direct dep** | Page count, metadata extraction, simple operations Docling doesn't expose |
| 10 | DS4SD/DocLayNet | https://github.com/DS4SD/DocLayNet | CDLA-Permissive-1.0 | **Pattern source** | Layout dataset behind Docling |
| 11 | run-llama/llama_parse | https://github.com/run-llama/llama_parse | Closed-source SDK | **Rejected** | Hallucinates technical data; closed pipeline; cloud-only |
| 12 | UB-Mannheim/tesseract | https://github.com/UB-Mannheim/tesseract | Apache 2.0 | **Optional system dep** | OCR fallback when Surya isn't available |

---

## Stage 2 — Chunking & Embeddings

| # | Repo | URL | License | Use in Marrow | Why |
|---|------|-----|---------|------------|-----|
| 13 | jina-ai/late-chunking | https://github.com/jina-ai/late-chunking | Apache 2.0 | **Direct dep** | Reference implementation of Late Chunking; we use it directly |
| 14 | ndgigliotti/afterthoughts | https://github.com/ndgigliotti/afterthoughts | MIT | **Pattern source** | Sentence-aware late chunking patterns; we adapted the pooling logic |
| 15 | jinaai/jina-embeddings-v2-base-en | https://huggingface.co/jinaai/jina-embeddings-v2-base-en | Apache 2.0 | **Direct dep (model)** | 8192 context window; default embedder |
| 16 | jinaai/jina-embeddings-v3 | https://huggingface.co/jinaai/jina-embeddings-v3 | CC-BY-NC-4.0 | **Opt-in (non-commercial)** | Stronger model but non-commercial license restricts default use |
| 17 | langchain-ai/langchain | https://github.com/langchain-ai/langchain | MIT | **Pattern source** | RecursiveCharacterTextSplitter as baseline only; not a runtime dep |
| 18 | run-llama/llama_index | https://github.com/run-llama/llama_index | MIT | **Pattern source** | Document agent + DocumentSummaryIndex patterns; we don't depend on it |
| 19 | bclavie/RAGatouille | https://github.com/bclavie/RAGatouille | Apache 2.0 | **Optional rerank extra** | ColBERT reranking for v1.1 hybrid retrieval |
| 20 | nltk/nltk | https://github.com/nltk/nltk | Apache 2.0 | **Direct dep** | Sentence segmentation for chunk boundary alignment |
| 21 | huggingface/transformers | https://github.com/huggingface/transformers | Apache 2.0 | **Direct dep** | Embedding model loading and inference |
| 22 | microsoft/unilm/E5 | https://github.com/microsoft/unilm/tree/master/e5 | MIT | **Alternative embedder** | Strong multilingual fallback for v1.1 Persian support |

---

## Stage 3 — GraphRAG Indexing & Retrieval

| # | Repo | URL | License | Use in Marrow | Why |
|---|------|-----|---------|------------|-----|
| 23 | gusye1234/nano-graphrag | https://github.com/gusye1234/nano-graphrag | MIT | **Direct dep (default)** | 1100 LOC, hackable, async, ~10% the token cost of MS GraphRAG |
| 24 | HKUDS/LightRAG | https://github.com/HKUDS/LightRAG | MIT | **Alternative** | Faster indexing but lower ROUGE on aspect-based summaries |
| 25 | microsoft/graphrag | https://github.com/microsoft/graphrag | MIT | **Reference / benchmark** | Canonical GraphRAG; too expensive for default |
| 26 | OSU-NLP-Group/HippoRAG | https://github.com/OSU-NLP-Group/HippoRAG | MIT | **Pattern source** | Personalized PageRank over entity graph; v1.1 candidate |
| 27 | hkust-nlp/RAGFlow | https://github.com/infiniflow/ragflow | Apache 2.0 | **Pattern source** | Production-grade chunking + reranking patterns |
| 28 | networkx/networkx | https://github.com/networkx/networkx | BSD-3 | **Direct dep** | In-memory graph backend for NanoGraphRAG |
| 29 | vtraag/leidenalg | https://github.com/vtraag/leidenalg | GPL-3.0 | **Optional dep** | Reference Leiden implementation; networkx-community is the default |
| 30 | igraph/python-igraph | https://github.com/igraph/python-igraph | GPL-2.0 | **Optional dep** | Faster graph operations on huge corpora (v1.1) |
| 31 | lancedb/lancedb | https://github.com/lancedb/lancedb | Apache 2.0 | **Direct dep** | Embedded vector store; no server, single file, Apache Arrow native |
| 32 | qdrant/qdrant | https://github.com/qdrant/qdrant | Apache 2.0 | **Rejected (default)** | Server overhead unjustified for single-machine local tool |
| 33 | chroma-core/chroma | https://github.com/chroma-core/chroma | Apache 2.0 | **Rejected** | Schema enforcement weaker than LanceDB |
| 34 | weaviate/weaviate | https://github.com/weaviate/weaviate | BSD-3 | **Rejected** | Server overhead |
| 35 | neo4j/neo4j | https://github.com/neo4j/neo4j | GPL-3.0 + commercial | **Rejected (default)** | Server overhead; we use NetworkX for in-memory + GraphML for inspection |
| 36 | kuzudb/kuzu | https://github.com/kuzudb/kuzu | MIT | **Future candidate** | Embedded property graph DB; v1.1 if NetworkX hits limits |

---

## Stage 4 — Synthesis & Compression

| # | Repo | URL | License | Use in Marrow | Why |
|---|------|-----|---------|------------|-----|
| 37 | weixuan-wang123/SummQ | https://github.com/weixuan-wang123/SummQ | Research | **Pattern source** | Adversarial quiz validation loop; we re-implement the agent loop in `stage_05b_validate.py` |
| 38 | (SciClaims paper repo) | search arXiv for canonical link | CC BY 4.0 | **Pattern source** | Atomic claim extraction pattern; we re-implement the dual-call pipeline |
| 39 | (NexusSum paper repo) | search arXiv for canonical link | Research | **Pattern source** | Hierarchical multi-agent merge; dialogue-to-prose transformation |
| 40 | yixinL7/SumLLM | https://github.com/yixinL7/SumLLM | MIT | **Pattern source** | Training/eval scripts for long-doc summarization |
| 41 | openai/summarize-from-feedback | https://github.com/openai/summarize-from-feedback | MIT (archived) | **Pattern source** | RLHF reward modeling reference for v2 fine-tuning |
| 42 | huankoh/long-doc-summarization | https://github.com/huankoh/long-doc-summarization | MIT | **Reference (paper map)** | Curated literature index; dataset and metric pointers |
| 43 | Anthropic SDK | https://github.com/anthropics/anthropic-sdk-python | MIT | **Direct dep** | Claude Sonnet 4.6 access |
| 44 | vllm-project/vllm | https://github.com/vllm-project/vllm | Apache 2.0 | **Direct dep (local mode)** | High-throughput local serving for Llama 3.1 8B |
| 45 | meta-llama/llama-models | https://github.com/meta-llama/llama-models | Llama community license | **Direct dep (local model)** | Llama 3.1 8B Instruct for claim extraction |
| 46 | ggml-org/llama.cpp | https://github.com/ggml-org/llama.cpp | MIT | **Optional alt-runtime** | CPU/Metal serving for users without GPUs |
| 47 | ollama/ollama | https://github.com/ollama/ollama | MIT | **Optional alt-runtime** | Easier local model setup; vLLM is faster but heavier |
| 48 | sgl-project/sglang | https://github.com/sgl-project/sglang | Apache 2.0 | **Future alt-runtime** | Higher throughput than vLLM for structured output workloads |
| 49 | guidance-ai/guidance | https://github.com/guidance-ai/guidance | MIT | **Pattern source** | Constrained generation patterns (we use Pydantic schemas instead) |
| 50 | jxnl/instructor | https://github.com/jxnl/instructor | MIT | **Direct dep** | Pydantic-validated structured output for Anthropic SDK |
| 51 | outlines-dev/outlines | https://github.com/outlines-dev/outlines | Apache 2.0 | **Alternative** | Structured generation for local models; instructor preferred for Anthropic |

---

## Stage 5 — Evaluation & Omission Detection

| # | Repo | URL | License | Use in Marrow | Why |
|---|------|-----|---------|------------|-----|
| 52 | lilakk/BooookScore | https://github.com/lilakk/BooookScore | MIT | **Direct dep** | Coherence scoring; ICLR 2024 reference impl |
| 53 | shmsw25/FActScore | https://github.com/shmsw25/FActScore | MIT | **Direct dep** | Atomic factual precision with γ=10 length penalty |
| 54 | DISL-Lab/HAMLET | https://github.com/DISL-Lab/HAMLET | MIT | **Direct dep** | Root/branch/leaf recall; the lossless gate |
| 55 | mungg/FABLES | https://github.com/mungg/FABLES | MIT | **Reference (benchmark)** | Faithfulness annotations; not a runtime dep but used to validate our threshold choices |
| 56 | explodinggradients/ragas | https://github.com/explodinggradients/ragas | Apache 2.0 | **Optional eval extra** | Faithfulness, answer relevancy for the v1.1 query mode |
| 57 | confident-ai/deepeval | https://github.com/confident-ai/deepeval | Apache 2.0 | **Pattern source** | LLM-as-judge harness; we wrote our own |
| 58 | Arize-ai/phoenix | https://github.com/Arize-ai/phoenix | Elastic-2.0 | **Optional observability** | Trace inspection for debugging LLM calls |
| 59 | UKPLab/sentence-transformers | https://github.com/UKPLab/sentence-transformers | Apache 2.0 | **Direct dep** | Used inside FActScore atomization |
| 60 | explosion/spaCy | https://github.com/explosion/spaCy | MIT | **Direct dep** | Atomic fact decomposition (used by FActScore) |
| 61 | Tiiiger/bert_score | https://github.com/Tiiiger/bert_score | MIT | **Optional metric** | Ancillary score; not part of the lossless gate |
| 62 | google-research/rouge | https://github.com/google-research/google-research/tree/master/rouge | Apache 2.0 | **Optional metric** | Comparison-only against baselines |

---

## Stage 6 — Export to Obsidian / Logseq

| # | Repo | URL | License | Use in Marrow | Why |
|---|------|-----|---------|------------|-----|
| 63 | obsidianmd/obsidian-api | https://github.com/obsidianmd/obsidian-api | MIT | **Pattern source** | Block reference syntax reference; we generate Markdown directly, no plugin |
| 64 | logseq/logseq | https://github.com/logseq/logseq | AGPL-3.0 | **Pattern source** | Block ID property syntax; we don't link to Logseq code |
| 65 | python-markdown/markdown | https://github.com/Python-Markdown/markdown | BSD-3 | **Direct dep** | Markdown parsing for citation rewriting |
| 66 | executablebooks/mdformat | https://github.com/executablebooks/mdformat | MIT | **Direct dep** | Deterministic Markdown formatting for byte-stable export |
| 67 | snowballstem/snowball | https://github.com/snowballstem/snowball | BSD-3 | **Optional dep** | Slug generation for book filenames |

---

## Cross-Cutting — Orchestration, CLI, Infra

| # | Repo | URL | License | Use in Marrow | Why |
|---|------|-----|---------|------------|-----|
| 68 | tiangolo/typer | https://github.com/tiangolo/typer | MIT | **Direct dep** | CLI; type hints become CLI args automatically |
| 69 | Textualize/rich | https://github.com/Textualize/rich | MIT | **Direct dep** | Pretty CLI output, progress bars, tables |
| 70 | pydantic/pydantic | https://github.com/pydantic/pydantic | MIT | **Direct dep** | All schemas; v2 required |
| 71 | hynek/structlog | https://github.com/hynek/structlog | MIT/Apache | **Direct dep** | Structured logging with contextvars |
| 72 | astral-sh/uv | https://github.com/astral-sh/uv | MIT/Apache | **Recommended dev tool** | 10× faster Python env management |
| 73 | astral-sh/ruff | https://github.com/astral-sh/ruff | MIT | **Dev dep** | Linting + formatting |
| 74 | python/mypy | https://github.com/python/mypy | MIT | **Dev dep** | Strict type checking |
| 75 | pytest-dev/pytest | https://github.com/pytest-dev/pytest | MIT | **Dev dep** | Test runner |
| 76 | kiwicom/pytest-recording | https://github.com/kiwicom/pytest-recording | MIT | **Dev dep** | VCR cassettes for LLM call tests |
| 77 | HypothesisWorks/hypothesis | https://github.com/HypothesisWorks/hypothesis | MPL-2.0 | **Dev dep** | Property-based testing for chunking determinism |
| 78 | pyyaml/pyyaml | https://github.com/yaml/pyyaml | MIT | **Direct dep** | Config file parsing |
| 79 | jinja/jinja | https://github.com/pallets/jinja | BSD-3 | **Direct dep** | Prompt templates |
| 80 | python/cpython sqlite3 | stdlib | PSF | **Direct dep** | Cost ledger backing store |

---

## Reference / Comparison-Only (Not Used at Runtime)

These repos are referenced in the PRD or research but explicitly **not** used in the default install. Listed for completeness so future contributors don't re-litigate the choice.

| Repo | URL | Why Listed | Why Not Used |
|------|-----|-----------|--------------|
| deepset-ai/haystack | https://github.com/deepset-ai/haystack | Research candidate | Heavier than needed for a single-machine CLI; we own the orchestrator |
| crewAIInc/crewAI | https://github.com/crewAIInc/crewAI | Multi-agent orchestrator | Stage architecture is simpler and more debuggable than agent loops |
| microsoft/autogen | https://github.com/microsoft/autogen | Multi-agent | Same reason |
| langchain-ai/langgraph | https://github.com/langchain-ai/langgraph | Stateful agent graphs | Our pipeline is linear with explicit checkpoints |
| stanfordnlp/dspy | https://github.com/stanfordnlp/dspy | Programmatic prompt optimization | v2 candidate for prompt auto-tuning |
| (LazyGraphRAG paper repo) | search Microsoft Research | GraphRAG variant | NanoGraphRAG already covers the cost-efficient end |
| getzep/graphiti | https://github.com/getzep/graphiti | Temporal knowledge graph | Designed for evolving conversations, not static books |
| topoteretes/cognee | https://github.com/topoteretes/cognee | Memory framework | Overlap with our stage 03; NanoGraphRAG is a tighter fit |
| mem0ai/mem0 | https://github.com/mem0ai/mem0 | Memory framework | Same |

---

## Install Manifest (Default Stack)

```toml
# pyproject.toml — default install
[project.dependencies]
python = ">=3.11"
typer = ">=0.12"
rich = ">=13"
pydantic = ">=2.6"
structlog = ">=24"
pyyaml = ">=6"
jinja2 = ">=3"

# Stage 1
docling = ">=2.0"
pypdf = ">=4"

# Stage 2
transformers = ">=4.40"
sentence-transformers = ">=3"
nltk = ">=3.8"
lancedb = ">=0.10"

# Stage 3
nano-graphrag = ">=0.0.8"   # or git ref
networkx = ">=3.2"

# Stage 4
anthropic = ">=0.40"
instructor = ">=1.3"

# Stage 5
booookscore = ">=1.0"
spacy = ">=3.7"
# factscore + HAMLET cloned, not pip-installed (subprocess isolation)

# Stage 6
markdown = ">=3.5"
mdformat = ">=0.7"

[project.optional-dependencies]
dev = ["pytest", "pytest-recording", "hypothesis", "ruff", "mypy"]
local = ["vllm>=0.5"]
mineru = ["mineru>=1.3"]              # AGPL — opt-in only
marker = ["marker-pdf>=0.3"]          # GPL — opt-in only
rerank = ["ragatouille>=0.0.8"]
phoenix = ["arize-phoenix>=4"]
```

---

## License Compatibility Matrix

| License | Default Install | Notes |
|---------|-----------------|-------|
| MIT | ✅ | Preferred |
| Apache 2.0 | ✅ | Preferred |
| BSD-3 / BSD-2 | ✅ | Compatible |
| MPL-2.0 | ✅ (dev only) | Hypothesis only |
| GPL-2.0 / GPL-3.0 | ❌ (default) | Opt-in extras only |
| AGPL-3.0 | ❌ (default) | MinerU, Logseq — opt-in only |
| LGPL-3.0 | ⚠️ | Allowed transitively only |
| Llama Community | ✅ (model only) | Llama 3.1 weights |
| CC-BY-NC | ❌ | Jina v3 — non-commercial |
| Closed-source SaaS | ❌ | LlamaParse, etc. |

---

## How to Add a New Repo Dependency

1. Verify license compatibility against the matrix above.
2. Pin version in `pyproject.toml`.
3. Add a row to the relevant section above with the same columns.
4. If it's a runtime dep, add a smoke test to `tests/integration/test_deps.py`.
5. Update `CLAUDE.md` Tech Stack section.
6. Open a PR with rationale.

---
**End of REPOS.md**
