"""Progress reporting for long-running pipeline runs.

A `marrow run` on a real book is a 1-2 hour affair. This module gives the user
visibility into what the pipeline is doing without changing the stage contract.

Three reporter implementations:
- `RichProgressReporter` — live bars in a TTY (overall + current stage).
- `PlainProgressReporter` — one line per stage boundary on stderr; no ANSI.
- `NullProgressReporter` — silent; used in tests and when `--no-progress` is set.

Stages reach the active reporter through the `current()` accessor, which reads
from a `ContextVar`. Tests that don't touch the contextvar see the Null
reporter and get byte-identical artifacts as before.
"""

from __future__ import annotations

import sys
import time
from contextvars import ContextVar
from typing import Protocol, TextIO


class ProgressReporter(Protocol):
    """The surface every stage interacts with. Implementations are free to
    render bars, write plain lines, or do nothing."""

    def stage_start(self, stage_name: str, total: int | None, unit: str) -> None: ...

    def stage_advance(self, n: int = 1) -> None: ...

    def stage_extend(self, by: int) -> None:
        """Add `by` units to the current stage's total (for multi-phase stages)."""

    def stage_log(self, message: str) -> None:
        """Record a sub-iteration summary (e.g. 'iter 1 pass_rate=0.82')."""

    def stage_end(self, stage_name: str, status: str, duration_s: float) -> None: ...

    def pipeline_start(self, total_stages: int) -> None: ...

    def pipeline_end(self) -> None: ...


class NullProgressReporter:
    """No-op reporter. Used by tests and `--no-progress`."""

    def stage_start(self, stage_name: str, total: int | None, unit: str) -> None:
        return

    def stage_advance(self, n: int = 1) -> None:
        return

    def stage_extend(self, by: int) -> None:
        return

    def stage_log(self, message: str) -> None:
        return

    def stage_end(self, stage_name: str, status: str, duration_s: float) -> None:
        return

    def pipeline_start(self, total_stages: int) -> None:
        return

    def pipeline_end(self) -> None:
        return


class PlainProgressReporter:
    """Writes one line per stage boundary to a text stream (default stderr).

    Suitable for CI, redirected output, and anywhere ANSI escapes would be
    hostile. Does not emit per-unit updates — that would spam non-interactive
    logs. For per-unit visibility use `RichProgressReporter`.
    """

    def __init__(self, stream: TextIO | None = None, total_stages: int = 8) -> None:
        self._stream = stream or sys.stderr
        self._total_stages = total_stages
        self._stage_idx = 0
        self._stage_name: str | None = None
        self._stage_started: float | None = None
        self._stage_unit: str = "units"
        self._stage_total: int | None = None
        self._stage_done: int = 0

    def pipeline_start(self, total_stages: int) -> None:
        self._total_stages = total_stages
        self._stage_idx = 0
        print(f"[marrow] pipeline start ({total_stages} stages)", file=self._stream, flush=True)

    def stage_start(self, stage_name: str, total: int | None, unit: str) -> None:
        self._stage_idx += 1
        self._stage_name = stage_name
        self._stage_started = time.perf_counter()
        self._stage_unit = unit
        self._stage_total = total
        self._stage_done = 0
        total_str = str(total) if total is not None else "?"
        print(
            f"[marrow {self._stage_idx:02d}/{self._total_stages:02d}] "
            f"{stage_name}: starting ({total_str} {unit})",
            file=self._stream,
            flush=True,
        )

    def stage_advance(self, n: int = 1) -> None:
        self._stage_done += n

    def stage_extend(self, by: int) -> None:
        # Promote None → by so stages that start indeterminate and then learn
        # their size mid-run can still report a final "N/N" on stage_end.
        self._stage_total = (self._stage_total or 0) + by

    def stage_log(self, message: str) -> None:
        print(
            f"[marrow {self._stage_idx:02d}/{self._total_stages:02d}] "
            f"{self._stage_name}: {message}",
            file=self._stream,
            flush=True,
        )

    def stage_end(self, stage_name: str, status: str, duration_s: float) -> None:
        total_str = str(self._stage_total) if self._stage_total is not None else "?"
        print(
            f"[marrow {self._stage_idx:02d}/{self._total_stages:02d}] "
            f"{stage_name}: {status} "
            f"({self._stage_done}/{total_str} {self._stage_unit}, "
            f"{duration_s:.1f}s)",
            file=self._stream,
            flush=True,
        )

    def pipeline_end(self) -> None:
        print("[marrow] pipeline done", file=self._stream, flush=True)


