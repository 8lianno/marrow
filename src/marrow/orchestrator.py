"""Stage discovery, checkpointing, resume, and mode-lock enforcement."""

from __future__ import annotations

import importlib
import pkgutil
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from marrow.config import MarrowConfig
from marrow.errors import InputNotFound, ModeLockViolation, StageError
from marrow.io import read_json, write_json
from marrow.logging import get_logger
from marrow.schemas.run import RunManifest, StageResult
from marrow.slug import book_slug

log = get_logger(__name__)

STAGE_NAME_RE = re.compile(r"^stage_(\d+[a-z]?)_(\w+)$")


class StageRunner(Protocol):
    def __call__(self, working_dir: Path, config: MarrowConfig) -> StageResult: ...


class Stage:
    def __init__(self, key: str, name: str, dirname: str, run: StageRunner) -> None:
        self.key = key  # e.g. "01", "05b"
        self.name = name  # e.g. "ingest", "validate"
        self.dirname = dirname  # e.g. "01_ingest"
        self.run = run

    def __repr__(self) -> str:
        return f"Stage({self.dirname})"


def discover_stages() -> list[Stage]:
    """Discover stages by scanning marrow.stages.* for stage_NN(_x)_name modules."""
    import marrow.stages as stages_pkg

    stages: list[Stage] = []
    for _finder, mod_name, _ispkg in pkgutil.iter_modules(stages_pkg.__path__):
        m = STAGE_NAME_RE.match(mod_name)
        if not m:
            continue
        key, name = m.group(1), m.group(2)
        module = importlib.import_module(f"marrow.stages.{mod_name}")
        run_fn: Callable[[Path, MarrowConfig], StageResult] | None = getattr(module, "run", None)
        if run_fn is None:
            log.warning("stage_module_missing_run", module=mod_name)
            continue
        stages.append(Stage(key=key, name=name, dirname=f"{key}_{name}", run=run_fn))

    stages.sort(key=lambda s: (int(re.match(r"(\d+)", s.key).group(1)), s.key))  # type: ignore[union-attr]
    return stages


def working_dir_for(config: MarrowConfig, book_path: Path) -> Path:
    return Path(config.runs_dir) / book_slug(book_path)


def is_complete(working_dir: Path, stage: Stage) -> bool:
    return (working_dir / stage.dirname / "_complete").exists()


def mark_complete(working_dir: Path, stage: Stage) -> None:
    marker = working_dir / stage.dirname / "_complete"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now(UTC).isoformat() + "\n", encoding="utf-8")


def _enforce_mode_lock(working_dir: Path, config: MarrowConfig, force: bool) -> None:
    manifest_path = working_dir / "manifest.json"
    if not manifest_path.exists():
        return
    prior = read_json(manifest_path)
    prior_mode = prior.get("mode")
    if prior_mode and prior_mode != config.mode and not force:
        raise ModeLockViolation(
            f"Run was started in mode={prior_mode!r}; cannot resume in mode={config.mode!r}. "
            "Use --force to wipe the working directory and restart."
        )


def _write_initial_manifest(
    working_dir: Path, config: MarrowConfig, book_path: Path
) -> RunManifest:
    from marrow import __version__

    manifest = RunManifest(
        book_slug=book_slug(book_path),
        book_path=str(book_path.resolve()),
        mode=config.mode,
        started_at=datetime.now(UTC),
        status="in_progress",
        config=config.model_dump(by_alias=True),
        marrow_version=__version__,
    )
    write_json(working_dir / "manifest.json", manifest)
    return manifest


def _finalize_manifest(
    working_dir: Path,
    manifest: RunManifest,
    stage_results: list[StageResult],
    status: str,
) -> RunManifest:
    from marrow.store.ledger import CostLedger

    ledger = CostLedger(working_dir / "cost_ledger.sqlite")
    tokens_in, tokens_out = ledger.total_tokens()

    manifest.stage_results = stage_results
    manifest.cost_breakdown.by_stage = ledger.by_stage()
    manifest.cost_breakdown.by_model_role = ledger.by_model_role()
    manifest.cost_breakdown.total_usd = ledger.total_usd()
    manifest.cost_breakdown.total_tokens_in = tokens_in
    manifest.cost_breakdown.total_tokens_out = tokens_out
    manifest.completed_at = datetime.now(UTC)
    manifest.duration_seconds = (manifest.completed_at - manifest.started_at).total_seconds()
    manifest.status = status  # type: ignore[assignment]

    final_brief = working_dir / "06b_export"
    if final_brief.exists():
        for p in final_brief.glob("*_Brief.md"):
            manifest.final_brief_path = str(p)
            break
        for p in final_brief.glob("*_Evaluation.md"):
            manifest.final_evaluation_path = str(p)
            break

    write_json(working_dir / "manifest.json", manifest)
    return manifest


def run_pipeline(
    book_path: Path,
    config: MarrowConfig,
    *,
    resume: bool = False,
    force: bool = False,
    only_stage: str | None = None,
    dry_run: bool = False,
) -> RunManifest:
    if not book_path.exists():
        raise InputNotFound(f"Book not found: {book_path}")

    working_dir = working_dir_for(config, book_path)

    if force and working_dir.exists():
        log.warning("force_wiping_working_dir", path=str(working_dir))
        shutil.rmtree(working_dir)

    working_dir.mkdir(parents=True, exist_ok=True)
    _enforce_mode_lock(working_dir, config, force=force)

    stages = discover_stages()
    if not stages:
        raise StageError("orchestrator", "No stages discovered under marrow.stages")

    if dry_run:
        for s in stages:
            print(f"  {s.dirname}  ({'SKIP' if is_complete(working_dir, s) else 'RUN'})")
        return _write_initial_manifest(working_dir, config, book_path)

    manifest = _write_initial_manifest(working_dir, config, book_path)
    stage_results: list[StageResult] = []
    overall_status = "success"

    for stage in stages:
        if only_stage and only_stage not in (stage.name, stage.key, stage.dirname):
            continue
        if resume and is_complete(working_dir, stage):
            log.info("stage_skipped_already_complete", stage=stage.dirname)
            existing = working_dir / stage.dirname / "result.json"
            if existing.exists():
                stage_results.append(read_json(existing, StageResult))
            continue

        log.info("stage_starting", stage=stage.dirname)
        try:
            result = stage.run(working_dir, config)
        except Exception as e:
            log.error("stage_crashed", stage=stage.dirname, error=str(e))
            overall_status = "failed"
            _finalize_manifest(working_dir, manifest, stage_results, overall_status)
            raise StageError(stage.dirname, str(e)) from e

        write_json(working_dir / stage.dirname / "result.json", result)
        stage_results.append(result)

        if result.status == "failed":
            overall_status = "failed"
            log.error("stage_reported_failure", stage=stage.dirname, errors=result.errors)
            break
        if result.status == "warning":
            overall_status = "partial" if overall_status == "success" else overall_status

        mark_complete(working_dir, stage)
        log.info(
            "stage_complete",
            stage=stage.dirname,
            duration_s=result.duration_seconds,
            cost_usd=result.cost_usd,
        )

    return _finalize_manifest(working_dir, manifest, stage_results, overall_status)
