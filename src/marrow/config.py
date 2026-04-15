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

RunMode = Literal["host", "api"]


class IngestConfig(BaseModel):
    parser: Literal["docling", "marker", "mineru"] = "docling"
    parser_mode: Literal["auto", "force_ocr", "text_only"] = "auto"


class ChunkConfig(BaseModel):
    embedding_model: str = "jinaai/jina-embeddings-v2-base-en"
    window_tokens: int = 512
    overlap_pct: float = 0.25


class GraphConfig(BaseModel):
    community_top_k: int = 512


class ClaimsConfig(BaseModel):
    dedup_threshold: float = 0.92


class SynthesizeConfig(BaseModel):
    target_pages: int = 50
    page_tolerance: int = 5


class ValidateConfig(BaseModel):
    max_iterations: int = 3
    pass_rate_threshold: float = 0.90


class EvaluateConfig(BaseModel):
    hamlet_leaf_threshold: float = 0.92
    booookscore_threshold: float = 0.70
    factscore_threshold: float = 0.80
    skip: bool = False


class ExportConfig(BaseModel):
    vault: str | None = None


class CostConfig(BaseModel):
    max_per_book: float = 4.00


class ModelRoute(BaseModel):
    provider: Literal["anthropic", "ollama", "gemini", "openrouter", "vllm", "jina", "stub"] = (
        "stub"
    )
    model_id: str = "stub"
    api_base: str | None = None  # e.g. http://localhost:11434 for ollama
    api_key_env: str | None = None  # name of env var holding the API key


class ModelsConfig(BaseModel):
    claim_extraction: ModelRoute = Field(default_factory=ModelRoute)
    graph_extraction: ModelRoute = Field(default_factory=ModelRoute)
    synthesis: ModelRoute = Field(default_factory=ModelRoute)
    validation: ModelRoute = Field(default_factory=ModelRoute)
    quiz_generation: ModelRoute = Field(default_factory=ModelRoute)


class HostConfig(BaseModel):
    task_dir: str = "host_tasks"
    result_dir: str = "host_results"
    claim_dir: str = "host_claims"
    poll_interval_seconds: float = 1.0
    task_timeout_seconds: float = 3600.0
    task_max_input_tokens: int = 8000
    task_max_output_tokens: int = 4000
    default_batch_size: int = 4
    claim_ttl_seconds: int = 1800
    allow_stub_fallback: bool = False


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class MarrowConfig(BaseModel):
    mode: RunMode = "host"
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    claims: ClaimsConfig = Field(default_factory=ClaimsConfig)
    synthesize: SynthesizeConfig = Field(default_factory=SynthesizeConfig)
    validate_: ValidateConfig = Field(default_factory=ValidateConfig, alias="validate")
    evaluate: EvaluateConfig = Field(default_factory=EvaluateConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    host: HostConfig = Field(default_factory=HostConfig)
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
