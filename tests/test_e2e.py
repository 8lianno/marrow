"""End-to-end smoke: full pipeline runs in both modes, parity holds, citations round-trip."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from marrow.config import load_config
from marrow.orchestrator import run_pipeline, working_dir_for


def _run_in_mode(synthetic_pdf: Path, runs_dir: Path, mode: str) -> Path:
    config = load_config(overrides={"mode": mode, "runs_dir": str(runs_dir)})
    manifest = run_pipeline(synthetic_pdf, config, force=True)
    assert manifest.status in ("success", "partial"), f"mode={mode} failed: {manifest.status}"
    return working_dir_for(config, synthetic_pdf)


@pytest.mark.slow
def test_pipeline_runs_in_api_mode(synthetic_pdf: Path, runs_dir: Path) -> None:
    wd = _run_in_mode(synthetic_pdf, runs_dir, "api")
    _assert_export_artifacts(wd)


@pytest.mark.slow
def test_pipeline_runs_in_host_mode(synthetic_pdf: Path, runs_dir: Path) -> None:
    wd = _run_in_mode(synthetic_pdf, runs_dir, "host")
    _assert_export_artifacts(wd)


@pytest.mark.slow
def test_mode_parity_source_is_byte_identical(synthetic_pdf: Path, tmp_path: Path) -> None:
    api_wd = _run_in_mode(synthetic_pdf, tmp_path / "api_runs", "api")
    host_wd = _run_in_mode(synthetic_pdf, tmp_path / "host_runs", "host")

    api_source = (api_wd / "06b_export").glob("*_Source.md").__next__()
    host_source = (host_wd / "06b_export").glob("*_Source.md").__next__()

    api_hash = hashlib.sha256(api_source.read_bytes()).hexdigest()
    host_hash = hashlib.sha256(host_source.read_bytes()).hexdigest()
    assert api_hash == host_hash, "Source.md must be byte-identical across modes"


@pytest.mark.slow
def test_determinism_repeat_run_same_uuids(synthetic_pdf: Path, tmp_path: Path) -> None:
    wd1 = _run_in_mode(synthetic_pdf, tmp_path / "run1", "api")
    wd2 = _run_in_mode(synthetic_pdf, tmp_path / "run2", "api")

    src1 = (wd1 / "06b_export").glob("*_Source.md").__next__().read_text()
    src2 = (wd2 / "06b_export").glob("*_Source.md").__next__().read_text()

    anchors1 = sorted(re.findall(r"\^([0-9a-fA-F-]{36})", src1))
    anchors2 = sorted(re.findall(r"\^([0-9a-fA-F-]{36})", src2))
    assert anchors1 == anchors2, "Chunk anchors must be deterministic across runs"


@pytest.mark.slow
def test_citation_round_trip(synthetic_pdf: Path, runs_dir: Path) -> None:
    wd = _run_in_mode(synthetic_pdf, runs_dir, "api")
    source_path = next((wd / "06b_export").glob("*_Source.md"))
    brief_path = next((wd / "06b_export").glob("*_Brief.md"))

    source_anchors = set(re.findall(r"^\^([0-9a-fA-F-]{36})", source_path.read_text(), re.M))
    brief_links = set(re.findall(r"\[\[[^\]#]+#\^([0-9a-fA-F-]{36})\]\]", brief_path.read_text()))

    assert brief_links, "Brief must contain at least one citation"
    unresolved = brief_links - source_anchors
    assert not unresolved, f"Brief citations missing from Source: {unresolved}"


def _assert_export_artifacts(wd: Path) -> None:
    export_dir = wd / "06b_export"
    assert export_dir.exists()
    sources = list(export_dir.glob("*_Source.md"))
    briefs = list(export_dir.glob("*_Brief.md"))
    evals = list(export_dir.glob("*_Evaluation.md"))
    assert sources and briefs and evals, "All three export files must exist"
    assert (export_dir / "_complete").exists()
