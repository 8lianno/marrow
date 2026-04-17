"""MarrowConfig: layered defaults → file → env → CLI.

Resolution order (highest precedence last):
1. Built-in defaults (this module)
2. configs/default.yaml
3. User-provided config file (if --config)
4. Environment variables (MARROW_*)
5. CLI flag overrides (passed to apply_overrides())
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from marrow.errors import ConfigError


class IngestConfig(BaseModel):
    parser: Literal["docling", "pypdf"] = "docling"
    parser_mode: Literal["auto", "force_ocr", "text_only"] = "auto"


class ClassifyConfig(BaseModel):
    pass


class SpineConfig(BaseModel):
    pass


class DistillConfig(BaseModel):
    compression_ratio: float = 0.30
    max_continuation_rounds: int = 5
    max_output_tokens: int = 16384


class CoherenceConfig(BaseModel):
    max_fix_rounds: int = 2
    similarity_threshold: float = 0.75


class ExportConfig(BaseModel):
    vault: str | None = None


class CostConfig(BaseModel):
    max_per_book: float = 3.00


class ModelRoute(BaseModel):
    provider: Literal["gemini", "codex", "stub"] = "stub"
    model_id: str = "stub"
    api_key_env: str | None = None
    thinking: bool = False  # enable Gemini thinking mode
    thinking_budget: int = 8192  # max thinking tokens


class ModelsConfig(BaseModel):
    classify: ModelRoute = Field(
        default_factory=lambda: ModelRoute(
            provider="gemini",
            model_id="gemini-flash-lite-latest",
            api_key_env="GEMINI_API_KEY",
        )
    )
    spine: ModelRoute = Field(
        default_factory=lambda: ModelRoute(
            provider="codex",
            model_id="gpt-5.1-codex",
        )
    )
    distill: ModelRoute = Field(
        default_factory=lambda: ModelRoute(
            provider="codex",
            model_id="gpt-5.1-codex",
        )
    )
    coherence: ModelRoute = Field(
        default_factory=lambda: ModelRoute(
            provider="codex",
            model_id="gpt-5.1-codex",
        )
    )


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class MarrowConfig(BaseModel):
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    classify: ClassifyConfig = Field(default_factory=ClassifyConfig)
    spine: SpineConfig = Field(default_factory=SpineConfig)
    distill: DistillConfig = Field(default_factory=DistillConfig)
    coherence: CoherenceConfig = Field(default_factory=CoherenceConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    runs_dir: str = "runs"

    model_config = {"populate_by_name": True}


# ---- Loader ----


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping: {path}")
    return raw


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_extends(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    seen = seen or set()
    path = path.resolve()
    if path in seen:
        raise ConfigError(f"Circular config extends: {path}")
    seen.add(path)
    raw = _read_yaml(path)
    parent_name = raw.pop("extends", None)
    if parent_name:
        parent_path = path.parent / parent_name
        parent = _resolve_extends(parent_path, seen)
        return _deep_merge(parent, raw)
    return raw


def _env_overrides() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if v := os.environ.get("MARROW_RUNS_DIR"):
        out["runs_dir"] = v
    if v := os.environ.get("MARROW_LOG_LEVEL"):
        out.setdefault("logging", {})["level"] = v
    if v := os.environ.get("MARROW_OBSIDIAN_VAULT"):
        out.setdefault("export", {})["vault"] = v
    if v := os.environ.get("MARROW_COST_MAX_PER_BOOK"):
        out.setdefault("cost", {})["max_per_book"] = float(v)
    return out


def load_config(
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> MarrowConfig:
    """Resolve config from defaults file → user file → env → overrides."""
    project_root = Path(__file__).resolve().parents[2]
    default_path = project_root / "configs" / "default.yaml"

    raw = _resolve_extends(default_path) if default_path.exists() else {}

    if config_path is not None:
        user_raw = _resolve_extends(config_path)
        raw = _deep_merge(raw, user_raw)

    raw = _deep_merge(raw, _env_overrides())

    if overrides:
        raw = _deep_merge(raw, overrides)

    return MarrowConfig.model_validate(raw)
