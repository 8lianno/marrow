"""Config layering: defaults → file → env → overrides."""

from __future__ import annotations

from pathlib import Path

from marrow.config import load_config


def test_defaults_load() -> None:
    cfg = load_config()
    assert cfg.mode == "host"
    assert cfg.cost.max_per_book == 4.00
    assert cfg.host.default_batch_size == 4
    assert cfg.host.task_timeout_seconds == 3600.0
    assert cfg.host.allow_stub_fallback is False


def test_overrides_apply() -> None:
    cfg = load_config(overrides={"mode": "api", "cost": {"max_per_book": 7.5}})
    assert cfg.mode == "api"
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


def test_cheap_extends_default(tmp_path: Path) -> None:
    cfg = load_config(config_path=Path("configs/cheap.yaml"))
    assert cfg.mode == "host"
    assert cfg.cost.max_per_book == 0.50  # cheap.yaml override
    assert cfg.chunk.window_tokens == 512  # inherited from default
