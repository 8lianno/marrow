"""Book-slug derivation. Deterministic from the source path."""

from __future__ import annotations

import re
from pathlib import Path


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "untitled"


def book_slug(book_path: Path) -> str:
    return slugify(book_path.stem)
