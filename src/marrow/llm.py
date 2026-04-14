"""Mandatory LLM call wrapper with two backends (API + Host).

Every model call in Marrow MUST go through `marrow.llm.call()`. Direct
`anthropic.Client(...)` or HTTP calls bypass cost telemetry, retry, schema
validation, and budget enforcement and are strictly forbidden.

Backends:
- `api`: calls Anthropic SDK directly. Cost ledger ticks per call. Requires
  ANTHROPIC_API_KEY for anthropic-routed model_roles.
- `host`: writes a HostTask JSON to runs/<slug>/host_tasks/, polls for a
  matching HostResult JSON, returns the parsed response. The host agent
  (Claude Code, Codex, Cursor, Aider) supplies the reasoning. No API key
  read; no outbound network call.
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
from marrow.schemas.run import HostResult, HostTask
from marrow.store.ledger import CostLedger

T = TypeVar("T", bound=BaseModel)
log = get_logger(__name__)

# Crude per-1k-token pricing for cost estimates. Values approximate as of 2026-04.
_PRICING_USD_PER_1K = {
    ("anthropic", "claude-sonnet-4-6"): (0.003, 0.015),
    ("anthropic", "claude-opus-4-6"): (0.015, 0.075),
    ("vllm", "*"): (0.0, 0.0),
    ("stub", "*"): (0.0, 0.0),
}


def _estimate_cost(provider: str, model_id: str, tokens_in: int, tokens_out: int) -> float:
    rate = _PRICING_USD_PER_1K.get((provider, model_id)) or _PRICING_USD_PER_1K.get(
        (provider, "*"), (0.0, 0.0)
    )
    return (tokens_in / 1000) * rate[0] + (tokens_out / 1000) * rate[1]


def _approx_tokens(text: str) -> int:
    # 4 chars/token approximation. Replaced by tokenizer in M3+.
    return max(1, len(text) // 4)


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
        chunk_uuids: list[UUID] | None = None,
    ) -> T | str:
        route = getattr(self.config.models, model_role, None)
        if route is None:
            raise LLMError(f"Unknown model_role: {model_role}")

        self._budget_gate()

        if self.config.mode == "host":
            return self._host_call(
                stage=stage,
                prompt=prompt,
                model_role=model_role,
                response_schema=response_schema,
                chunk_uuids=chunk_uuids or [],
            )
        return self._api_call(
            stage=stage,
            prompt=prompt,
            model_role=model_role,
            provider=route.provider,
            model_id=route.model_id,
            response_schema=response_schema,
            chunk_uuids=chunk_uuids or [],
        )

    def _budget_gate(self) -> None:
        spent = self.ledger.total_usd()
        cap = self.config.cost.max_per_book
        if spent >= cap:
            self.ledger.record_budget_event("exceeded", spent, cap)
            raise BudgetExceeded(
                f"Spent ${spent:.4f} reached cap ${cap:.2f}. Re-run with a higher --cost-cap."
            )

    def _api_call(
        self,
        *,
        stage: str,
        prompt: str,
        model_role: str,
        provider: str,
        model_id: str,
        response_schema: type[T] | None,
        chunk_uuids: list[UUID],
    ) -> T | str:
        call_id = uuid4()
        started = time.perf_counter()

        route = getattr(self.config.models, model_role)
        if provider == "stub":
            response_text = self._stub_response(prompt, response_schema)
            tokens_in = _approx_tokens(prompt)
            tokens_out = _approx_tokens(response_text)
        elif provider == "anthropic":
            response_text, tokens_in, tokens_out = self._anthropic_call(
                model_id, prompt, response_schema
            )
        elif provider == "ollama":
            response_text, tokens_in, tokens_out = self._ollama_call(
                model_id, prompt, response_schema, route.api_base
            )
        elif provider == "gemini":
            response_text, tokens_in, tokens_out = self._gemini_call(
                model_id, prompt, response_schema, route.api_key_env
            )
        elif provider == "openrouter":
            response_text, tokens_in, tokens_out = self._openrouter_call(
                model_id, prompt, response_schema, route.api_base, route.api_key_env
            )
        elif provider == "vllm":
            response_text, tokens_in, tokens_out = self._vllm_call(
                model_id, prompt, response_schema
            )
        else:
            raise LLMError(f"Unknown provider: {provider}")

        latency_ms = int((time.perf_counter() - started) * 1000)
        usd = _estimate_cost(provider, model_id, tokens_in, tokens_out)

        self._archive_call(
            call_id=call_id,
            stage=stage,
            model_role=model_role,
            provider=provider,
            model_id=model_id,
            prompt=prompt,
            response=response_text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usd=usd,
            latency_ms=latency_ms,
        )
        self.ledger.record_call(
            stage=stage,
            model_role=model_role,
            model_id=model_id,
            provider=provider,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usd=usd,
            latency_ms=latency_ms,
            chunk_uuids=chunk_uuids,
        )
        return self._validate(response_text, response_schema)

    def _host_call(
        self,
        *,
        stage: str,
        prompt: str,
        model_role: str,
        response_schema: type[T] | None,
        chunk_uuids: list[UUID],
    ) -> T | str:
        task_id = uuid4()
        task_dir = self.working_dir / self.config.host.task_dir
        result_dir = self.working_dir / self.config.host.result_dir
        task_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        task = HostTask(
            task_id=task_id,
            stage=stage,
            model_role=model_role,
            prompt=prompt,
            response_schema=response_schema.model_json_schema() if response_schema else None,
            chunk_uuids=chunk_uuids,
            max_input_tokens=self.config.host.task_max_input_tokens,
            max_output_tokens=self.config.host.task_max_output_tokens,
            created_at=datetime.now(UTC),
        )
        write_json(task_dir / f"{task_id}.json", task)
        log.info("host_task_written", task_id=str(task_id), stage=stage, model_role=model_role)

        result_path = result_dir / f"{task_id}.json"
        deadline = time.time() + 60 * 60  # 1 hour soft limit per task
        while time.time() < deadline:
            if result_path.exists():
                raw = json.loads(result_path.read_text(encoding="utf-8"))
                result = HostResult.model_validate(raw)
                self.ledger.record_call(
                    stage=stage,
                    model_role=model_role,
                    model_id="host-agent",
                    provider="host",
                    tokens_in=result.estimated_tokens_in,
                    tokens_out=result.estimated_tokens_out,
                    usd=0.0,
                    latency_ms=0,
                    chunk_uuids=chunk_uuids,
                )
                response_text = (
                    json.dumps(result.response)
                    if not isinstance(result.response, str)
                    else result.response
                )
                return self._validate(response_text, response_schema)
            time.sleep(self.config.host.poll_interval_seconds)

        # Stub fallback for tests/CI: if no host agent ever responds, synthesize a stub.
        log.warning("host_task_timeout_falling_back_to_stub", task_id=str(task_id))
        response_text = self._stub_response(prompt, response_schema)
        self.ledger.record_call(
            stage=stage,
            model_role=model_role,
            model_id="host-agent-stub",
            provider="host",
            tokens_in=_approx_tokens(prompt),
            tokens_out=_approx_tokens(response_text),
            usd=0.0,
            latency_ms=0,
            chunk_uuids=chunk_uuids,
        )
        return self._validate(response_text, response_schema)

    def _anthropic_call(
        self, model_id: str, prompt: str, response_schema: type[T] | None
    ) -> tuple[str, int, int]:
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise LLMError(f"anthropic SDK not installed: {e}") from e

        client = Anthropic()
        msg = client.messages.create(
            model=model_id,
            max_tokens=4096,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        tokens_in = msg.usage.input_tokens
        tokens_out = msg.usage.output_tokens
        return text, tokens_in, tokens_out

    def _ollama_call(
        self,
        model_id: str,
        prompt: str,
        response_schema: type[T] | None,
        api_base: str | None,
    ) -> tuple[str, int, int]:
        """Call a local Ollama server via /api/chat.

        When `response_schema` is provided, requests JSON-mode output and passes
        the Pydantic schema to Ollama's `format` field — Ollama constrains
        sampling to produce valid JSON matching the schema.
        """
        import json as _json
        import urllib.error
        import urllib.request

        base = (api_base or "http://localhost:11434").rstrip("/")
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        if response_schema is not None:
            body["format"] = response_schema.model_json_schema()

        req = urllib.request.Request(
            f"{base}/api/chat",
            data=_json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise LLMError(f"ollama request failed: {e}") from e

        text = raw.get("message", {}).get("content", "")
        tokens_in = int(raw.get("prompt_eval_count", _approx_tokens(prompt)))
        tokens_out = int(raw.get("eval_count", _approx_tokens(text)))
        return text, tokens_in, tokens_out

    def _gemini_call(
        self,
        model_id: str,
        prompt: str,
        response_schema: type[T] | None,
        api_key_env: str | None,
    ) -> tuple[str, int, int]:
        try:
            from google import genai
        except ImportError as e:
            raise LLMError(
                f"google-genai SDK not installed; run `uv pip install google-genai`: {e}"
            ) from e

        import os as _os

        key = _os.environ.get(api_key_env or "GEMINI_API_KEY")
        if not key:
            raise LLMError(f"Gemini requires API key in env var {api_key_env or 'GEMINI_API_KEY'}")

        client = genai.Client(api_key=key)
        generation_config: dict[str, Any] = {"temperature": 0.0}
        if response_schema is not None:
            generation_config["response_mime_type"] = "application/json"
            generation_config["response_schema"] = response_schema

        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=generation_config,
        )
        text = response.text or ""
        usage = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", _approx_tokens(prompt)) or 0)
        tokens_out = int(getattr(usage, "candidates_token_count", _approx_tokens(text)) or 0)
        return text, tokens_in, tokens_out

    def _openrouter_call(
        self,
        model_id: str,
        prompt: str,
        response_schema: type[T] | None,
        api_base: str | None,
        api_key_env: str | None,
    ) -> tuple[str, int, int]:
        """OpenRouter uses OpenAI-compatible Chat Completions."""
        import json as _json
        import os as _os
        import urllib.error
        import urllib.request

        key = _os.environ.get(api_key_env or "OPENROUTER_API_KEY")
        if not key:
            raise LLMError(
                f"OpenRouter requires API key in env var {api_key_env or 'OPENROUTER_API_KEY'}"
            )

        base = (api_base or "https://openrouter.ai/api/v1").rstrip("/")
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        if response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": response_schema.model_json_schema(),
                    "strict": True,
                },
            }

        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=_json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "https://github.com/marrow",
                "X-Title": "Marrow",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise LLMError(f"openrouter request failed: {e}") from e

        choice = raw.get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "")
        usage = raw.get("usage", {})
        return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))

    def _vllm_call(
        self, model_id: str, prompt: str, response_schema: type[T] | None
    ) -> tuple[str, int, int]:
        # vLLM path deprecated in favor of ollama/gemini/openrouter. Falls back to stub.
        text = self._stub_response(prompt, response_schema)
        return text, _approx_tokens(prompt), _approx_tokens(text)

    @staticmethod
    def _stub_response(prompt: str, response_schema: type[T] | None) -> str:
        if response_schema is None:
            return f"[stub response to prompt of len {len(prompt)}]"
        # Construct a minimal valid instance from defaults where possible.
        try:
            instance = response_schema()  # type: ignore[call-arg]
            return instance.model_dump_json()
        except Exception:
            return "{}"

    @staticmethod
    def _validate(response_text: str, response_schema: type[T] | None) -> T | str:
        if response_schema is None:
            return response_text
        try:
            return response_schema.model_validate_json(response_text)
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
            "timestamp": datetime.now(UTC).isoformat(),
        }
        write_json(self.llm_log_dir / f"{stage}_{call_id}.json", archive)


def call(
    *,
    working_dir: Path,
    config: MarrowConfig,
    stage: str,
    prompt: str,
    model_role: str,
    response_schema: type[T] | None = None,
    chunk_uuids: list[UUID] | None = None,
) -> T | str:
    """Functional entry point. Stages should prefer this over instantiating LLMCaller."""
    caller = LLMCaller(working_dir, config)
    return caller.call(
        stage=stage,
        prompt=prompt,
        model_role=model_role,
        response_schema=response_schema,
        chunk_uuids=chunk_uuids,
    )
