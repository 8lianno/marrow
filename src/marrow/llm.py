"""Mandatory LLM call wrapper — Gemini + Anthropic only.

Every model call in Marrow MUST go through this module. Direct SDK calls
bypass cost telemetry, retry, schema validation, and budget enforcement.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from marrow.config import MarrowConfig
from marrow.errors import BudgetExceeded, LLMError
from marrow.io import write_json
from marrow.logging import get_logger
from marrow.store.ledger import CostLedger

T = TypeVar("T", bound=BaseModel)
log = get_logger(__name__)

# Per-1k-token pricing (April 2026).
_PRICING_USD_PER_1K: dict[tuple[str, str], tuple[float, float]] = {
    ("gemini", "gemini-flash-latest"): (0.0006, 0.0006),
    ("gemini", "gemini-flash-lite-latest"): (0.0003, 0.0003),
    ("codex", "*"): (0.0, 0.0),  # subscription, no per-call billing
    ("stub", "*"): (0.0, 0.0),
}


def _estimate_cost(provider: str, model_id: str, tokens_in: int, tokens_out: int) -> float:
    rate = _PRICING_USD_PER_1K.get((provider, model_id)) or _PRICING_USD_PER_1K.get(
        (provider, "*"), (0.0, 0.0)
    )
    return (tokens_in / 1000) * rate[0] + (tokens_out / 1000) * rate[1]


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class LLMResponse:
    """Wraps a raw LLM response with metadata."""

    def __init__(self, text: str, tokens_in: int, tokens_out: int, finish_reason: str = "STOP"):
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.finish_reason = finish_reason


class LLMCaller:
    """Stateful caller bound to a specific run's working_dir and config."""

    def __init__(self, working_dir: Path, config: MarrowConfig) -> None:
        self.working_dir = working_dir
        self.config = config
        self.ledger = CostLedger(working_dir / "cost_ledger.sqlite")
        self.llm_log_dir = working_dir / "logs" / "llm"
        self.llm_log_dir.mkdir(parents=True, exist_ok=True)

    def call(
        self,
        *,
        stage: str,
        prompt: str,
        model_role: str,
        response_schema: type[T] | None = None,
        max_tokens: int = 8192,
    ) -> T | str:
        """High-level call that returns validated schema or raw text."""
        raw = self.call_raw(
            stage=stage,
            prompt=prompt,
            model_role=model_role,
            response_schema=response_schema,
            max_tokens=max_tokens,
        )
        return self._validate(raw.text, response_schema)

    def call_raw(
        self,
        *,
        stage: str,
        prompt: str,
        model_role: str,
        response_schema: type[T] | None = None,
        max_tokens: int = 8192,
    ) -> LLMResponse:
        """Low-level call that returns LLMResponse with finish_reason."""
        route = getattr(self.config.models, model_role, None)
        if route is None:
            raise LLMError(f"Unknown model_role: {model_role}")

        self._budget_gate()

        call_id = uuid4()
        started = time.perf_counter()

        provider = route.provider
        model_id = route.model_id

        if provider == "stub":
            text = self._stub_response(prompt, response_schema)
            raw = LLMResponse(text, _approx_tokens(prompt), _approx_tokens(text), "STOP")
        elif provider == "gemini":
            raw = self._gemini_call(
                model_id, prompt, response_schema, route.api_key_env, max_tokens,
                thinking=route.thinking, thinking_budget=route.thinking_budget,
            )
        elif provider == "codex":
            raw = self._codex_call(model_id, prompt, response_schema, max_tokens)
        else:
            raise LLMError(f"Unknown provider: {provider}")

        latency_ms = int((time.perf_counter() - started) * 1000)
        usd = _estimate_cost(provider, model_id, raw.tokens_in, raw.tokens_out)

        self._archive_call(
            call_id=call_id,
            stage=stage,
            model_role=model_role,
            provider=provider,
            model_id=model_id,
            prompt=prompt,
            response=raw.text,
            tokens_in=raw.tokens_in,
            tokens_out=raw.tokens_out,
            usd=usd,
            latency_ms=latency_ms,
            finish_reason=raw.finish_reason,
        )
        self.ledger.record_call(
            stage=stage,
            model_role=model_role,
            model_id=model_id,
            provider=provider,
            tokens_in=raw.tokens_in,
            tokens_out=raw.tokens_out,
            usd=usd,
            latency_ms=latency_ms,
        )
        return raw

    def _budget_gate(self) -> None:
        spent = self.ledger.total_usd()
        cap = self.config.cost.max_per_book
        if spent >= cap:
            self.ledger.record_budget_event("exceeded", spent, cap)
            raise BudgetExceeded(
                f"Spent ${spent:.4f} reached cap ${cap:.2f}. Re-run with a higher cost cap."
            )

    def _gemini_call(
        self,
        model_id: str,
        prompt: str,
        response_schema: type[T] | None,
        api_key_env: str | None,
        max_tokens: int,
        thinking: bool = False,
        thinking_budget: int = 8192,
    ) -> LLMResponse:
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError as e:
            raise LLMError(
                f"google-genai SDK not installed; run `uv pip install google-genai`: {e}"
            ) from e

        import os as _os

        key = _os.environ.get(api_key_env or "GEMINI_API_KEY")
        if not key:
            raise LLMError(f"Gemini requires API key in env var {api_key_env or 'GEMINI_API_KEY'}")

        client = genai.Client(api_key=key)
        generation_config: dict[str, Any] = {
            "max_output_tokens": max_tokens,
        }

        # Thinking mode: enable extended reasoning before answering.
        # When thinking is on, temperature must be unset (Gemini controls it).
        if thinking:
            generation_config["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=thinking_budget,
            )
        else:
            generation_config["temperature"] = 0.0

        if response_schema is not None:
            generation_config["response_mime_type"] = "application/json"
            generation_config["response_schema"] = response_schema

        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=generation_config,
        )

        # Extract text from response, skipping thinking parts
        text = ""
        candidates = getattr(response, "candidates", None)
        if candidates and candidates[0].content and candidates[0].content.parts:
            for part in candidates[0].content.parts:
                # Skip thinking parts — only take the final answer
                if getattr(part, "thought", False):
                    continue
                if hasattr(part, "text") and part.text:
                    text += part.text
        if not text:
            text = response.text or ""

        usage = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", _approx_tokens(prompt)) or 0)
        tokens_out = int(getattr(usage, "candidates_token_count", _approx_tokens(text)) or 0)

        # Extract finish reason from Gemini response
        finish_reason = "STOP"
        if candidates:
            reason = getattr(candidates[0], "finish_reason", None)
            if reason and str(reason).upper() in ("MAX_TOKENS", "2"):
                finish_reason = "MAX_TOKENS"

        return LLMResponse(text, tokens_in, tokens_out, finish_reason)

    def _codex_call(
        self,
        model_id: str,
        prompt: str,
        response_schema: type[T] | None,
        max_tokens: int,  # ignored — codex runs until done
    ) -> LLMResponse:
        """Invoke `codex exec` as a subprocess with progress logging."""
        import json as _json
        import os as _os
        import shutil as _shutil
        import subprocess
        import tempfile
        import threading

        if _shutil.which("codex") is None:
            raise LLMError(
                "codex CLI not found on PATH. Install from "
                "https://codex.openai.com/install.sh or run "
                "`marrow --help` with provider=gemini."
            )

        schema_path: str | None = None
        if response_schema is not None:
            schema_dict = response_schema.model_json_schema()
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as sf:
                _json.dump(schema_dict, sf)
                schema_path = sf.name

        cmd: list[str] = ["codex", "exec"]
        if model_id and model_id not in ("codex", "default", "auto", ""):
            cmd.extend(["-m", model_id])
        cmd.extend([
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--ephemeral",
            "-c", 'model_reasoning_effort="medium"',
        ])
        if schema_path is not None:
            cmd.extend(["--output-schema", schema_path])
        cmd.append("-")  # read prompt from stdin

        TIMEOUT_S = 3600  # 60 min — codex on dense chapters can take 15-20 min
        LOG_INTERVAL_S = 15

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            )

            def _write_stdin():
                try:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                except Exception:
                    pass
            threading.Thread(target=_write_stdin, daemon=True).start()

            start = time.perf_counter()
            last_log = start

            while proc.poll() is None:
                elapsed = time.perf_counter() - start
                if elapsed > TIMEOUT_S:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise LLMError(f"codex exec timed out after {TIMEOUT_S}s")
                if time.perf_counter() - last_log > LOG_INTERVAL_S:
                    log.info("codex_exec_progress", elapsed_s=f"{elapsed:.0f}")
                    last_log = time.perf_counter()
                time.sleep(0.5)

            stdout_text = (proc.stdout.read() if proc.stdout else "").strip()
            stderr_text = (proc.stderr.read() if proc.stderr else "")
            returncode = proc.returncode

            log.info(
                "codex_exec_completed",
                model_id=model_id,
                elapsed_s=f"{time.perf_counter() - start:.1f}",
                prompt_chars=len(prompt),
                response_chars=len(stdout_text),
            )

        except KeyboardInterrupt:
            log.warning("codex_exec_interrupted")
            raise
        finally:
            if schema_path is not None:
                try:
                    _os.unlink(schema_path)
                except OSError:
                    pass

        stderr_tail = stderr_text[-2000:] if stderr_text else ""

        if returncode != 0:
            lowered = stderr_tail.lower()
            quota_patterns = (
                "rate limit", "quota", "usage limit", "too many requests",
                "usage cap", "try again later",
            )
            if any(k in lowered for k in quota_patterns):
                from marrow.errors import BudgetExceeded

                raise BudgetExceeded(
                    f"Codex quota exhausted. Wait or use "
                    f"`--config configs/gemini.yaml`. stderr:\n{stderr_tail}"
                )
            if any(k in lowered for k in ("login", "authenticate", "unauthorized", "not logged in")):
                raise LLMError(
                    f"Codex auth failed. Run `codex login` and try again. "
                    f"stderr:\n{stderr_tail}"
                )
            raise LLMError(
                f"codex exec failed (exit {returncode}):\n{stderr_tail}"
            )

        if not stdout_text:
            raise LLMError(
                f"codex exec produced empty output. stderr:\n{stderr_tail}"
            )

        tokens_in = _approx_tokens(prompt)
        tokens_out = _approx_tokens(stdout_text)
        return LLMResponse(stdout_text, tokens_in, tokens_out, "STOP")

    @staticmethod
    def _stub_response(prompt: str, response_schema: type[T] | None) -> str:
        if response_schema is None:
            return f"[stub response to prompt of len {len(prompt)}]"
        try:
            instance = response_schema()  # type: ignore[call-arg]
            return instance.model_dump_json()
        except Exception:
            return "{}"

    @staticmethod
    def _validate(response_text: str, response_schema: type[T] | None) -> T | str:
        if response_schema is None:
            return response_text
        # Strip markdown code fences if the model wrapped JSON in them
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        try:
            return response_schema.model_validate_json(cleaned)
        except Exception as e:
            raise LLMError(f"Response failed schema validation: {e}\n\n{response_text}") from e

    def _archive_call(
        self,
        *,
        call_id: UUID,
        stage: str,
        model_role: str,
        provider: str,
        model_id: str,
        prompt: str,
        response: str,
        tokens_in: int,
        tokens_out: int,
        usd: float,
        latency_ms: int,
        finish_reason: str = "STOP",
    ) -> None:
        archive = {
            "call_id": str(call_id),
            "stage": stage,
            "model_role": model_role,
            "provider": provider,
            "model_id": model_id,
            "prompt": prompt,
            "response": response,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "usd": usd,
            "latency_ms": latency_ms,
            "finish_reason": finish_reason,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        write_json(self.llm_log_dir / f"{stage}_{call_id}.json", archive)
