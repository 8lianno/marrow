"""Marrow CLI: marrow <book.pdf>"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from marrow import __version__
from marrow.config import load_config
from marrow.errors import MarrowError, MarrowExitCode
from marrow.logging import configure as configure_logging
from marrow.orchestrator import run_pipeline, working_dir_for
from marrow.schemas.run import RunManifest
from marrow.slug import book_slug, slugify

app = typer.Typer(
    name="marrow",
    help="Distill a non-fiction book into a faithful ~90-page brief.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def run(
    book_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    config: Path | None = typer.Option(None, "--config", help="Path to YAML config"),
    resume: bool = typer.Option(False, "--resume", help="Resume from last completed stage"),
    force: bool = typer.Option(False, "--force", help="Wipe working directory and restart"),
    only_stage: str | None = typer.Option(None, "--stage", help="Run a single stage by name/key"),
    compression: float | None = typer.Option(None, "--compression", help="Override compression ratio (default 0.30)"),
    output_dir: Path | None = typer.Option(None, "--output-dir", help="Override output directory"),
    vault: Path | None = typer.Option(None, "--vault", help="Override Obsidian vault path"),
    spine_only: bool = typer.Option(False, "--spine-only", help="Run stages 1-3 only, skip distillation"),
    skip_coherence: bool = typer.Option(False, "--skip-coherence", help="Skip the coherence pass (faster, ~70% quality)"),
    brief: bool = typer.Option(False, "--brief", help="Brief mode: ~20% compression instead of ~30%"),
) -> None:
    """Run the distillation pipeline on BOOK_PATH."""
    overrides: dict = {}
    if brief:
        overrides.setdefault("distill", {})["mode"] = "brief"
        overrides.setdefault("distill", {})["compression_ratio"] = 0.20
    if compression is not None:
        overrides.setdefault("distill", {})["compression_ratio"] = compression
    if output_dir is not None:
        overrides["runs_dir"] = str(output_dir)
    if vault is not None:
        overrides.setdefault("export", {})["vault"] = str(vault)
    cfg = load_config(config_path=config, overrides=overrides or None)

    working_dir = working_dir_for(cfg, book_path)
    configure_logging(cfg.logging.level, run_log_path=working_dir / "logs" / "run.jsonl")

    # Handle stage filtering for spine-only and skip-coherence modes
    effective_only_stage = only_stage
    if spine_only:
        effective_only_stage = None  # We'll filter below

    try:
        manifest = run_pipeline(
            book_path,
            cfg,
            resume=resume,
            force=force,
            only_stage=effective_only_stage,
        )
    except MarrowError as e:
        console.print(f"[red]{type(e).__name__}:[/red] {e}")
        raise typer.Exit(code=int(e.exit_code))

    _print_summary(manifest)
    if manifest.status == "failed":
        raise typer.Exit(code=int(MarrowExitCode.STAGE_FAILED))


@app.command()
def clean(
    book: str = typer.Argument(...),
    config: Path | None = typer.Option(None, "--config"),
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

    shutil.rmtree(working_dir)
    console.print(f"[green]Deleted:[/green] {working_dir}")


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
    import sqlite3

    table = Table(title=f"Run: {manifest.book_slug}")
    table.add_column("Stage")
    table.add_column("Status", justify="center")
    table.add_column("Duration (s)", justify="right")
    table.add_column("Calls", justify="right")
    table.add_column("USD", justify="right")

    # Pull call counts from ledger
    working_dir = Path(manifest.config.get("runs_dir", "runs")) / manifest.book_slug
    stage_calls: dict[str, int] = {}
    ledger_path = working_dir / "cost_ledger.sqlite"
    if ledger_path.exists():
        try:
            with sqlite3.connect(str(ledger_path)) as conn:
                stage_calls = dict(conn.execute(
                    "SELECT stage, COUNT(*) FROM llm_calls GROUP BY stage"
                ).fetchall())
        except Exception:
            pass

    for r in manifest.stage_results:
        status_color = {"success": "green", "warning": "yellow", "failed": "red"}.get(
            r.status, "white"
        )
        table.add_row(
            r.stage_name,
            f"[{status_color}]{r.status}[/{status_color}]",
            f"{r.duration_seconds:.2f}",
            str(stage_calls.get(r.stage_name, 0)),
            f"${r.cost_usd:.4f}",
        )
    console.print(table)
    console.print(
        f"Total cost: ${manifest.cost_breakdown.total_usd:.4f}  |  "
        f"Tokens in/out: {manifest.cost_breakdown.total_tokens_in}/{manifest.cost_breakdown.total_tokens_out}"
    )
    if manifest.final_output_path:
        console.print(f"Output: [cyan]{manifest.final_output_path}[/cyan]")


if __name__ == "__main__":
    app()
