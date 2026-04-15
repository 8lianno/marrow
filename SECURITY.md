# Security Policy

## Supported versions

Only the latest minor release on `main` receives security fixes. Marrow is
pre-1.0; older tags are not patched.

| Version | Supported |
|---|---|
| 0.1.x (latest on `main`) | ✅ |
| Earlier | ❌ |

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Prefer GitHub's private vulnerability reporting:
https://github.com/8lianno/marrow/security/advisories/new

If that's unavailable, email the maintainer by opening a minimal public issue
asking for a private contact address — do not include vulnerability details in
the public issue.

Please include:
- Affected file or command
- Reproduction steps (smallest possible example)
- Impact (what an attacker could do)
- Suggested fix if you have one

Expect an initial response within 7 days. Severe issues (arbitrary code
execution, credential leakage, data loss from a valid invocation) are
prioritized.

## Scope

### In scope

- The Marrow Python package under [`src/marrow/`](src/marrow/).
- The CLI entry point (`marrow run`, `status`, `clean`, `next`, `submit`,
  `tasks`, `version`).
- The LLM wrapper ([`src/marrow/llm.py`](src/marrow/llm.py)) — API-key
  handling, provider routing, cost-ledger integrity.
- The Host Mode task/result file protocol under
  [`runs/<slug>/host_tasks/`](HOST_MODE.md) and `host_results/`.
- Config loading ([`src/marrow/config.py`](src/marrow/config.py)) — YAML
  parsing, environment-variable override precedence, path traversal in
  `MARROW_RUNS_DIR` and `MARROW_OBSIDIAN_VAULT`.
- The Claude Code skill at
  [`skills/claude-code/marrow/SKILL.md`](skills/claude-code/marrow/SKILL.md).

### Out of scope

- **Book content you feed to Marrow.** Marrow parses PDFs and EPUBs via
  Docling; any code-execution vulnerability in Docling itself should be
  reported upstream. Marrow is a consumer, not a PDF security boundary.
- **LLM output quality.** Marrow's lossless gate (HAMLET + SummQ + FActScore)
  catches *completeness* failures, not *safety* failures. A jailbroken model
  that produces unsafe content in a `body_md` is the model's problem, not
  Marrow's.
- **Your host agent's credentials.** In Host Mode, Marrow does not read API
  keys. In API Mode, Marrow reads keys from environment variables you set;
  key hygiene (env files, shell history, VCS ignores) is your responsibility.
- **Upstream dependencies.** Issues in Docling, Jina, LanceDB, Ollama,
  PyTorch, NetworkX, Pydantic, and Anthropic/Gemini/OpenRouter SDKs should
  be reported to those projects. We pin versions in `pyproject.toml` and
  will upgrade when upstream fixes land.

## Known design decisions with security implications

These are intentional tradeoffs — not vulnerabilities — but are worth
knowing about:

1. **Prompt injection on book content is assumed.** Marrow renders book
   text into LLM prompts during claim extraction, graph extraction, and
   synthesis. A malicious PDF could attempt to manipulate the LLM's
   output. Consequences are bounded by the schema-validated response
   types — an injection can only alter *what* Marrow records, not make
   Marrow execute arbitrary code. The lossless gate's fact verification
   pass (FActScore) will still flag fabricated claims.

2. **Host-task files are trusted.** In Host Mode, Marrow reads
   `host_results/*.json` files written by the host agent. A malicious
   actor with write access to the working directory could inject crafted
   responses. This is acceptable because the working directory is
   per-user, under the user's home. Shared-filesystem deployments
   (NFS, shared runners) are not supported.

3. **API keys are read from environment variables.** API Mode resolves
   provider credentials via `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
   `OPENROUTER_API_KEY`, and `JINA_API_KEY`. Keys are redacted from
   Marrow's own logs via the `structlog` redactor in
   [`src/marrow/logging.py`](src/marrow/logging.py), but upstream SDKs
   may log independently.

4. **Determinism is load-bearing.** UUID5 content addressing and
   `temperature=0.0` mean the same input produces the same output IDs.
   A consequence: if your source changes by one character, chunk UUIDs
   change, and existing briefs no longer resolve via their citation
   anchors. This is a correctness feature, not a vulnerability.

5. **No network calls in Host Mode.** `--mode host` makes zero outbound
   LLM API calls from Marrow's subprocess. Docling may still download
   layout and table models on first run, and Jina v2 (if used) downloads
   model weights. Those downloads happen in API Mode or during stage 01
   / 02 execution in any mode.

## Disclosure policy

Once a reported vulnerability is patched on `main`, we:

1. Issue a new patch release with the fix.
2. Publish a GitHub security advisory with CVE assignment where
   appropriate.
3. Credit the reporter in the advisory unless they request otherwise.
