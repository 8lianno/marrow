"""Stage discovery, resume semantics, mode lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from marrow.config import load_config
from marrow.errors import ModeLockViolation
from marrow.orchestrator import discover_stages, run_pipeline


def test_stage_discovery_returns_all_stages() -> None:
    stages = discover_stages()
    names = [s.dirname for s in stages]
    assert names == [
        "01_ingest",
        "02_chunk",
        "03_graph",
        "04_claims",
        "05_synthesize",
        "05b_validate",
        "06a_evaluate",
        "06b_export",
    ]


@pytest.mark.slow
def test_resume_skips_complete_stages(synthetic_pdf: Path, tmp_path: Path) -> None:
    cfg = load_config(overrides={"mode": "api", "runs_dir": str(tmp_path / "runs")})
    m1 = run_pipeline(synthetic_pdf, cfg, force=True)
    m2 = run_pipeline(synthetic_pdf, cfg, resume=True)
    # Resume should produce same number of stage results, all skipped or re-validated.
    assert len(m1.stage_results) == len(m2.stage_results)


@pytest.mark.slow
def test_mode_lock_violation(synthetic_pdf: Path, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    cfg_api = load_config(overrides={"mode": "api", "runs_dir": str(runs)})
    cfg_host = load_config(overrides={"mode": "host", "runs_dir": str(runs)})
    run_pipeline(synthetic_pdf, cfg_api, force=True)
    with pytest.raises(ModeLockViolation):
        run_pipeline(synthetic_pdf, cfg_host, resume=True)
