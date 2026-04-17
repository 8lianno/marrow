"""Stage discovery and pipeline orchestration."""

from __future__ import annotations

from marrow.orchestrator import discover_stages


def test_stage_discovery_returns_v2_stages() -> None:
    stages = discover_stages()
    names = [s.dirname for s in stages]
    assert names == [
        "01_ingest",
        "02_classify",
        "03_spine",
        "04_distill",
        "05_coherence",
    ]
