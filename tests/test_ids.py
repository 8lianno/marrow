"""UUID derivation determinism."""

from __future__ import annotations

from marrow.ids import paragraph_id, section_id


def test_paragraph_id_deterministic() -> None:
    a = paragraph_id("Hello world.", ["Chapter 1"], 1)
    b = paragraph_id("Hello world.", ["Chapter 1"], 1)
    assert a == b


def test_paragraph_id_changes_with_text() -> None:
    a = paragraph_id("Hello world.", ["Chapter 1"], 1)
    b = paragraph_id("Hello mars.", ["Chapter 1"], 1)
    assert a != b


def test_section_id_changes_with_level() -> None:
    a = section_id("Title", 1, [])
    b = section_id("Title", 2, [])
    assert a != b
