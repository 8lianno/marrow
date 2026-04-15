"""Folder watcher: poll an input directory, run the pipeline, relocate briefs.

Processes one book at a time (pipelines are long and contend on GPU/disk).
Failures isolate to ``failed/`` so one bad book never blocks the queue.
Interrupted runs resume via ``_complete`` markers on the next tick.
"""

from __future__ import annotations

import shutil
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from marrow.config import MarrowConfig
from marrow.errors import MarrowError
from marrow.io import read_json, write_json
from marrow.logging import get_logger
from marrow.orchestrator import run_pipeline, working_dir_for
from marrow.schemas.run import RunManifest
from marrow.slug import book_slug

log = get_logger(__name__)

FAILED_SUBDIR = "failed"
PROCESSED_SUBDIR = "processed"
REPORT_FILENAME = "watch_report.json"

_SUCCESS_STATUSES: frozenset[str] = frozenset({"success", "partial"})


class WatchEvent(BaseModel):
    timestamp: datetime
    source_path: str
    slug: str
    status: str  # "success" | "failed"
    duration_seconds: float
    error: str | None = None
    brief_path: str | None = None
    evaluation_path: str | None = None


class WatchReport(BaseModel):
    events: list[WatchEvent] = Field(default_factory=list)


def discover_pending(input_dir: Path, extensions: list[str]) -> list[Path]:
    """Top-level files in input_dir matching extensions.

    Ignores dotfiles, subdirectories, and anything under ``failed/`` or
    ``processed/`` (those are managed by the watcher itself).
    """
    if not input_dir.exists():
        return []
    exts = {e.lower() for e in extensions}
    out: list[Path] = []
    for child in sorted(input_dir.iterdir()):
        if not child.is_file():
            continue
        if child.name.startswith("."):
            continue
        if child.suffix.lower() not in exts:
            continue
        out.append(child)
    return out


def _manifest_status(working_dir: Path) -> str | None:
    manifest_path = working_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = read_json(manifest_path, RunManifest)
    except Exception as e:  # corrupt / mid-write
        log.warning("watch_manifest_read_failed", path=str(manifest_path), error=str(e))
        return None
    return manifest.status


def _copy_artifacts(manifest: RunManifest, output_dir: Path) -> tuple[Path | None, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    brief_dest: Path | None = None
    eval_dest: Path | None = None
    if manifest.final_brief_path:
        src = Path(manifest.final_brief_path)
        if src.exists():
            brief_dest = output_dir / src.name
            shutil.copy2(src, brief_dest)
    if manifest.final_evaluation_path:
        src = Path(manifest.final_evaluation_path)
        if src.exists():
            eval_dest = output_dir / src.name
            shutil.copy2(src, eval_dest)
    return brief_dest, eval_dest


def _relocate_source(source: Path, input_dir: Path, subdir: str) -> Path:
    target_dir = input_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if target.exists():
        stem, suffix = target.stem, target.suffix
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"{stem}.{ts}{suffix}"
    shutil.move(str(source), str(target))
    return target


def append_report(output_dir: Path, event: WatchEvent) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_FILENAME
    if report_path.exists():
        try:
            report = read_json(report_path, WatchReport)
        except Exception:
            report = WatchReport()
    else:
        report = WatchReport()
    report.events.append(event)
    write_json(report_path, report)


def process_one(book_path: Path, cfg: MarrowConfig) -> WatchEvent:
    """Run (or resume) the pipeline for one book. Always returns; never raises.

    Relocates the source file to ``processed/`` on success or ``failed/`` on
    error, copies artifacts to ``cfg.monitor.output_dir``, and appends to
    ``watch_report.json``.
    """
    assert cfg.monitor.input_dir and cfg.monitor.output_dir, (
        "monitor.input_dir and monitor.output_dir must be configured"
    )
    input_dir = Path(cfg.monitor.input_dir)
    output_dir = Path(cfg.monitor.output_dir)
    slug = book_slug(book_path)
    started = time.perf_counter()
    timestamp = datetime.now(UTC)

    try:
        working_dir = working_dir_for(cfg, book_path)
        prior_status = _manifest_status(working_dir)

        if prior_status in _SUCCESS_STATUSES:
            log.info("watch_run_already_complete", slug=slug, status=prior_status)
            manifest = read_json(working_dir / "manifest.json", RunManifest)
        else:
            log.info("watch_run_starting", slug=slug, resume=prior_status == "in_progress")
            manifest = run_pipeline(book_path, cfg, resume=True)

        if manifest.status not in _SUCCESS_STATUSES:
            raise MarrowError(f"pipeline status={manifest.status!r}")

        brief_dest, eval_dest = _copy_artifacts(manifest, output_dir)
        _relocate_source(book_path, input_dir, PROCESSED_SUBDIR)
        event = WatchEvent(
            timestamp=timestamp,
            source_path=str(book_path),
            slug=slug,
            status="success",
            duration_seconds=time.perf_counter() - started,
            brief_path=str(brief_dest) if brief_dest else None,
            evaluation_path=str(eval_dest) if eval_dest else None,
        )
    except Exception as e:
        log.error("watch_run_failed", slug=slug, error=str(e))
        try:
            if book_path.exists():
                _relocate_source(book_path, input_dir, FAILED_SUBDIR)
        except Exception as move_err:
            log.error("watch_relocate_failed", slug=slug, error=str(move_err))
        event = WatchEvent(
            timestamp=timestamp,
            source_path=str(book_path),
            slug=slug,
            status="failed",
            duration_seconds=time.perf_counter() - started,
            error=str(e),
        )

    append_report(output_dir, event)
    return event


def run_watch(cfg: MarrowConfig, once: bool = False) -> list[WatchEvent]:
    """Main polling loop. Returns the events processed in this invocation.

    ``once=True`` processes the current input-dir contents exactly once and
    returns — used by tests and for manual one-shot runs.
    """
    if not cfg.monitor.input_dir or not cfg.monitor.output_dir:
        raise MarrowError("monitor.input_dir and monitor.output_dir must be configured")

    input_dir = Path(cfg.monitor.input_dir)
    output_dir = Path(cfg.monitor.output_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    stop = {"flag": False}

    def _handle(_signum: int, _frame: object) -> None:
        stop["flag"] = True

    prior_int = signal.signal(signal.SIGINT, _handle)
    prior_term = signal.signal(signal.SIGTERM, _handle)

    events: list[WatchEvent] = []
    try:
        while not stop["flag"]:
            pending = discover_pending(input_dir, cfg.monitor.supported_extensions)
            for path in pending:
                if stop["flag"]:
                    break
                events.append(process_one(path, cfg))
            if once:
                break
            time.sleep(cfg.monitor.poll_interval_seconds)
    finally:
        signal.signal(signal.SIGINT, prior_int)
        signal.signal(signal.SIGTERM, prior_term)

    return events
