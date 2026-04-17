"""Deterministic content-addressed UUID5 derivation.

Every artifact UUID in Marrow is UUID5(NAMESPACE, MD5(canonical_input)). This guarantees
re-runs on identical inputs produce byte-identical IDs across machines and Python versions.
"""

from __future__ import annotations

import hashlib
from uuid import NAMESPACE_DNS, UUID, uuid5

# Stable namespace for all Marrow content-addressed UUIDs. Do not change.
NAMESPACE = uuid5(NAMESPACE_DNS, "marrow.lossless-book-to-brief")


def _content_uuid(*parts: object) -> UUID:
    canonical = "\x1f".join(_normalize(p) for p in parts)
    digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()
    return uuid5(NAMESPACE, digest)


def _normalize(part: object) -> str:
    if isinstance(part, list | tuple):
        return "/".join(_normalize(p) for p in part)
    if isinstance(part, UUID):
        return str(part)
    return str(part)


def paragraph_id(text: str, chapter_path: list[str], page_start: int) -> UUID:
    return _content_uuid("paragraph", text, chapter_path, page_start)


def section_id(title: str, level: int, parent_path: list[str]) -> UUID:
    return _content_uuid("section", title, level, parent_path)
