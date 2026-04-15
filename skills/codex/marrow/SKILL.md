# Marrow Host Mode for Codex

Use this playbook when running Marrow in **host mode** from a Codex session. Marrow handles deterministic pipeline work; Codex handles every reasoning task by reading claimed task files and writing `HostResult` JSON back.

## Start or resume

```bash
# Fresh run
marrow run /path/to/book.pdf --mode host --force

# Resume
marrow run /path/to/book.pdf --mode host --resume
```

## Codex orchestration loop

1. Resolve the book slug with `marrow status <book-or-slug>` if needed.
2. Claim a batch from the active stage:

```bash
marrow next <slug> --limit 4 --claimer codex-parent
```

3. Read the returned JSON. It includes:
   - `stage`
   - `recommended_parallelism`
   - `tasks[]` with `task_path`, `result_path`, `response_schema_name`, `chunk_uuids`, and `quality_hints`
4. Spawn up to `recommended_parallelism` helper agents. Give each worker exactly one claimed task.
5. Each worker must:
   - Read the full task JSON at `task_path`
   - Read every evidence block in the prompt before drafting
   - Compare all relevant evidence before answering
   - Match the declared schema exactly
   - Write a temporary `HostResult` JSON locally
6. Submit each worker result:

```bash
marrow submit <slug> <task-id> /tmp/<task-id>.json
```

7. Repeat `marrow next` until it returns `status: "complete"`.

## Quality bar

- Do not skim. Read the entire task prompt.
- If the prompt includes multiple claims, communities, or evidence boxes, compare all of them before answering.
- One worker owns one claimed task end-to-end. Do not split a single task across multiple workers.
- Preserve citations and UUID strings exactly as provided.
- If a result fails validation and the task reappears, re-read the schema and fix the exact mismatch instead of improvising.

## Useful commands

```bash
marrow tasks <slug> --limit 50    # inspect queue state
marrow status <slug>              # stage-level progress
```
