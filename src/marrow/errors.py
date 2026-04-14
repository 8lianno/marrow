"""Error hierarchy and exit codes for Marrow."""

from __future__ import annotations

from enum import IntEnum


class MarrowExitCode(IntEnum):
    SUCCESS = 0
    CONFIG_ERROR = 2
    STAGE_FAILED = 3
    BUDGET_EXCEEDED = 4
    LLM_ERROR = 5
    INPUT_NOT_FOUND = 6
    MODE_LOCK_VIOLATION = 7
    UNKNOWN = 99


class MarrowError(Exception):
    """Root of all Marrow errors."""

    exit_code: MarrowExitCode = MarrowExitCode.UNKNOWN


class ConfigError(MarrowError):
    exit_code = MarrowExitCode.CONFIG_ERROR


class InputNotFound(MarrowError):
    exit_code = MarrowExitCode.INPUT_NOT_FOUND


class StageError(MarrowError):
    exit_code = MarrowExitCode.STAGE_FAILED

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage


class BudgetExceeded(MarrowError):
    exit_code = MarrowExitCode.BUDGET_EXCEEDED


class LLMError(MarrowError):
    exit_code = MarrowExitCode.LLM_ERROR


class ChunkExtractionFailed(MarrowError):
    """Single-chunk failure. Caller MUST catch and continue, never propagate."""

    exit_code = MarrowExitCode.STAGE_FAILED


class ModeLockViolation(MarrowError):
    """Attempted to resume in a different mode than the run was started in."""

    exit_code = MarrowExitCode.MODE_LOCK_VIOLATION
