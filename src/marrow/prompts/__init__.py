"""Jinja2 prompt templates. Keep templates in this package, load via `render`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPT_DIR = Path(__file__).parent
_ENV = Environment(
    loader=FileSystemLoader(_PROMPT_DIR),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def render(template_name: str, **context: Any) -> str:
    """Render a Jinja2 template from marrow/prompts/. Missing vars raise."""
    template = _ENV.get_template(template_name)
    return template.render(**context)
