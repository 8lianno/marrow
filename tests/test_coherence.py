"""Tests for coherence stage utilities."""

from __future__ import annotations

from marrow.stages.stage_05_coherence import _fuzzy_contains, _normalize_text


def test_exact_substring_match() -> None:
    assert _fuzzy_contains("This chapter introduces System 1 and System 2.", "System 1", 0.7)


def test_exact_match_case_insensitive() -> None:
    assert _fuzzy_contains("The HEDONIC TREADMILL explains adaptation.", "hedonic treadmill", 0.7)


def test_normalized_match_strips_punctuation() -> None:
    assert _fuzzy_contains(
        "The author's so-called 'peak-end rule' drives perception.",
        "peak end rule",
        0.7,
    )


def test_token_window_match_with_variation() -> None:
    haystack = (
        "Kahneman divides cognition into System 1, which operates automatically, "
        "and System 2, which allocates attention to effortful mental activities."
    )
    # "System 1 vs System 2" — the words are present but not as a contiguous phrase
    assert _fuzzy_contains(haystack, "System 1 vs System 2", 0.5)


def test_missing_needle_returns_false() -> None:
    assert not _fuzzy_contains(
        "This chapter is about behavioral economics and nudge theory.",
        "quantum entanglement",
        0.7,
    )


def test_short_needle_uses_substring() -> None:
    assert _fuzzy_contains("The nudge framework is central.", "nudge", 0.7)
    assert not _fuzzy_contains("The push framework is central.", "nudge", 0.7)


def test_citation_tokens_dont_interfere() -> None:
    """After stripping [p:uuid] tokens, matching should work cleanly."""
    import re

    text_with_citations = (
        "The anchoring effect [p:abc-123-def] demonstrates how initial "
        "values influence [p:ghi-456-jkl] subsequent judgments."
    )
    clean = re.sub(r"\[p:[a-f0-9-]+\]", "", text_with_citations)
    assert _fuzzy_contains(clean, "anchoring effect", 0.7)


def test_normalize_text() -> None:
    assert _normalize_text("Hello, World!  How's   it?") == "hello world how s it"