class RichProgressReporter:
    """Live two-bar progress display using `rich.progress.Progress`.

    Outer bar: pipeline-level; advances one step per stage boundary.
    Inner bar: current stage; recreated per stage with the stage's unit total.
    Indeterminate phases (total=None) render as a pulsing bar.
    """

    def __init__(self) -> None:
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )

        self._console = Console(stderr=True)
        self._progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.description}[/bold]"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("[dim]{task.fields[unit]}[/dim]"),
            TimeElapsedColumn(),
            TimeRemainingColumn(compact=True),
            console=self._console,
            transient=False,
            refresh_per_second=8,
        )
        self._overall_task: int | None = None
        self._current_task: int | None = None
        self._current_stage_name: str | None = None

    def pipeline_start(self, total_stages: int) -> None:
        self._progress.start()
        self._overall_task = self._progress.add_task(
            description="Overall",
            total=total_stages,
            unit="stages",
        )

    def stage_start(self, stage_name: str, total: int | None, unit: str) -> None:
        if self._current_task is not None:
            self._progress.remove_task(self._current_task)
        self._current_stage_name = stage_name
        self._current_task = self._progress.add_task(
            description=stage_name,
            total=total,
            unit=unit,
        )

    def stage_advance(self, n: int = 1) -> None:
        if self._current_task is not None:
            self._progress.advance(self._current_task, n)

    def stage_extend(self, by: int) -> None:
        if self._current_task is None:
            return
        task = self._progress.tasks[self._progress.task_ids.index(self._current_task)]
        new_total = (task.total or 0) + by
        self._progress.update(self._current_task, total=new_total)

    def stage_log(self, message: str) -> None:
        self._progress.console.log(f"[dim][{self._current_stage_name}][/dim] {message}")

    def stage_end(self, stage_name: str, status: str, duration_s: float) -> None:
        if self._current_task is not None:
            # Mark 100% on completion so the bar doesn't look stuck.
            task = self._progress.tasks[self._progress.task_ids.index(self._current_task)]
            if task.total is not None and task.completed < task.total:
                self._progress.update(self._current_task, completed=task.total)
        if self._overall_task is not None:
            self._progress.advance(self._overall_task, 1)
        color = {"success": "green", "warning": "yellow", "failed": "red"}.get(status, "white")
        self._progress.console.log(
            f"[{color}]{status:>7}[/{color}] {stage_name} ({duration_s:.1f}s)"
        )

    def pipeline_end(self) -> None:
        self._progress.stop()


# ---- ContextVar-based accessor ----

# Null reporter is stateless (every method is a no-op returning None), so
# sharing a single instance across contexts is semantically equivalent to
# creating a fresh one each time.
_NULL_REPORTER = NullProgressReporter()

_current: ContextVar[ProgressReporter] = ContextVar(
    "marrow_progress", default=_NULL_REPORTER
)


def current() -> ProgressReporter:
    """Return the progress reporter for the current run/context."""
    return _current.get()


def set_current(reporter: ProgressReporter):
    """Install a reporter for the current context. Returns the Token so the
    caller can restore the previous reporter afterward."""
    return _current.set(reporter)


def reset_current(token) -> None:
    """Restore the reporter that was active before the matching `set_current`."""
    _current.reset(token)


def select_reporter(*, mode: str | None = None, no_progress: bool = False) -> ProgressReporter:
    """Pick the right reporter for the current environment.

    - `no_progress=True` → NullProgressReporter (explicit opt-out).
    - stderr is a TTY → RichProgressReporter (live bars).
    - otherwise → PlainProgressReporter (stage-boundary lines).

    The `mode` arg is accepted for future use (e.g. host mode may want a
    different default) but today does not influence the selection.
    """
    _ = mode
    if no_progress:
        return NullProgressReporter()
    if sys.stderr.isatty():
        try:
            return RichProgressReporter()
        except ImportError:  # rich should always be present, but be safe
            return PlainProgressReporter()
    return PlainProgressReporter()
