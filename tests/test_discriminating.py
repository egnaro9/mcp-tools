"""Inputs where the wrong implementation gives a different answer.

A mutation audit replaced eight expressions in this package with constants, inversions or
their simplest wrong variant, and all 42 tests stayed green. The pattern throughout was a
fixture or an assertion too weak to separate the right behaviour from a plausible wrong one:
`k=1` where one result and ten look alike to the assertion, a check that both arrow glyphs
appear *somewhere* rather than which one goes with a rise, a truthiness assertion that the
fallback value also satisfies.
"""
from __future__ import annotations

import pytest

from mcptools.grading import _content_words, grade_answer
from mcptools.live import summarize_drift
from mcptools.obs import Metrics
from mcptools.tools import BM25

# ───────────────────────────────── BM25 ─────────────────────────────────

def _index() -> BM25:
    # 'rare' appears once; 'common' is everywhere, so IDF alone cannot order these.
    return BM25({
        "many": "rare common common common common common",
        "few": "rare common",
    })


def test_bm25_uses_term_frequency_and_length_and_is_not_a_bare_idf_sum():
    """The whole saturation term could be replaced by 1.0, leaving a sum of IDF. Both docs
    contain both query terms, so an IDF-only score makes them identical; only tf and document
    length can separate them."""
    scores = {doc_id: s for doc_id, _, s in _index().search("rare common", k=10)}
    assert len(scores) == 2
    assert scores["few"] != scores["many"], (
        "two documents containing the same terms scored identically, so tf and length are "
        f"not reaching the score: {scores}")


def test_search_returns_k_results_and_not_a_fixed_number():
    """`scored[:k]` could become `scored[:10]`: the existing test passes k=1, and with only a
    handful of matching docs one result and ten are indistinguishable to an assertion that
    just checks the top hit."""
    docs = {f"d{i}": "alpha beta gamma" for i in range(8)}
    idx = BM25(docs)
    assert len(idx.search("alpha", k=1)) == 1
    assert len(idx.search("alpha", k=3)) == 3
    assert len(idx.search("alpha", k=8)) == 8


# ───────────────────────────────── drift arrows ─────────────────────────────────

def _board(delta: float) -> dict:
    """Two runs of one real model, moving `delta` on accuracy and nothing else."""
    base = {"t": "a", "acc": 0.50, "latency_ms": 100.0, "out_chars": 12.0,
            "reliability": 1.0, "refusal_rate": 0.0}
    return {"series": {"openai:gpt-x": [base, {**base, "acc": round(0.50 + delta, 4)}]}}


def test_a_rise_and_a_fall_get_different_arrows():
    """Both arrows could be inverted: the existing assertion only checks that both glyphs
    appear somewhere in the output, which an inverted mapping satisfies exactly as well."""
    up = summarize_drift(_board(+0.10), "")
    down = summarize_drift(_board(-0.10), "")
    assert "▲" in up and "▼" not in up, f"a rise did not render as a rise: {up}"
    assert "▼" in down and "▲" not in down, f"a fall did not render as a fall: {down}"


def test_mock_series_are_excluded_from_the_board():
    """`if not k.startswith("mock:") and v` could become `if True`, or drop just the prefix
    test. An assertion was written for this but no fixture contained a mock series alongside a
    real one."""
    real = _board(0.10)["series"]["openai:gpt-x"]
    data = {"series": {"mock:fake-model": real, "openai:gpt-x": real}}
    out = summarize_drift(data, "")
    assert "openai:gpt-x" in out
    assert "mock:" not in out and "fake-model" not in out, (
        f"a mock series reached the board: {out}")


def test_an_empty_series_is_not_treated_as_a_real_one():
    """The `and v` half of the same condition."""
    data = {"series": {"openai:empty": [],
                       "openai:gpt-x": _board(0.10)["series"]["openai:gpt-x"]}}
    out = summarize_drift(data, "")
    assert "openai:empty" not in out


# ───────────────────────────────── grading ─────────────────────────────────

