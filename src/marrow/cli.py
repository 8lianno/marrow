"""Marrow CLI: marrow run / status / clean / next."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from marrow import __version__
from marrow.config import MarrowConfig, load_config
from marrow.errors import MarrowError, MarrowExitCode
from marrow.io import read_json
from marrow.logging import configure as configure_logging
from marrow.orchestrator import discover_stages, is_complete, run_pipeline, working_dir_for
from marrow.schemas.run import RunManifest
from marrow.slug import book_slug, slugify

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
) -> None:
    """Run the full pipeline on BOOK_PATH."""
    cfg = _resolve_config(config, mode, cost_cap, vault)
    working_dir = working_dir_for(cfg, book_path)
    configure_logging(cfg.logging.level, run_log_path=working_dir / "logs" / "run.jsonl")

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

    _print_summary(manifest)
    if manifest.status == "failed":
        raise typer.Exit(code=int(MarrowExitCode.STAGE_FAILED))


@app.command()
def status(
    book: str = typer.Argument(..., help="Book slug or path"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Show stage completion for a run."""
    cfg = load_config(config_path=config)
    slug = _book_to_slug(book)
    working_dir = Path(cfg.runs_dir) / slug
    if not working_dir.exists():
        console.print(f"[yellow]No run found for slug:[/yellow] {slug}")
        raise typer.Exit(code=int(MarrowExitCode.INPUT_NOT_FOUND))

    table = Table(title=f"Run status: {slug}")
    table.add_column("Stage")
    table.add_column("State", justify="center")
    table.add_column("Duration (s)", justify="right")
    table.add_column("Cost (USD)", justify="right")

    for stage in discover_stages():
        complete = is_complete(working_dir, stage)
        result_path = working_dir / stage.dirname / "result.json"
        dur, cost = "-", "-"
        if result_path.exists():
            from marrow.schemas.run import StageResult

            r = read_json(result_path, StageResult)
            dur = f"{r.duration_seconds:.2f}"
            cost = f"{r.cost_usd:.4f}"
        table.add_row(
            stage.dirname,
            "[green]✓[/green]" if complete else "[dim]—[/dim]",
            dur,
            cost,
        )
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
) -> None:
    """Print the next pending host-mode task (host agent's loop pivot)."""
    cfg = load_config(config_path=config)
    slug = _book_to_slug(book)
    working_dir = Path(cfg.runs_dir) / slug
    task_dir = working_dir / cfg.host.task_dir
    result_dir = working_dir / cfg.host.result_dir

    if not task_dir.exists():
        console.print("[yellow]No host_tasks/ directory yet — has the run started?[/yellow]")
        raise typer.Exit(code=int(MarrowExitCode.INPUT_NOT_FOUND))

    pending = sorted(p for p in task_dir.glob("*.json") if not (result_dir / p.name).exists())
    if not pending:
        console.print("[green]No pending tasks.[/green]")
        return
    console.print_json(pending[0].read_text(encoding="utf-8"))


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


if __name__ == "__main__":
    app()
