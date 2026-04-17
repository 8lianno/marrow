"""Config layering: defaults → file → env → overrides."""

from __future__ import annotations

from marrow.config import load_config


def test_defaults_load() -> None:
    cfg = load_config()
    assert cfg.cost.max_per_book == 3.00
    assert cfg.distill.compression_ratio == 0.30
    assert cfg.distill.max_continuation_rounds == 5
    assert cfg.coherence.max_fix_rounds == 2


def test_model_defaults() -> None:
    cfg = load_config()
    assert cfg.models.spine.provider == "gemini"
    assert cfg.models.spine.model_id == "gemini-2.5-flash"
    assert cfg.models.spine.thinking is True
    assert cfg.models.distill.provider == "gemini"
    assert cfg.models.distill.model_id == "gemini-2.5-pro"
    assert cfg.models.distill.thinking is False
    assert cfg.models.coherence.provider == "anthropic"
    assert cfg.models.coherence.model_id == "claude-sonnet-4-6"


def test_overrides_apply() -> None:
    cfg = load_config(overrides={"cost": {"max_per_book": 7.5}})
    assert cfg.cost.max_per_book == 7.5


def test_env_overrides_apply(monkeypatch) -> None:
    monkeypatch.setenv("MARROW_COST_MAX_PER_BOOK", "9.99")
    monkeypatch.setenv("MARROW_LOG_LEVEL", "DEBUG")
    cfg = load_config()
    assert cfg.cost.max_per_book == 9.99
    assert cfg.logging.level == "DEBUG"


def test_cli_overrides_beat_env(monkeypatch) -> None:
    monkeypatch.setenv("MARROW_COST_MAX_PER_BOOK", "9.99")
    cfg = load_config(overrides={"cost": {"max_per_book": 1.00}})
    assert cfg.cost.max_per_book == 1.00


def test_compression_override() -> None:
    cfg = load_config(overrides={"distill": {"compression_ratio": 0.50}})
    assert cfg.distill.compression_ratio == 0.50


def test_max_output_tokens_default() -> None:
    cfg = load_config()
    assert cfg.distill.max_output_tokens == 16384
