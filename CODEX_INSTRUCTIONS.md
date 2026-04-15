# Codex Instructions — Run Marrow on an Inbox Book

Drop any book into `inbox/`, start the watcher, then paste the prompt below into
Codex. It works for any book — no filenames or slugs to edit by hand.

## Step 1 — Start the watcher (Terminal 1)

```bash
cd /Users/ali/personal/Marrow
.venv/bin/marrow watch --config configs/express.yaml
```

Leave it running. It picks up any new `.pdf` or `.epub` dropped into `inbox/`,
kicks off the pipeline in host mode, and writes reasoning tasks to
`runs/<slug>/host_tasks/` for Codex to process.

## Step 2 — Paste the prompt below into Codex (Terminal 2)

Open a fresh Codex session in this project, then copy-paste everything between
the lines into the first message.

---

```
You are driving Marrow's host-mode task queue to completion for whatever book
is currently being processed by the `marrow watch` daemon running in another
terminal. Do not ask me which book — discover it yourself.

## Environment
- Working directory: /Users/ali/personal/Marrow
- Marrow CLI:        .venv/bin/marrow
- Python:            .venv/bin/python

## Step A — Discover the active slug

Run this exactly, then use $SLUG for every `marrow` command below:

    SLUG=$(.venv/bin/python - <<'PY'
    from pathlib import Path
    import json, sys
    candidates = []
    for m in Path("runs").glob("*/manifest.json"):
        try:
            data = json.loads(m.read_text())
        except Exception:
            continue
        if data.get("status") in {"in_progress", "partial"}:
            candidates.append((m.stat().st_mtime, m.parent.name))
    if not candidates:
        # Fallback: derive from the single book in inbox/
        from marrow.slug import book_slug
        exts = {".pdf", ".epub"}
        books = [p for p in Path("inbox").iterdir()
                 if p.is_file() and p.suffix.lower() in exts]
        if not books:
            sys.exit("no active run and no book in inbox/")
        print(book_slug(books[0]))
    else:
        candidates.sort(reverse=True)
        print(candidates[0][1])
    PY
    )
    echo "Active slug: $SLUG"

If that command prints "no active run and no book in inbox/", stop and report
back. The user needs to either drop a book into `inbox/` or start the watcher.

## Step B — Read the playbook

Read skills/codex/marrow/SKILL.md end-to-end before claiming your first task.
It defines the exact contract for task files, HostResult envelopes, and the
quality bar. Follow it precisely.

## Step C — Loop until done

Repeat until `marrow next` returns `"status": "complete"`:

    1) Check state:
       .venv/bin/marrow status $SLUG

    2) Claim a batch:
       .venv/bin/marrow next $SLUG --limit 4 --claimer codex-parent

       The JSON response contains `recommended_parallelism` and a `tasks[]`
       array. If `tasks` is empty and `status` is "complete", you are done —
       exit the loop.

    3) For each task in `tasks[]`:
       a. Read the full task JSON at `task_path`. It includes:
          - `prompt`                 — the full reasoning prompt (read all of it)
          - `response_schema`        — JSON Schema your output must match exactly
          - `response_schema_name`   — human-readable schema name
          - `chunk_uuids`            — UUIDs to preserve verbatim in outputs
          - `quality_hints`          — what matters most for this specific call
       b. Produce a JSON object that validates against `response_schema`. Same
          keys, same types, same enum values. Echo every UUID the prompt gave
          you — never invent new ones.
       c. Wrap it in this HostResult envelope and write to `/tmp/<task-id>.json`:

          {
            "task_id": "<task-id>",
            "response": { ...your schema-matching JSON... },
            "estimated_tokens_in": <rough int>,
            "estimated_tokens_out": <rough int>,
            "model_id": "codex",
            "worker_id": "<a short worker name>",
            "host_environment": "codex",
            "completed_at": "<ISO-8601 UTC timestamp>"
          }

       d. Submit:
          .venv/bin/marrow submit $SLUG <task-id> /tmp/<task-id>.json

    4) When `recommended_parallelism > 1`, fan out — one sub-agent per task,
       end-to-end. Do not split a single task across multiple workers.

    5) Loop back to step 1.

## Hard rules

- Read every evidence block in every prompt. Do not skim, even when long.
- Preserve every UUID exactly. Never regenerate, never shorten.
- Match the declared schema exactly: same keys, same types, same enum values.
- If a task reappears on a later `marrow next` call, your previous result
  failed validation. Re-read the schema, fix the exact mismatch, resubmit.
  Do not improvise.
- One task, one worker, one HostResult. No splits.
- On an unrecoverable single-task error, log it and continue the loop —
  never crash the whole run over one bad task.

## When complete

The watcher automatically copies `<slug>_Brief.md` and `<slug>_Evaluation.md`
into ./briefs/ and moves the source book to ./inbox/processed/. Verify with:

    ls briefs/
    cat briefs/watch_report.json

Start now at Step A.
```

---

## Troubleshooting

**Codex stops with "no active run and no book in inbox/"**
Drop a `.pdf` or `.epub` into `inbox/` and make sure the watcher in Terminal 1
has had a tick (default 5s) to pick it up. Then re-run the prompt.

**Tasks keep re-appearing after you submit them**
Schema validation failed. Codex should re-read `response_schema` for that task
type and correct the exact field mismatch. The most common cause is an enum
value that doesn't match, or a missing required field.

**You want to watch progress from a third terminal**

```bash
# Replace <slug> with whatever the first `marrow status` call printed
watch -n 5 '.venv/bin/marrow status <slug>'
```

**You want to start over on a specific book**

```bash
.venv/bin/marrow clean <slug> --yes
# then drop the book back into inbox/
```

**You want to skip host mode entirely and run fully autonomous**
Use ollama instead of Codex — no manual task loop needed:

```bash
.venv/bin/marrow watch --config configs/ollama.yaml
```

Synthesis quality is lower than Codex/Sonnet but the watcher finishes the
book end-to-end with no human in the loop.
