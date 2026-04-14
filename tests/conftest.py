"""Shared fixtures: synthetic PDFs + isolated runs_dir per test."""

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

# Hierarchical fixture for M1: chapters with subsections + a small table.
NESTED_FIXTURE = [
    {
        "title": "Chapter 1: Principles",
        "paragraphs": [
            "Strategy begins with intent and ends with execution.",
            "Without intent, execution is motion.",
        ],
        "subsections": [
            {
                "title": "Section 1.1: Intent",
                "paragraphs": [
                    "Intent is the answer to why before how.",
                    "Clarity of intent compresses decisions downstream.",
                ],
            },
            {
                "title": "Section 1.2: Execution",
                "paragraphs": [
                    "Execution is the loop that closes intent against reality.",
                ],
            },
        ],
    },
    {
        "title": "Chapter 2: Measurement",
        "paragraphs": [
            "What you cannot measure, you cannot improve.",
        ],
        "table": [
            ["Metric", "Target", "Status"],
            ["Coverage", "92%", "OK"],
            ["Latency", "250ms", "OK"],
        ],
    },
]


@pytest.fixture(scope="session")
def synthetic_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Three flat chapters; smoke fixture used since M0."""
    out = tmp_path_factory.mktemp("fixture") / "synthetic.pdf"
    _draw_flat_pdf(out, FIXTURE_PARAGRAPHS)
    return out


@pytest.fixture(scope="session")
def nested_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Chapters with subsections and a table; M1 hierarchy fixture."""
    out = tmp_path_factory.mktemp("fixture-nested") / "nested.pdf"
    _draw_nested_pdf(out, NESTED_FIXTURE)
    return out


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    d.mkdir()
    return d


# ---- Helpers ----


def _draw_flat_pdf(out: Path, fixture: dict[str, list[str]]) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out), pagesize=letter)
    _, height = letter

    for chapter_title, paragraphs in fixture.items():
        y = height - 72
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, y, chapter_title)
        y -= 36
        c.setFont("Helvetica", 12)
        for para in paragraphs:
            for line in _wrap(para, 80):
                c.drawString(72, y, line)
                y -= 16
            y -= 8
        c.showPage()
    c.save()


def _draw_nested_pdf(out: Path, fixture: list[dict]) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out), pagesize=letter)
    _, height = letter

    for chapter in fixture:
        y = height - 72
        c.setFont("Helvetica-Bold", 20)
        c.drawString(72, y, chapter["title"])
        y -= 30
        c.setFont("Helvetica", 12)
        for para in chapter["paragraphs"]:
            for line in _wrap(para, 80):
                c.drawString(72, y, line)
                y -= 16
            y -= 8

        for sub in chapter.get("subsections", []):
            c.setFont("Helvetica-Bold", 14)
            y -= 8
            c.drawString(72, y, sub["title"])
            y -= 22
            c.setFont("Helvetica", 12)
            for para in sub["paragraphs"]:
                for line in _wrap(para, 80):
                    c.drawString(72, y, line)
                    y -= 16
                y -= 8

        if "table" in chapter:
            y -= 12
            c.setFont("Helvetica", 11)
            for row in chapter["table"]:
                c.drawString(72, y, " | ".join(row))
                y -= 16

        c.showPage()
    c.save()


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        cur.append(w)
        if sum(len(x) for x in cur) + len(cur) - 1 > width:
            cur.pop()
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines
