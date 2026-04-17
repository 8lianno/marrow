"""Shared fixtures: synthetic test books + isolated runs_dir per test."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_PARAGRAPHS = {
    "Chapter 1: Foundations": [
        "All warfare is based on deception.",
        "Therefore, when capable of attacking, feign incapacity.",
        "When near, appear far; when far, appear near.",
    ],
    "Chapter 2: Strategy": [
        "Supreme excellence consists in breaking the enemy's resistance without fighting.",
        "Hence to fight and conquer in all your battles is not supreme excellence.",
        "Know thy enemy and know thyself; in a hundred battles you will never be defeated.",
    ],
    "Chapter 3: Logistics": [
        "An army marches on its stomach.",
        "The line between disorder and order lies in logistics.",
        "Move not unless you see an advantage; use not your troops unless there is something to be gained.",
    ],
}


@pytest.fixture(scope="session")
def synthetic_txt_book(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Plain text book with chapter markers — used for fallback ingest testing."""
    out = tmp_path_factory.mktemp("fixture") / "synthetic.txt"
    lines: list[str] = []
    for chapter_title, paragraphs in FIXTURE_PARAGRAPHS.items():
        lines.append(chapter_title)
        lines.append("")
        for para in paragraphs:
            lines.append(para)
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    d.mkdir()
    return d
