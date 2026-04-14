---
name: marrow
description: Run Marrow's lossless book-to-brief pipeline inside Claude Code. You become the reasoning engine — Marrow writes task files, you answer them, no API keys needed. Invoke with `/marrow <book-path>` or `/marrow <book-slug>` to resume.
---

# Marrow Host-Mode Skill

When the user invokes `/marrow`, you drive the pipeline. Marrow handles parsing, chunking, embedding, graph construction, and file management. **You** handle every LLM call by reading task JSON files from disk and writing response JSON back.

Zero API keys. Zero metered cost. Your subscription tokens are the only compute.

## The mental model in one paragraph

Marrow's stages 03/04/05/05b/06a each need LLM reasoning. In host mode, instead of calling an API, each call writes a `HostTask` JSON to `runs/<slug>/host_tasks/<task-id>.json` and polls for a matching `runs/<slug>/host_results/<task-id>.json`. Your job is to pop tasks and write results until the run completes.

## Arguments

- `$1` (required): either a book path like `./books/art-of-war.pdf` or an existing book slug like `art-of-war` (to resume an in-progress run).
- `$2` (optional): path to a config yaml (`configs/default.yaml` by default).

## Execution

1. **Resolve slug.** If `$1` is a file path, compute the slug via `marrow status` or derive it manually: lowercase + replace non-alphanumeric with `-`, strip trailing `-`. If `$1` is already a slug, use it as-is.

2. **Start Marrow in the background** (only if the run isn't already going):
   ```
   Bash (run_in_background=true): marrow run "$1" --mode host --force
   ```
   Or, to resume a partially-complete run:
   ```
   Bash (run_in_background=true): marrow run "$1" --mode host --resume
   ```
   Capture the background task id.

3. **Enter the task loop.** Repeat until Marrow's background process exits:

   a. Scan `runs/<slug>/host_tasks/` for `*.json` files whose matching `runs/<slug>/host_results/<same-name>.json` does **not** exist. Pick the oldest (by filename / mtime).

   b. If nothing pending: wait 2 seconds, re-scan. After 3 empty scans in a row, check if the background process is still alive. If it has exited, break the loop.

   c. Read the task JSON with the `Read` tool. It has this shape:
      ```json
      {
        "task_id": "<uuid>",
        "stage": "04_claims",
        "model_role": "claim_extraction",
        "prompt": "<the full instructions you need>",
        "response_schema": { ... Pydantic JSON schema ... },
        "chunk_uuids": ["<uuid>", ...],
        "max_input_tokens": 8000,
        "max_output_tokens": 4000,
        "created_at": "<iso>"
      }
      ```

   d. **Reason about the prompt.** The prompt field contains a fully-rendered template with the rules, the source text, and the required JSON output shape already described. Follow it exactly — it is the single source of truth for what you should produce.

   e. **Construct the response.** The `response_schema` is the JSON Schema Pydantic will validate against. Match it field-for-field. If the schema has `"additionalProperties": false`, do not invent keys. Types matter: `float` means JSON number, `bool` means `true`/`false` (not strings), UUIDs are hyphenated 36-char strings.

   f. **Write the result** with the `Write` tool to `runs/<slug>/host_results/<task-id>.json`:
      ```json
      {
        "task_id": "<same uuid as the task>",
        "response": <your answer — either the object matching response_schema, or a plain string if no schema>,
        "estimated_tokens_in": <rough: len(prompt.split())>,
        "estimated_tokens_out": <rough: len(your_response_serialized.split())>,
        "completed_at": "<iso-utc-now>"
      }
      ```

   g. Loop to (a).

4. **On completion**: run `marrow status <slug>` and show the user the final table. The brief is at `runs/<slug>/06b_export/<slug>_Brief.md`, the evaluation at `<slug>_Evaluation.md`.

## Task types you'll see

Every task's `prompt` field has detailed instructions. This is a quick map so you know what's coming:

| `stage` | `model_role` | What you produce |
|---|---|---|
| `04_claims` | `claim_extraction` | `ExtractedClaimsResponse` — atomic claims with source spans |
| `03_graph` | `graph_extraction` | Either `ExtractedGraphResponse` (entities + relationships) or `CommunitySummaryResponse` (per-community title + summary). Disambiguate by the prompt. |
| `05_synthesize` | `synthesis` | `ChapterSynthesisResponse` — chapter body_md with `[chunk:UUID]` citations |
| `05b_validate` | `quiz_generation` | `GeneratedQuiz` — test questions for one chunk |
| `05b_validate` | `validation` | One of: `QuizAnswerResponse` (examinee), `QuizGrade` (grader) — disambiguate by the prompt |
| `06a_evaluate` | `validation` | One of: `CoherenceScore`, `FactVerification` — disambiguate by the prompt |

## Schema gotchas

- **UUIDs are strings** in JSON, hyphenated, 36 chars. Don't emit `UUID("...")` wrappers.
- **Enums are string literals.** If the schema says `"claim_type": "factual|definitional|..."`, emit one of those lowercase strings exactly.
- **min_length constraints**: `source_chunk_uuids` often has `min_length: 1`. Don't emit empty arrays.
- **Nested models**: the schema may include `$ref` — the referenced definition is in the same document's `$defs`.
- **Citations in synthesis output**: in `ChapterSynthesisResponse.body_md`, every substantive sentence must end with `[chunk:UUID]` tokens. Use UUIDs from the claims' `source_chunk_uuids`, verbatim.

## When to stop

Stop the loop when all three are true:
1. `runs/<slug>/host_tasks/` has no files without matching results.
2. The background `marrow run` process has exited (check via the Bash task id).
3. `runs/<slug>/06b_export/_complete` exists.

Then report the final paths to the user.

## Errors

- **Task parse failure**: write a result with `"response": null` so Marrow's validator records a failure and moves on — never leave a task unanswered.
- **Background process crashed**: surface the error. Run `cat runs/<slug>/logs/run.jsonl | tail -20` for context.
- **Schema validation loops**: if you see the same task appear more than 3 times (Marrow retries), your output is failing validation. Double-check types and required fields against `response_schema`.

## Monitoring

Between tasks, you can optionally run `marrow status <slug>` to show the user which stage is active and how many tasks have been processed. Don't do this every iteration — once every ~10 tasks is fine.

## Why this exists

Marrow's architectural principle: every LLM call goes through one wrapper. In API mode the wrapper calls Anthropic / Gemini / OpenRouter / Ollama. In host mode the wrapper writes a file and waits. You, the host agent, are the LLM. Same pipeline, same artifacts, same citation round-trip — zero API keys, zero metered cost.

This is the same protocol documented in `HOST_MODE.md`. When you extend Marrow or debug a run, treat the task/result JSON files as the contract.