def test_the_coverage_threshold_actually_decides():
    """The documented 0.6 default could be swept to 1e-9, 0.3 or 0.85 with the suite green.
    These two claims sit either side of it, so a threshold that has drifted changes a verdict.
    """
    sources = ["The system improves throughput and reduces latency for batch jobs."]
    well_supported = "The system improves throughput for batch jobs."
    barely = "The system improves throughput while orbiting Jupiter on alternate Tuesdays."

    assert grade_answer(well_supported, sources)["claims"][0]["supported"] is True
    assert grade_answer(barely, sources)["claims"][0]["supported"] is False, (
        "a claim mostly made of words absent from the sources must not count as supported")

    # The band that pins the default from ABOVE as well as below. Measured coverage 0.667:
    # supported at 0.6 and not at 0.85, so the default cannot drift either way unnoticed.
    straddles = "The system improves throughput and stability for nightly jobs."
    assert grade_answer(straddles, sources)["claims"][0]["supported"] is True, (
        "a claim covering two thirds of its words must pass the documented 0.6 default")


def test_the_threshold_argument_is_honoured():
    """Both directions on the same input, so the default cannot be quietly moved."""
    sources = ["Alpha beta gamma delta."]
    claim = "Alpha beta epsilon zeta."          # half the content words are supported
    assert grade_answer(claim, sources, threshold=0.4)["claims"][0]["supported"] is True
    assert grade_answer(claim, sources, threshold=0.9)["claims"][0]["supported"] is False


def test_numbers_are_not_content_words():
    """`and not w.isdigit()` could be dropped. Numbers are checked separately by _numbers, so
    letting them back in double-counts them and shifts every coverage ratio."""
    assert "47" not in _content_words("It improves accuracy by 47 percent.")
    assert "improves" in _content_words("It improves accuracy by 47 percent.")


def test_faithfulness_keeps_four_decimals():
    """`round(ok / n, 4)` could become `round(ok / n, 1)`. One claim in three is 0.3333, which
    rounds to 0.3 and loses the distinction from 1 in 4 (0.25 -> 0.2) and 2 in 7 (0.2857)."""
    sources = ["Alpha beta gamma delta."]
    answer = "Alpha beta gamma delta. Quasars levy excise duty. Penguins audit tax law."
    f = grade_answer(answer, sources)["faithfulness"]
    assert f == pytest.approx(1 / 3, abs=1e-4), f"faithfulness lost precision: {f}"


# ───────────────────────────────── metrics ─────────────────────────────────

def test_errors_are_counted_and_time_accumulates():
    """`self.errors += int(is_error)` could become `+= 0`, and `total_ms` could be pinned to
    0.0. Nothing asserted either, so the observability surface could report a clean, instant
    server while it was failing and slow."""
    m = Metrics()
    m.record("search", is_error=False, ms=10.0)
    m.record("search", is_error=True, ms=30.0)
    snap = m.snapshot()
    assert snap["calls"] == 2
    assert snap["errors"] == 1, f"a failed call was not counted: {snap}"
    assert snap["by_tool"]["search"]["total_ms"] == pytest.approx(40.0), snap


# ───────────────────────────────── the persisted trail ─────────────────────────────────

def test_the_persisted_summary_is_the_result_and_not_the_tool_name():
    """`text.splitlines()[0][:200] if text else tool` could collapse to just `tool`. The
    existing assertion is a truthiness check, and the fallback value (the tool name) satisfies
    it exactly as well as a real summary does, so the trail could record "grade_answer" for
    every row and nothing noticed.
    """
    from mcptools import obs

    recorded: list[tuple[str, str, dict | None]] = []

    class _Spy:
        def record(self, tool, summary, detail=None):
            recorded.append((tool, summary, detail))

    original = obs._current_store
    obs._current_store = lambda: _Spy()
    try:
        obs.record_call("grade_answer", args={"answer": "a"},
                        result={"content": [{"type": "text",
                                             "text": "2 of 3 claims not supported\nmore"}]},
                        is_error=False, ms=1.0)
    finally:
        obs._current_store = original

    assert recorded, "nothing reached the store"
    tool, summary, _ = recorded[0]
    assert summary != tool, "the trail recorded the tool name instead of the result"
    assert summary.startswith("2 of 3 claims not supported")
    assert "\n" not in summary, "the summary must be the first line only"
