"""Marrow CLI: marrow run / status / clean / next."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from marrow import __version__
from marrow.config import MarrowConfig, load_config
from marrow.errors import MarrowError, MarrowExitCode
from marrow.host import (
    claim_task_batch,
    claimable_task_paths,
    detect_host_info,
    recommended_parallelism,
    submit_host_result,
    task_counts,
    task_payload,
)
from marrow.io import read_json
from marrow.logging import configure as configure_logging
from marrow.orchestrator import discover_stages, is_complete, run_pipeline, working_dir_for
from marrow.progress import reset_current, select_reporter, set_current
from marrow.schemas.run import HostResult, HostTask, RunManifest
from marrow.slug import book_slug, slugify
from marrow.watch import run_watch

app = typer.Typer(
    name="marrow",
    help="Lossless book-to-brief pipeline. Compress a 300-page book into a 50-page conceptual brief.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _resolve_config(
    config_path: Path | None,
    mode: str | None,
    cost_cap: float | None,
    vault: Path | None,
) -> MarrowConfig:
    overrides: dict = {}
    if mode is not None:
        overrides["mode"] = mode
    if cost_cap is not None:
        overrides["cost"] = {"max_per_book": cost_cap}
    if vault is not None:
        overrides["export"] = {"vault": str(vault)}
    cfg = load_config(config_path=config_path, overrides=overrides)
    return cfg


@app.command()
def run(
    book_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    mode: str | None = typer.Option(None, "--mode", help="host | api"),
    config: Path | None = typer.Option(None, "--config", help="Path to YAML config"),
    resume: bool = typer.Option(False, "--resume", help="Resume from last completed stage"),
    force: bool = typer.Option(False, "--force", help="Wipe working directory and restart"),
    only_stage: str | None = typer.Option(None, "--stage", help="Run a single stage by name/key"),
    cost_cap: float | None = typer.Option(None, "--cost-cap", help="Override max_per_book USD"),
    vault: Path | None = typer.Option(None, "--vault", help="Override Obsidian vault path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print stage plan and exit"),
    no_progress: bool = typer.Option(
        False, "--no-progress", help="Disable progress bars / stage-boundary lines"
    ),
) -> None:
    """Run the full pipeline on BOOK_PATH."""
    cfg = _resolve_config(config, mode, cost_cap, vault)
    working_dir = working_dir_for(cfg, book_path)
    configure_logging(cfg.logging.level, run_log_path=working_dir / "logs" / "run.jsonl")

    reporter = select_reporter(mode=cfg.mode, no_progress=no_progress)
    token = set_current(reporter)
    try:
        try:
            manifest = run_pipeline(
                book_path,
                cfg,
                resume=resume,
                force=force,
                only_stage=only_stage,
                dry_run=dry_run,
            )
        except MarrowError as e:
            console.print(f"[red]{type(e).__name__}:[/red] {e}")
            raise typer.Exit(code=int(e.exit_code))
    finally:
        reset_current(token)

    _print_summary(manifest)
    if manifest.status == "failed":
        raise typer.Exit(code=int(MarrowExitCode.STAGE_FAILED))


@app.command()
def status(
    book: str = typer.Argument(..., help="Book slug or path"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Show stage completion for a run, including the active stage if one is in progress."""
    import time

    cfg = load_config(config_path=config)
    slug = _book_to_slug(book)
    working_dir = Path(cfg.runs_dir) / slug
    if not working_dir.exists():
        console.print(f"[yellow]No run found for slug:[/yellow] {slug}")
        raise typer.Exit(code=int(MarrowExitCode.INPUT_NOT_FOUND))

    # Per-stage task counts for Host Mode runs.
    from marrow.host import task_counts_by_stage

    per_stage_tasks = task_counts_by_stage(working_dir, cfg.host)

    table = Table(title=f"Run status: {slug}")
    table.add_column("Stage")
    table.add_column("State", justify="center")
    table.add_column("Duration (s)", justify="right")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Tasks (done/total)", justify="right")

    for stage in discover_stages():
        complete = is_complete(working_dir, stage)
        stage_dir = working_dir / stage.dirname
        result_path = stage_dir / "result.json"
        dur, cost = "-", "-"
        state_cell = "[dim]—[/dim]"

        if complete and result_path.exists():
            from marrow.schemas.run import StageResult

            r = read_json(result_path, StageResult)
            dur = f"{r.duration_seconds:.2f}"
            cost = f"{r.cost_usd:.4f}"
            state_cell = "[green]✓[/green]"
        elif stage_dir.exists():
            # Active stage: directory exists but no _complete marker.
            elapsed = time.time() - stage_dir.stat().st_mtime
            dur = f"{elapsed:.0f} (active)"
            state_cell = "[yellow]⏳[/yellow]"

        tasks = per_stage_tasks.get(stage.dirname)
        if tasks:
            tasks_cell = f"{tasks['completed']}/{tasks['total']}"
            if tasks.get("pending"):
                tasks_cell += f" ([yellow]{tasks['pending']} pending[/yellow])"
        else:
            tasks_cell = "-"

        table.add_row(stage.dirname, state_cell, dur, cost, tasks_cell)
    console.print(table)


