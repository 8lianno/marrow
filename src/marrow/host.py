"""Host-mode orchestration helpers for claimable task batches and host detection."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from marrow.config import HostConfig
from marrow.io import dump_json, read_json
from marrow.schemas.run import HostInfo, HostResult, HostTask, HostTaskClaim


def detect_host_info(env: dict[str, str] | None = None) -> HostInfo:
    """Detect the current host environment from well-known session variables."""
    env = env or dict(os.environ)

    if session_id := env.get("CODEX_SESSION_ID"):
        return HostInfo(environment="codex", session_id=session_id)
    if env.get("CLAUDECODE") or (Path.home() / ".claude").exists():
        return HostInfo(environment="claude-code", session_id=env.get("CLAUDE_SESSION_ID"))
    if session_id := env.get("CURSOR_AGENT_ID"):
        return HostInfo(environment="cursor", session_id=session_id)

    aider_keys = sorted(k for k in env if k.startswith("AIDER_"))
    if aider_keys:
        return HostInfo(environment="aider", session_id=env.get("AIDER_SESSION_ID"))
    return HostInfo(environment="generic")


def quality_hints_for(stage: str, model_role: str) -> list[str]:
    """Return host-side instructions that preserve comparable quality under delegation."""
    hints = [
        "Read the entire prompt before drafting a response.",
        "Preserve the schema exactly; do not invent fields or change types.",
        "Read every evidence/input block in the prompt before answering.",
    ]
    if stage in {"03_graph", "05_synthesize", "05b_validate", "06a_evaluate"}:
        hints.append(
            "When multiple evidence boxes or candidate facts are present, compare all of them before finalizing."
        )
    if model_role in {"validation", "synthesis"}:
        hints.append(
            "If you delegate, have one worker own one claimed task end to end and keep the same quality bar as a direct answer."
        )
    return hints


def claimable_task_paths(
    working_dir: Path,
    host_config: HostConfig,
    *,
    stage: str | None = None,
) -> list[Path]:
    """Return pending task files that are not completed and are not actively claimed."""
    task_dir = working_dir / host_config.task_dir
    result_dir = working_dir / host_config.result_dir
    claim_dir = working_dir / host_config.claim_dir
    if not task_dir.exists():
        return []

    claim_dir.mkdir(parents=True, exist_ok=True)
    tasks = sorted(task_dir.glob("*.json"))
    out: list[Path] = []
    for task_path in tasks:
        if stage and not task_path.name.startswith(stage):
            continue
        if (result_dir / task_path.name).exists():
            continue
        claim_path = claim_dir / task_path.name
        if _active_claim(claim_path) is not None:
            continue
        out.append(task_path)
    return out


def claim_task_batch(
    working_dir: Path,
    host_config: HostConfig,
    *,
    limit: int,
    claimer: str,
    host_info: HostInfo,
    stage: str | None = None,
) -> list[tuple[HostTask, Path]]:
    """Claim up to `limit` pending tasks for a specific host/worker."""
    task_dir = working_dir / host_config.task_dir
    result_dir = working_dir / host_config.result_dir
    claim_dir = working_dir / host_config.claim_dir
    if not task_dir.exists():
        return []
    claim_dir.mkdir(parents=True, exist_ok=True)

    claimed: list[tuple[HostTask, Path]] = []
    for task_path in sorted(task_dir.glob("*.json")):
        if len(claimed) >= limit:
            break
        if (result_dir / task_path.name).exists():
            continue

        task = read_json(task_path, HostTask)
        if stage is not None and task.stage != stage:
            continue
        claim_path = claim_dir / task_path.name
        if _active_claim(claim_path) is not None:
            continue
        claim = HostTaskClaim(
            task_id=task.task_id,
            claimer=claimer,
            host_environment=host_info.environment,
            claimed_at=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=host_config.claim_ttl_seconds),
        )
        if _write_claim_exclusive(claim_path, claim):
            claimed.append((task, task_path))
    return claimed


def submit_host_result(
    working_dir: Path,
    host_config: HostConfig,
    *,
    task_id: str,
    host_result: HostResult,
) -> Path:
    """Persist a validated host result and release any matching claim."""
    task_name = f"{task_id}.json"
    result_dir = working_dir / host_config.result_dir
    claim_dir = working_dir / host_config.claim_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / task_name
    result_path.write_text(dump_json(host_result) + "\n", encoding="utf-8")
    claim_path = claim_dir / task_name
    claim_path.unlink(missing_ok=True)
    return result_path


def task_payload(
    working_dir: Path,
    host_config: HostConfig,
    *,
    task: HostTask,
    task_path: Path,
) -> dict[str, object]:
    result_dir = working_dir / host_config.result_dir
    return {
        "task_id": str(task.task_id),
        "stage": task.stage,
        "model_role": task.model_role,
        "response_schema_name": task.response_schema_name,
        "task_path": str(task_path),
        "result_path": str(result_dir / task_path.name),
        "chunk_uuids": [str(u) for u in task.chunk_uuids],
        "parallelizable": task.parallelizable,
        "max_input_tokens": task.max_input_tokens,
        "max_output_tokens": task.max_output_tokens,
        "quality_hints": task.quality_hints,
    }


def task_counts(working_dir: Path, host_config: HostConfig) -> dict[str, int]:
    task_dir = working_dir / host_config.task_dir
    result_dir = working_dir / host_config.result_dir
    claim_dir = working_dir / host_config.claim_dir
    if not task_dir.exists():
        return {"pending": 0, "claimed": 0, "completed": 0, "total": 0}

    total = len(list(task_dir.glob("*.json")))
    completed = len([p for p in task_dir.glob("*.json") if (result_dir / p.name).exists()])
    claimed = 0
    for task_path in task_dir.glob("*.json"):
        if (result_dir / task_path.name).exists():
            continue
        if _active_claim(claim_dir / task_path.name) is not None:
            claimed += 1
    pending = max(total - completed - claimed, 0)
    return {"pending": pending, "claimed": claimed, "completed": completed, "total": total}


def task_counts_by_stage(working_dir: Path, host_config: HostConfig) -> dict[str, dict[str, int]]:
    """Group task counts by the stage that wrote each task.

    Returns `{stage_name: {pending, claimed, completed, total}}`. Stages with
    no tasks are omitted. Useful for `marrow status` and the Host Mode skill's
    per-stage progress updates.
    """
    task_dir = working_dir / host_config.task_dir
    result_dir = working_dir / host_config.result_dir
    claim_dir = working_dir / host_config.claim_dir
    if not task_dir.exists():
        return {}

    per_stage: dict[str, dict[str, int]] = {}
    for task_path in task_dir.glob("*.json"):
        try:
            task = read_json(task_path, HostTask)
        except Exception:
            continue
        bucket = per_stage.setdefault(
            task.stage, {"pending": 0, "claimed": 0, "completed": 0, "total": 0}
        )
        bucket["total"] += 1
        if (result_dir / task_path.name).exists():
            bucket["completed"] += 1
        elif _active_claim(claim_dir / task_path.name) is not None:
            bucket["claimed"] += 1
        else:
            bucket["pending"] += 1
    return per_stage


def recommended_parallelism(host_config: HostConfig, stage: str, available_tasks: int) -> int:
    if available_tasks <= 0:
        return 0
    if stage in {"03_graph", "04_claims", "05b_validate", "06a_evaluate"}:
        return min(host_config.default_batch_size, available_tasks)
    return min(2, available_tasks)


def _active_claim(claim_path: Path) -> HostTaskClaim | None:
    if not claim_path.exists():
        return None
    try:
        claim = read_json(claim_path, HostTaskClaim)
    except Exception:
        claim_path.unlink(missing_ok=True)
        return None
    if claim.lease_expires_at <= datetime.now(UTC):
        claim_path.unlink(missing_ok=True)
        return None
    return claim


def _write_claim_exclusive(path: Path, claim: HostTaskClaim) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(dump_json(claim) + "\n")
    except FileExistsError:
        return False
    return True
