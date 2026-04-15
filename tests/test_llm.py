"""Verify the LLM wrapper works in both modes without touching real providers."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from marrow.config import load_config
from marrow.errors import LLMError
from marrow.llm import LLMCaller


def test_api_stub_provider_records_to_ledger(tmp_path: Path) -> None:
    cfg = load_config(
        overrides={
            "mode": "api",
            "runs_dir": str(tmp_path),
            "models": {"synthesis": {"provider": "stub", "model_id": "stub"}},
        }
    )
    caller = LLMCaller(tmp_path, cfg)
    out = caller.call(stage="test", prompt="hello", model_role="synthesis")
    assert isinstance(out, str)
    assert caller.ledger.total_usd() >= 0.0
    # Cost ledger has at least one row for this call.
    assert caller.ledger.by_stage().get("test", 0.0) >= 0.0


def test_host_mode_writes_task_and_completes_when_result_arrives(tmp_path: Path) -> None:
    cfg = load_config(
        overrides={
            "mode": "host",
            "runs_dir": str(tmp_path),
            "host": {"poll_interval_seconds": 0.05},
        }
    )
    working_dir = tmp_path / "wd"
    working_dir.mkdir()
    caller = LLMCaller(working_dir, cfg)

    # Background thread that simulates the host agent: waits for a task to appear,
    # then writes a HostResult JSON.
    def host_agent() -> None:
        task_dir = working_dir / cfg.host.task_dir
        result_dir = working_dir / cfg.host.result_dir
        result_dir.mkdir(parents=True, exist_ok=True)
        for _ in range(40):
            if task_dir.exists():
                tasks = list(task_dir.glob("*.json"))
                if tasks:
                    task_path = tasks[0]
                    task = json.loads(task_path.read_text())
                    result = {
                        "task_id": task["task_id"],
                        "response": "Simulated host response.",
                        "estimated_tokens_in": 10,
                        "estimated_tokens_out": 5,
                        "completed_at": datetime.now(UTC).isoformat(),
                    }
                    (result_dir / task_path.name).write_text(json.dumps(result))
                    return
            time.sleep(0.05)

    t = threading.Thread(target=host_agent, daemon=True)
    t.start()

    out = caller.call(stage="test", prompt="please summarize", model_role="synthesis")
    t.join(timeout=2.0)

    assert out == "Simulated host response."
    # Verify task was written and result was consumed.
    task_files = list((working_dir / cfg.host.task_dir).glob("*.json"))
    result_files = list((working_dir / cfg.host.result_dir).glob("*.json"))
    assert len(task_files) == 1
    assert len(result_files) == 1
    # Task ID parses as UUID.
    UUID(task_files[0].stem)


def test_host_mode_timeout_raises_without_stub_fallback(tmp_path: Path) -> None:
    cfg = load_config(
        overrides={
            "mode": "host",
            "runs_dir": str(tmp_path),
            "host": {"poll_interval_seconds": 0.01, "task_timeout_seconds": 0.05},
        }
    )
    working_dir = tmp_path / "wd"
    working_dir.mkdir()
    caller = LLMCaller(working_dir, cfg)

    with pytest.raises(LLMError, match="Timed out waiting for host result"):
        caller.call(stage="test", prompt="please summarize", model_role="synthesis")


def test_host_mode_timeout_can_stub_when_explicitly_enabled(tmp_path: Path) -> None:
    cfg = load_config(
        overrides={
            "mode": "host",
            "runs_dir": str(tmp_path),
            "host": {
                "poll_interval_seconds": 0.01,
                "task_timeout_seconds": 0.05,
                "allow_stub_fallback": True,
            },
        }
    )
    working_dir = tmp_path / "wd"
    working_dir.mkdir()
    caller = LLMCaller(working_dir, cfg)

    out = caller.call(stage="test", prompt="please summarize", model_role="synthesis")
    assert isinstance(out, str)
