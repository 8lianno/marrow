"""Verify the LLM wrapper works with stub provider."""

from __future__ import annotations

from pathlib import Path

from marrow.config import load_config
from marrow.llm import LLMCaller


def test_codex_provider_is_registered_in_config() -> None:
    """Sanity: codex is a valid provider value in ModelRoute."""
    from marrow.config import ModelRoute

    route = ModelRoute(provider="codex", model_id="gpt-5.1-codex")
    assert route.provider == "codex"


def test_stub_provider_records_to_ledger(tmp_path: Path) -> None:
    cfg = load_config(
        overrides={
            "runs_dir": str(tmp_path),
            "models": {"spine": {"provider": "stub", "model_id": "stub"}},
        }
    )
    caller = LLMCaller(tmp_path, cfg)
    out = caller.call(stage="test", prompt="hello", model_role="spine")
    assert isinstance(out, str)
    assert caller.ledger.total_usd() >= 0.0
    assert caller.ledger.by_stage().get("test", 0.0) >= 0.0


def test_call_raw_returns_finish_reason(tmp_path: Path) -> None:
    cfg = load_config(
        overrides={
            "runs_dir": str(tmp_path),
            "models": {"distill": {"provider": "stub", "model_id": "stub"}},
        }
    )
    caller = LLMCaller(tmp_path, cfg)
    raw = caller.call_raw(stage="test", prompt="hello", model_role="distill")
    assert raw.finish_reason == "STOP"
    assert raw.tokens_in > 0
    assert raw.tokens_out > 0


def test_validate_strips_code_fences(tmp_path: Path) -> None:
    from marrow.llm import LLMCaller
    from marrow.schemas.spine import ChapterSpine

    # Simulate a model wrapping JSON in code fences
    json_with_fences = '```json\n{"chapter_title": "Test", "section_id": "00000000-0000-0000-0000-000000000001", "thesis": "x", "source_word_count": 100, "target_word_count": 30}\n```'
    result = LLMCaller._validate(json_with_fences, ChapterSpine)
    assert isinstance(result, ChapterSpine)
    assert result.chapter_title == "Test"


def test_archive_call_writes_to_disk(tmp_path: Path) -> None:
    cfg = load_config(
        overrides={
            "runs_dir": str(tmp_path),
            "models": {"spine": {"provider": "stub", "model_id": "stub"}},
        }
    )
    caller = LLMCaller(tmp_path, cfg)
    caller.call(stage="test_archive", prompt="hello", model_role="spine")

    log_files = list((tmp_path / "logs" / "llm").glob("test_archive_*.json"))
    assert len(log_files) == 1
