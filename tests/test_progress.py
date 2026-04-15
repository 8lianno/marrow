"""Progress reporter behavior + contextvar wiring."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from marrow.config import HostConfig
from marrow.host import task_counts_by_stage
from marrow.io import write_json
from marrow.progress import (
    NullProgressReporter,
    PlainProgressReporter,
    RichProgressReporter,
    current,
    reset_current,
    select_reporter,
    set_current,
)
from marrow.schemas.run import HostTask

# ---- NullProgressReporter ----


def test_null_reporter_is_total_no_op() -> None:
    r = NullProgressReporter()
    # None of these should raise or return anything.
    assert r.pipeline_start(8) is None
    assert r.stage_start("01_ingest", 10, "page") is None
    assert r.stage_advance(1) is None
    assert r.stage_extend(5) is None
    assert r.stage_log("hello") is None
    assert r.stage_end("01_ingest", "success", 1.5) is None
    assert r.pipeline_end() is None


# ---- PlainProgressReporter ----


def test_plain_reporter_writes_expected_lines() -> None:
    buf = io.StringIO()
    r = PlainProgressReporter(stream=buf, total_stages=2)
    r.pipeline_start(2)
    r.stage_start("01_ingest", 3, "page")
    r.stage_advance(1)
    r.stage_advance(2)
    r.stage_end("01_ingest", "success", 1.2)
    r.stage_start("02_chunk", None, "paragraph")
    r.stage_advance(5)
    r.stage_end("02_chunk", "warning", 0.8)
    r.pipeline_end()

    out = buf.getvalue()
    assert "pipeline start (2 stages)" in out
    assert "[marrow 01/02] 01_ingest: starting (3 page)" in out
    assert "[marrow 01/02] 01_ingest: success (3/3 page, 1.2s)" in out
    # Indeterminate total → "?" in the final line.
    assert "[marrow 02/02] 02_chunk: warning (5/? paragraph, 0.8s)" in out
    assert "pipeline done" in out


def test_plain_reporter_stage_extend_increases_total() -> None:
    buf = io.StringIO()
    r = PlainProgressReporter(stream=buf, total_stages=1)
    r.pipeline_start(1)
    r.stage_start("03_graph", 6, "chunk")
    r.stage_advance(6)
    r.stage_extend(3)  # community summaries phase
    r.stage_advance(3)
    r.stage_end("03_graph", "success", 2.0)
    r.pipeline_end()

    assert "9/9 chunk" in buf.getvalue()


def test_plain_reporter_stage_log_writes_in_stage_context() -> None:
    buf = io.StringIO()
    r = PlainProgressReporter(stream=buf, total_stages=1)
    r.pipeline_start(1)
    r.stage_start("05b_validate", 10, "question")
    r.stage_log("iter 1: pass_rate=0.82")
    r.stage_end("05b_validate", "success", 3.0)
    r.pipeline_end()

    assert "05b_validate: iter 1: pass_rate=0.82" in buf.getvalue()


# ---- RichProgressReporter ----


def test_rich_reporter_constructs_and_advances_without_error() -> None:
    # We don't assert rendered bytes (Rich output is environment-sensitive),
    # only that the object lifecycle doesn't raise.
    r = RichProgressReporter()
    r.pipeline_start(3)
    r.stage_start("01_ingest", 5, "page")
    r.stage_advance(2)
    r.stage_extend(3)
    r.stage_advance(6)
    r.stage_log("mid-stage note")
    r.stage_end("01_ingest", "success", 1.0)
    r.stage_start("02_chunk", None, "paragraph")  # indeterminate
    r.stage_advance(10)
    r.stage_end("02_chunk", "warning", 2.0)
    r.pipeline_end()


# ---- ContextVar wiring ----


def test_current_defaults_to_null_reporter() -> None:
    # No set_current has run in this test — default should be Null.
    r = current()
    assert isinstance(r, NullProgressReporter)


def test_set_and_reset_current_swaps_reporter() -> None:
    sentinel = PlainProgressReporter(stream=io.StringIO())
    token = set_current(sentinel)
    try:
        assert current() is sentinel
    finally:
        reset_current(token)
    assert isinstance(current(), NullProgressReporter)


# ---- Selector ----


def test_select_reporter_returns_null_when_no_progress_flag_set() -> None:
    r = select_reporter(no_progress=True)
    assert isinstance(r, NullProgressReporter)


def test_select_reporter_returns_plain_when_stderr_not_tty(monkeypatch) -> None:
    # Force non-TTY to exercise the Plain branch deterministically.
    monkeypatch.setattr("sys.stderr.isatty", lambda: False, raising=False)
    r = select_reporter(no_progress=False)
    assert isinstance(r, PlainProgressReporter)


# ---- task_counts_by_stage ----


def _make_host_task(stage: str, task_id: UUID | None = None) -> HostTask:
    return HostTask(
        task_id=task_id or uuid4(),
        stage=stage,
        model_role="graph_extraction" if stage == "03_graph" else "claim_extraction",
        prompt="stub",
        max_input_tokens=8000,
        max_output_tokens=4000,
        created_at=datetime.now(UTC),
    )


def test_task_counts_by_stage_groups_correctly(tmp_path: Path) -> None:
    host_cfg = HostConfig()
    task_dir = tmp_path / host_cfg.task_dir
    result_dir = tmp_path / host_cfg.result_dir
    task_dir.mkdir()
    result_dir.mkdir()

    # 3 tasks in 03_graph, 2 done.
    t1 = _make_host_task("03_graph")
    t2 = _make_host_task("03_graph")
    t3 = _make_host_task("03_graph")
    # 2 tasks in 04_claims, 0 done.
    t4 = _make_host_task("04_claims")
    t5 = _make_host_task("04_claims")

    for t in [t1, t2, t3, t4, t5]:
        write_json(task_dir / f"{t.task_id}.json", t)

    # Mark t1 and t2 completed by writing matching result files.
    (result_dir / f"{t1.task_id}.json").write_text("{}")
    (result_dir / f"{t2.task_id}.json").write_text("{}")

    grouped = task_counts_by_stage(tmp_path, host_cfg)
    assert grouped["03_graph"] == {
        "pending": 1,
        "claimed": 0,
        "completed": 2,
        "total": 3,
    }
    assert grouped["04_claims"] == {
        "pending": 2,
        "claimed": 0,
        "completed": 0,
        "total": 2,
    }


def test_task_counts_by_stage_empty_when_no_tasks_dir(tmp_path: Path) -> None:
    host_cfg = HostConfig()
    assert task_counts_by_stage(tmp_path, host_cfg) == {}


# ---- Contextvar installed by CLI → stages see it ----


def test_stage_can_observe_installed_reporter() -> None:
    """A stage using `current()` sees whatever reporter the CLI installs."""
    buf = io.StringIO()
    installed = PlainProgressReporter(stream=buf, total_stages=1)
    token = set_current(installed)
    try:
        # Pretend we're inside a stage.
        reporter = current()
        reporter.pipeline_start(1)
        reporter.stage_start("fake_stage", 2, "item")
        reporter.stage_advance(2)
        reporter.stage_end("fake_stage", "success", 0.1)
        reporter.pipeline_end()
    finally:
        reset_current(token)

    output = buf.getvalue()
    assert "fake_stage: starting (2 item)" in output
    assert "fake_stage: success (2/2 item, 0.1s)" in output
