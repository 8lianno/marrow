"""US-013: folder-watcher tests (no real pipeline, all via monkeypatch)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from marrow.config import MarrowConfig, load_config
from marrow.errors import MarrowError, StageError
from marrow.io import read_json
from marrow.orchestrator import working_dir_for
from marrow.schemas.run import RunManifest
from marrow.slug import book_slug
from marrow.watch import (
    FAILED_SUBDIR,
    PROCESSED_SUBDIR,
    REPORT_FILENAME,
    WatchReport,
    discover_pending,
    process_one,
    run_watch,
)

# ---- Fixtures ----


def _cfg(tmp_path: Path, inbox: Path, briefs: Path) -> MarrowConfig:
    return load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path / "runs"),
            "monitor": {
                "input_dir": str(inbox),
                "output_dir": str(briefs),
                "poll_interval_seconds": 0.01,
            },
        }
    )


def _seed_book(inbox: Path, name: str = "book.pdf") -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def _fake_success_factory(
    cfg: MarrowConfig,
    *,
    write_artifacts: bool = True,
):
    """Build a replacement for run_pipeline that writes realistic outputs."""

    def fake(book_path: Path, config: MarrowConfig, **_kwargs: Any) -> RunManifest:
        slug = book_slug(book_path)
        working_dir = working_dir_for(cfg, book_path)
        export_dir = working_dir / "06b_export"
        export_dir.mkdir(parents=True, exist_ok=True)
        brief_path = export_dir / f"{slug}_Brief.md"
        eval_path = export_dir / f"{slug}_Evaluation.md"
        if write_artifacts:
            brief_path.write_text(f"# Brief for {slug}\n", encoding="utf-8")
            eval_path.write_text(f"# Eval for {slug}\n", encoding="utf-8")
        now = datetime.now(UTC)
        return RunManifest(
            book_slug=slug,
            book_path=str(book_path),
            mode="api",
            started_at=now,
            completed_at=now,
            duration_seconds=0.1,
            status="success",
            config={},
            marrow_version="test",
            final_brief_path=str(brief_path) if write_artifacts else None,
            final_evaluation_path=str(eval_path) if write_artifacts else None,
        )

    return fake


# ---- discover_pending ----


def test_discover_pending_ignores_subdirs_and_unsupported(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    good = inbox / "a.pdf"
    good.write_bytes(b"x")
    (inbox / "b.epub").write_bytes(b"x")
    (inbox / "c.txt").write_bytes(b"x")  # unsupported
    (inbox / ".hidden.pdf").write_bytes(b"x")  # dotfile
    (inbox / PROCESSED_SUBDIR).mkdir()
    (inbox / PROCESSED_SUBDIR / "done.pdf").write_bytes(b"x")
    (inbox / FAILED_SUBDIR).mkdir()
    (inbox / FAILED_SUBDIR / "bad.pdf").write_bytes(b"x")

    pending = discover_pending(inbox, [".pdf", ".epub"])
    names = sorted(p.name for p in pending)
    assert names == ["a.pdf", "b.epub"]


def test_discover_pending_missing_input_dir_returns_empty(tmp_path: Path) -> None:
    assert discover_pending(tmp_path / "does-not-exist", [".pdf"]) == []


# ---- process_one: success path ----


def test_process_one_success_relocates_artifacts(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    briefs = tmp_path / "briefs"
    cfg = _cfg(tmp_path, inbox, briefs)
    book = _seed_book(inbox, "effective_engineer.pdf")

    monkeypatch.setattr("marrow.watch.run_pipeline", _fake_success_factory(cfg))

    event = process_one(book, cfg)

    slug = book_slug(book)
    assert event.status == "success"
    assert event.slug == slug
    assert (briefs / f"{slug}_Brief.md").exists()
    assert (briefs / f"{slug}_Evaluation.md").exists()
    assert (inbox / PROCESSED_SUBDIR / "effective_engineer.pdf").exists()
    assert not book.exists()  # moved, not copied

    report = read_json(briefs / REPORT_FILENAME, WatchReport)
    assert len(report.events) == 1
    assert report.events[0].status == "success"
    assert report.events[0].slug == slug


# ---- process_one: failure path ----


def test_process_one_failure_moves_to_failed_dir(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    briefs = tmp_path / "briefs"
    cfg = _cfg(tmp_path, inbox, briefs)
    book = _seed_book(inbox, "broken.pdf")

    def fake_raise(*_args: Any, **_kwargs: Any) -> RunManifest:
        raise StageError("01_ingest", "bad PDF")

    monkeypatch.setattr("marrow.watch.run_pipeline", fake_raise)

    event = process_one(book, cfg)

    assert event.status == "failed"
    assert "bad PDF" in (event.error or "")
    assert (inbox / FAILED_SUBDIR / "broken.pdf").exists()
    assert not book.exists()
    assert not (briefs / f"{event.slug}_Brief.md").exists()

    report = read_json(briefs / REPORT_FILENAME, WatchReport)
    assert len(report.events) == 1
    assert report.events[0].status == "failed"


# ---- run_watch: end-to-end with --once ----


def test_run_watch_once_processes_all_then_exits(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    briefs = tmp_path / "briefs"
    cfg = _cfg(tmp_path, inbox, briefs)

    ok1 = _seed_book(inbox, "ok_one.pdf")
    ok2 = _seed_book(inbox, "ok_two.pdf")
    bad = _seed_book(inbox, "bad.pdf")

    success = _fake_success_factory(cfg)

    def dispatch(book_path: Path, config: MarrowConfig, **kwargs: Any) -> RunManifest:
        if book_path.name == "bad.pdf":
            raise StageError("01_ingest", "boom")
        return success(book_path, config, **kwargs)

    monkeypatch.setattr("marrow.watch.run_pipeline", dispatch)

    events = run_watch(cfg, once=True)

    assert len(events) == 3
    statuses = sorted(e.status for e in events)
    assert statuses == ["failed", "success", "success"]

    assert (inbox / PROCESSED_SUBDIR / "ok_one.pdf").exists()
    assert (inbox / PROCESSED_SUBDIR / "ok_two.pdf").exists()
    assert (inbox / FAILED_SUBDIR / "bad.pdf").exists()

    brief_files = sorted(p.name for p in briefs.glob("*_Brief.md"))
    assert len(brief_files) == 2
    assert all(name.endswith("_Brief.md") for name in brief_files)

    report = read_json(briefs / REPORT_FILENAME, WatchReport)
    assert len(report.events) == 3

    # Reference vars to satisfy the linter about their role as fixtures.
    assert ok1.parent == inbox and ok2.parent == inbox and bad.parent == inbox


# ---- run_watch: resume ----


def test_run_watch_resumes_in_progress_manifest(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    briefs = tmp_path / "briefs"
    cfg = _cfg(tmp_path, inbox, briefs)
    book = _seed_book(inbox, "half_done.pdf")

    # Pre-seed an in-progress manifest — simulates a prior interrupted run.
    slug = book_slug(book)
    working_dir = working_dir_for(cfg, book)
    working_dir.mkdir(parents=True, exist_ok=True)
    from marrow.io import write_json as _write_json

    _write_json(
        working_dir / "manifest.json",
        RunManifest(
            book_slug=slug,
            book_path=str(book),
            mode="api",
            started_at=datetime.now(UTC),
            status="in_progress",
            config={},
            marrow_version="test",
        ),
    )

    resume_calls: list[bool] = []
    success = _fake_success_factory(cfg)

    def capture(book_path: Path, config: MarrowConfig, **kwargs: Any) -> RunManifest:
        resume_calls.append(bool(kwargs.get("resume")))
        return success(book_path, config, **kwargs)

    monkeypatch.setattr("marrow.watch.run_pipeline", capture)

    events = run_watch(cfg, once=True)

    assert resume_calls == [True], "watch must invoke run_pipeline with resume=True"
    assert events[0].status == "success"


def test_run_watch_skips_already_complete_run(tmp_path: Path, monkeypatch) -> None:
    """If the manifest already says success, re-copy artifacts without re-running."""
    inbox = tmp_path / "inbox"
    briefs = tmp_path / "briefs"
    cfg = _cfg(tmp_path, inbox, briefs)
    book = _seed_book(inbox, "done_before.pdf")

    # Pre-seed a completed run with artifacts already on disk.
    slug = book_slug(book)
    working_dir = working_dir_for(cfg, book)
    export_dir = working_dir / "06b_export"
    export_dir.mkdir(parents=True, exist_ok=True)
    brief = export_dir / f"{slug}_Brief.md"
    brief.write_text("# existing\n", encoding="utf-8")
    evaluation = export_dir / f"{slug}_Evaluation.md"
    evaluation.write_text("# existing\n", encoding="utf-8")
    from marrow.io import write_json as _write_json

    _write_json(
        working_dir / "manifest.json",
        RunManifest(
            book_slug=slug,
            book_path=str(book),
            mode="api",
            started_at=datetime.now(UTC),
            status="success",
            config={},
            marrow_version="test",
            final_brief_path=str(brief),
            final_evaluation_path=str(evaluation),
        ),
    )

    def must_not_run(*_args: Any, **_kwargs: Any) -> RunManifest:
        pytest.fail("run_pipeline should not be called for an already-complete run")

    monkeypatch.setattr("marrow.watch.run_pipeline", must_not_run)

    events = run_watch(cfg, once=True)
    assert events[0].status == "success"
    assert (briefs / f"{slug}_Brief.md").exists()


# ---- Configuration ----


def test_run_watch_requires_input_and_output(tmp_path: Path) -> None:
    cfg = load_config(overrides={"mode": "api", "runs_dir": str(tmp_path / "runs")})
    with pytest.raises(MarrowError):
        run_watch(cfg, once=True)
