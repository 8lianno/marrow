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


def chunk_uuid(text: str, book_slug: str, chapter_path: list[str]) -> UUID:
    return _content_uuid("chunk", text, book_slug, chapter_path)


def entity_id(canonical_name: str, book_slug: str) -> UUID:
    return _content_uuid("entity", canonical_name.lower(), book_slug)


def relation_id(subject_id: UUID, predicate: str, object_id: UUID, book_slug: str) -> UUID:
    return _content_uuid("relation", subject_id, predicate.lower(), object_id, book_slug)


def community_id(entity_ids: list[UUID], book_slug: str) -> UUID:
    return _content_uuid("community", sorted(str(e) for e in entity_ids), book_slug)


def claim_id(claim_text: str, book_slug: str) -> UUID:
    return _content_uuid("claim", claim_text, book_slug)


def section_id(title: str, level: int, parent_path: list[str]) -> UUID:
    return _content_uuid("section", title, level, parent_path)


def question_id(question_text: str, chapter_path: list[str]) -> UUID:
    return _content_uuid("question", question_text, chapter_path)
