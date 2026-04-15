"""Host-mode orchestration helpers and CLI queue surface."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from marrow.cli import app
from marrow.config import load_config
from marrow.host import claim_task_batch, detect_host_info, submit_host_result, task_counts
from marrow.io import write_json
from marrow.schemas.run import HostInfo, HostResult, HostTask


def test_detect_host_info_prefers_codex(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_SESSION_ID", "codex-session")
    monkeypatch.setenv("CLAUDECODE", "1")
    host_info = detect_host_info()
    assert host_info.environment == "codex"
    assert host_info.session_id == "codex-session"


def test_claim_and_submit_host_task(tmp_path: Path) -> None:
    cfg = load_config(overrides={"runs_dir": str(tmp_path), "mode": "host"})
    working_dir = tmp_path / "book-slug"
    task = _write_task(working_dir / cfg.host.task_dir)

    claimed = claim_task_batch(
        working_dir,
        cfg.host,
        limit=1,
        claimer="codex-parent",
        host_info=HostInfo(environment="codex", session_id="codex-session"),
        stage=task.stage,
    )
    assert len(claimed) == 1
    assert task_counts(working_dir, cfg.host) == {
        "pending": 0,
        "claimed": 1,
        "completed": 0,
        "total": 1,
    }

    stored = submit_host_result(
        working_dir,
        cfg.host,
        task_id=str(task.task_id),
        host_result=HostResult(
            task_id=task.task_id,
            response={"summary": "ok"},
            estimated_tokens_in=12,
            estimated_tokens_out=6,
            model_id="codex-host-agent",
            worker_id="worker-1",
            host_environment="codex",
            completed_at=datetime.now(UTC),
        ),
    )
    assert stored.exists()
    assert task_counts(working_dir, cfg.host) == {
        "pending": 0,
        "claimed": 0,
        "completed": 1,
        "total": 1,
    }


def test_cli_next_and_submit(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    monkeypatch.setenv("MARROW_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_SESSION_ID", "codex-session")

    cfg = load_config()
    working_dir = tmp_path / "book-slug"
    task = _write_task(working_dir / cfg.host.task_dir)

    next_result = runner.invoke(app, ["next", "book-slug", "--limit", "1"])
    assert next_result.exit_code == 0
    next_payload = json.loads(next_result.stdout)
    assert next_payload["status"] == "awaiting_host"
    assert next_payload["host_environment"] == "codex"
    assert next_payload["recommended_parallelism"] == 1
    assert len(next_payload["tasks"]) == 1
    assert next_payload["tasks"][0]["task_id"] == str(task.task_id)

    result_path = working_dir / "worker-result.json"
    write_json(
        result_path,
        HostResult(
            task_id=task.task_id,
            response={"summary": "ok"},
            estimated_tokens_in=10,
            estimated_tokens_out=5,
            model_id="codex-host-agent",
            worker_id="worker-1",
            host_environment="codex",
            completed_at=datetime.now(UTC),
        ),
    )

    submit_result = runner.invoke(
        app,
        ["submit", "book-slug", str(task.task_id), str(result_path)],
    )
    assert submit_result.exit_code == 0
    submit_payload = json.loads(submit_result.stdout)
    assert submit_payload["status"] == "submitted"
    assert submit_payload["task_id"] == str(task.task_id)


def _write_task(task_dir: Path) -> HostTask:
    task = HostTask(
        task_id=uuid4(),
        stage="04_claims",
        model_role="claim_extraction",
        prompt="Read every evidence block and return JSON.",
        response_schema={"type": "object"},
        response_schema_name="ExtractedClaimsResponse",
        max_input_tokens=8000,
        max_output_tokens=4000,
        quality_hints=["Read all inputs.", "Compare all evidence before answering."],
        created_at=datetime.now(UTC),
    )
    write_json(task_dir / f"{task.task_id}.json", task)
    return task
