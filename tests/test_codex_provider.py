"""Smoke test for the codex provider. Requires codex CLI + authenticated subscription."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from marrow.config import load_config
from marrow.llm import LLMCaller


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
def test_codex_provider_returns_text(tmp_path: Path) -> None:
    """Minimal end-to-end: one call, non-empty response."""
    cfg = load_config(
        overrides={
            "runs_dir": str(tmp_path),
            "models": {
                "spine": {
                    "provider": "codex",
                    "model_id": "gpt-5.1-codex",
                }
            },
        }
    )
    caller = LLMCaller(tmp_path, cfg)
    out = caller.call(
        stage="test_codex",
        prompt=(
            "Respond with exactly one word, uppercase: PONG. "
            "No quotes, no punctuation, no other text."
        ),
        model_role="spine",
    )
    assert "PONG" in str(out).upper()


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
def test_codex_provider_with_schema(tmp_path: Path) -> None:
    """Schema-enforced JSON round-trip."""
    from pydantic import BaseModel

    class Greeting(BaseModel):
        word: str
        count: int

    cfg = load_config(
        overrides={
            "runs_dir": str(tmp_path),
            "models": {
                "spine": {
                    "provider": "codex",
                    "model_id": "gpt-5.1-codex",
                }
            },
        }
    )
    caller = LLMCaller(tmp_path, cfg)
    out = caller.call(
        stage="test_codex_schema",
        prompt='Return JSON: {"word": "pong", "count": 1}',
        model_role="spine",
        response_schema=Greeting,
    )
    assert isinstance(out, Greeting)
    assert out.word.lower() == "pong"
    assert out.count == 1