@app.command()
def clean(
    book: str = typer.Argument(...),
    config: Path | None = typer.Option(None, "--config"),
    keep_export: bool = typer.Option(False, "--keep-export"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete the working directory for a book."""
    cfg = load_config(config_path=config)
    slug = _book_to_slug(book)
    working_dir = Path(cfg.runs_dir) / slug
    if not working_dir.exists():
        console.print(f"[yellow]No run found:[/yellow] {slug}")
        return

    if not yes:
        confirmed = typer.confirm(f"Delete {working_dir}?")
        if not confirmed:
            return

    if keep_export:
        export_dir = working_dir / "06b_export"
        if export_dir.exists():
            backup = working_dir.with_suffix(".export-only")
            shutil.move(str(export_dir), str(backup))
            shutil.rmtree(working_dir)
            shutil.move(str(backup), str(working_dir))
        else:
            shutil.rmtree(working_dir)
    else:
        shutil.rmtree(working_dir)
    console.print(f"[green]Deleted:[/green] {working_dir}")


@app.command(name="next")
def next_task(
    book: str = typer.Argument(..., help="Book slug or path"),
    config: Path | None = typer.Option(None, "--config"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Max tasks to return/claim"),
    claimer: str | None = typer.Option(None, "--claimer", help="Logical worker/agent name"),
    claim: bool = typer.Option(True, "--claim/--no-claim", help="Claim tasks for parallel workers"),
) -> None:
    """Return the next claimable host-mode task batch as JSON."""
    cfg = load_config(config_path=config)
    slug = _book_to_slug(book)
    working_dir = Path(cfg.runs_dir) / slug
    task_dir = working_dir / cfg.host.task_dir
    manifest = _read_manifest_if_present(working_dir)
    host_info = manifest.host_info if manifest and manifest.host_info else detect_host_info()
    batch_limit = limit or cfg.host.default_batch_size

    if not working_dir.exists():
        console.print(f"[yellow]No run found for slug:[/yellow] {slug}")
        raise typer.Exit(code=int(MarrowExitCode.INPUT_NOT_FOUND))
    if not task_dir.exists():
        _print_json(
            {
                "book_slug": slug,
                "status": "starting" if manifest else "not_found",
                "host_environment": host_info.environment,
                "counts": task_counts(working_dir, cfg.host),
                "tasks": [],
            }
        )
        return

    claimable = claimable_task_paths(working_dir, cfg.host)
    if not claimable:
        _print_json(
            {
                "book_slug": slug,
                "status": "complete"
                if (working_dir / "06b_export" / "_complete").exists()
                else "waiting",
                "host_environment": host_info.environment,
                "counts": task_counts(working_dir, cfg.host),
                "tasks": [],
            }
        )
        return

    first_task = read_json(claimable[0], HostTask)
    selected_stage = first_task.stage
    claim_name = claimer or f"{host_info.environment}:{host_info.session_id or 'session'}"

    if claim:
        claimed = claim_task_batch(
            working_dir,
            cfg.host,
            limit=batch_limit,
            claimer=claim_name,
            host_info=host_info,
            stage=selected_stage,
        )
        selected_tasks = claimed
    else:
        selected_tasks = [
            (read_json(task_path, HostTask), task_path)
            for task_path in claimable
            if read_json(task_path, HostTask).stage == selected_stage
        ][:batch_limit]

    payload = {
        "book_slug": slug,
        "status": "awaiting_host",
        "host_environment": host_info.environment,
        "stage": selected_stage,
        "recommended_parallelism": recommended_parallelism(
            cfg.host, selected_stage, len(selected_tasks)
        ),
        "counts": task_counts(working_dir, cfg.host),
        "claimer": claim_name if claim else None,
        "tasks": [
            task_payload(working_dir, cfg.host, task=task, task_path=task_path)
            for task, task_path in selected_tasks
        ],
    }
    _print_json(payload)


@app.command()
def tasks(
    book: str = typer.Argument(..., help="Book slug or path"),
    config: Path | None = typer.Option(None, "--config"),
    limit: int = typer.Option(50, "--limit", min=1, help="Max tasks to include in output"),
) -> None:
    """List current host-mode queue state as JSON."""
    cfg = load_config(config_path=config)
    slug = _book_to_slug(book)
    working_dir = Path(cfg.runs_dir) / slug
    if not working_dir.exists():
        console.print(f"[yellow]No run found for slug:[/yellow] {slug}")
        raise typer.Exit(code=int(MarrowExitCode.INPUT_NOT_FOUND))

    pending_paths = claimable_task_paths(working_dir, cfg.host)[:limit]
    payload = {
        "book_slug": slug,
        "status": "complete"
        if (working_dir / "06b_export" / "_complete").exists()
        else "in_progress",
        "counts": task_counts(working_dir, cfg.host),
        "tasks": [
            task_payload(
                working_dir,
                cfg.host,
                task=read_json(task_path, HostTask),
                task_path=task_path,
            )
            for task_path in pending_paths
        ],
    }
    _print_json(payload)


@app.command()
def submit(
    book: str = typer.Argument(..., help="Book slug or path"),
    task_id: str = typer.Argument(..., help="Task UUID"),
    result_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Submit a validated host result file for a specific task."""
    cfg = load_config(config_path=config)
    slug = _book_to_slug(book)
    working_dir = Path(cfg.runs_dir) / slug
    if not working_dir.exists():
        console.print(f"[yellow]No run found for slug:[/yellow] {slug}")
        raise typer.Exit(code=int(MarrowExitCode.INPUT_NOT_FOUND))

    host_result = read_json(result_path, HostResult)
    if str(host_result.task_id) != task_id:
        console.print(
            f"[red]Task/result mismatch:[/red] result contains {host_result.task_id}, expected {task_id}"
        )
        raise typer.Exit(code=int(MarrowExitCode.INVALID_INPUT))

    stored_path = submit_host_result(
        working_dir,
        cfg.host,
        task_id=task_id,
        host_result=host_result,
    )
    _print_json(
        {
            "status": "submitted",
            "book_slug": slug,
            "task_id": task_id,
            "stored_result_path": str(stored_path),
        }
    )


@app.command()
def watch(
    input_dir: Path | None = typer.Option(
        None,
        "--input",
        file_okay=False,
        help="Folder to monitor. Defaults to ./inbox (created if missing).",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output",
        file_okay=False,
        help="Folder for finished briefs. Defaults to ./briefs (created if missing).",
    ),
    mode: str | None = typer.Option(None, "--mode", help="host | api"),
    config: Path | None = typer.Option(None, "--config", help="Path to YAML config"),
    cost_cap: float | None = typer.Option(None, "--cost-cap", help="Override max_per_book USD"),
    vault: Path | None = typer.Option(None, "--vault", help="Override Obsidian vault path"),
    poll_interval: float | None = typer.Option(
        None, "--poll-interval", help="Seconds between scans (default 5.0)"
    ),
    once: bool = typer.Option(
        False, "--once", help="Process current contents once and exit (no polling loop)"
    ),
) -> None:
    """Monitor an input folder; run the pipeline on dropped books; deliver briefs.

    With no flags, watches ./inbox and delivers to ./briefs (both auto-created).
    Successful runs land in the output folder; the source moves to <input>/processed/.
    Failed runs move the source to <input>/failed/ so the queue keeps flowing.
    Interrupted runs resume on the next tick. Run one watcher per folder.
    """
    cfg = _resolve_config(config, mode, cost_cap, vault)
    if input_dir is not None:
        cfg.monitor.input_dir = str(input_dir)
    if output_dir is not None:
        cfg.monitor.output_dir = str(output_dir)
    if poll_interval is not None:
        cfg.monitor.poll_interval_seconds = poll_interval

    resolved_input = Path(cfg.monitor.input_dir).resolve()
    resolved_output = Path(cfg.monitor.output_dir).resolve()
    resolved_input.mkdir(parents=True, exist_ok=True)
    resolved_output.mkdir(parents=True, exist_ok=True)
    cfg.monitor.input_dir = str(resolved_input)
    cfg.monitor.output_dir = str(resolved_output)

    configure_logging(cfg.logging.level, run_log_path=resolved_output / "logs" / "watch.jsonl")
    console.print(
        f"[green]marrow watch[/green] input=[cyan]{resolved_input}[/cyan] "
        f"output=[cyan]{resolved_output}[/cyan] once={once}"
    )
    try:
        events = run_watch(cfg, once=once)
    except MarrowError as e:
        console.print(f"[red]{type(e).__name__}:[/red] {e}")
        raise typer.Exit(code=int(e.exit_code))

    if once:
        succeeded = sum(1 for e in events if e.status == "success")
        failed = sum(1 for e in events if e.status == "failed")
        console.print(f"[green]done[/green] processed={len(events)} ok={succeeded} failed={failed}")


@app.command()
def version() -> None:
    """Print Marrow version."""
    console.print(f"marrow {__version__}")


# ---- helpers ----


def _book_to_slug(book: str) -> str:
    p = Path(book)
    if p.exists() and not p.is_dir():
        return book_slug(p)
    return slugify(book)


def _print_summary(manifest: RunManifest) -> None:
    table = Table(title=f"Run: {manifest.book_slug} ({manifest.mode})")
    table.add_column("Stage")
    table.add_column("Status", justify="center")
    table.add_column("Duration (s)", justify="right")
    table.add_column("Cost (USD)", justify="right")
    for r in manifest.stage_results:
        status_color = {"success": "green", "warning": "yellow", "failed": "red"}.get(
            r.status, "white"
        )
        table.add_row(
            r.stage_name,
            f"[{status_color}]{r.status}[/{status_color}]",
            f"{r.duration_seconds:.2f}",
            f"{r.cost_usd:.4f}",
        )
    console.print(table)
    console.print(
        f"Total cost: ${manifest.cost_breakdown.total_usd:.4f}  |  "
        f"Tokens in/out: {manifest.cost_breakdown.total_tokens_in}/{manifest.cost_breakdown.total_tokens_out}"
    )
    if manifest.final_brief_path:
        console.print(f"Brief: [cyan]{manifest.final_brief_path}[/cyan]")


def _print_json(payload: dict[str, object]) -> None:
    typer.echo(json.dumps(payload, sort_keys=True))


def _read_manifest_if_present(working_dir: Path) -> RunManifest | None:
    manifest_path = working_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    return read_json(manifest_path, RunManifest)


if __name__ == "__main__":
    app()
