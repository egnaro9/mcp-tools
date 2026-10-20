"""The two tools that read live systems.

Formatting and parsing are pinned against fixtures so CI never depends on a
network round-trip; the network path itself is only checked for *failing
gracefully*, since a tool that raises kills the server for every other tool.
"""
import json
import os
import pathlib

import pytest

from mcptools.live import (
    BOARD_URL,
    _fetch,
    compare_runs,
    model_drift,
    summarize_comparison,
    summarize_drift,
)
from mcptools.server import handle
from mcptools.tools import ToolError

BOARD = {"updated": "2026-07-18T19:00:00Z", "series": {
    "mock:stable": [{"t": "x", "acc": 1.0}],                       # must be ignored
    "openai:gpt-5": [
        {"t": "a", "acc": 1.0, "latency_ms": 1200.0, "out_chars": 5.0,
         "reliability": 1.0, "refusal_rate": 0.0},
        {"t": "b", "acc": 0.95, "latency_ms": 1800.0, "out_chars": 6.0,
         "reliability": 1.0, "refusal_rate": 0.0}],
    "meta:llama-3.1-8b": [
        {"t": "a", "acc": 0.59, "latency_ms": 131.0, "out_chars": 12.0,
         "reliability": 1.0, "refusal_rate": 0.0}],
}}


def test_drift_reports_latest_and_the_move():
    out = summarize_drift(BOARD, "gpt-5")
    assert "openai:gpt-5" in out
    assert "95.0%" in out                      # latest accuracy
    assert "▼" in out and "5.0%" in out        # dropped vs previous run
    assert "▲" in out                          # latency went up
    assert "mock:" not in out                  # the test fixture model is hidden


def test_drift_matches_loosely_and_lists_when_unknown():
    assert "meta:llama-3.1-8b" in summarize_drift(BOARD, "llama")
    miss = summarize_drift(BOARD, "mistral")
    assert "No tracked model matches" in miss and "openai:gpt-5" in miss


def test_drift_says_so_when_there_is_no_trend_yet():
    out = summarize_drift(BOARD, "llama-3.1-8b")
    assert "no trend" in out.lower()


def test_drift_with_no_query_lists_every_model():
    out = summarize_drift(BOARD, "")
    assert "openai:gpt-5" in out and "meta:llama-3.1-8b" in out


def test_drift_handles_an_empty_board():
    assert "no recorded runs" in summarize_drift({"series": {}}, "")


def test_comparison_leads_with_the_verdict():
    out = summarize_comparison(
        {"verdict": "regressed", "is_regression": True,
         "regressions": [{"q": "is aspirin safe?", "metric": "faithfulness",
                          "before": 0.9, "after": 0.6}],
         "improvements": [], "newly_flagged": ["is aspirin safe?"]}, "rag-eval-lab")
    assert "regressed" in out and "1 regression" in out
    assert "aspirin" in out and "0.9" in out and "0.6" in out


def test_comparison_says_when_nothing_moved():
    out = summarize_comparison({"verdict": "unchanged", "regressions": [], "improvements": []}, "s")
    assert "Nothing moved" in out


def test_network_failure_is_a_tool_error_not_a_crash():
    def boom(url): raise ToolError("could not reach the board")
    with pytest.raises(ToolError):
        model_drift("gpt-5", fetch=boom)
    with pytest.raises(ToolError):
        compare_runs("rag-eval-lab", fetch=boom)


def test_compare_needs_a_suite_name():
    with pytest.raises(ToolError):
        compare_runs("   ", fetch=lambda u: {})


def test_too_few_runs_is_explained_not_an_error():
    assert "not enough runs" in compare_runs("x", fetch=lambda u: {"verdict": None})


def test_both_are_exposed_over_mcp():
    tools = {t["name"] for t in handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert {"model_drift", "compare_runs"} <= tools


def test_a_failing_live_call_comes_back_as_isError_not_an_exception(monkeypatch):
    monkeypatch.setattr("mcptools.live._fetch",
                        lambda url: (_ for _ in ()).throw(ToolError("network down")))
    r = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "model_drift", "arguments": {"model": "gpt-5"}}})
    assert r["result"]["isError"] is True
    assert "network down" in r["result"]["content"][0]["text"]


# ── the real board, recorded ───────────────────────────────────────────
# model_drift returned HTTP 404 from 2026-08-30 until this was fixed: model-drift
# renamed dashboard/metrics.json to dashboard/drift_board.json and every test here
# passed, because each one handed summarize_drift a hand-written dict and none of them
# looked at the URL. These pin the URL to a recording of the real file and parse the
# real schema. Re-record with scripts/record_drift_board.py.
FIXTURES = pathlib.Path(__file__).parent / "fixtures"
RECORDED = json.loads((FIXTURES / "drift_board.json").read_text())
SOURCE = json.loads((FIXTURES / "drift_board.source.json").read_text())


def _serve_only_the_recorded_url(url: str) -> dict:
    """A fetcher that answers the way GitHub does: the recorded path, or a 404."""
    if url != SOURCE["url"]:
        raise ToolError(f"{url} returned HTTP 404")
    return RECORDED


def test_the_board_url_is_the_one_the_fixture_was_recorded_from():
    """The old constant ended in dashboard/metrics.json and fails this."""
    assert SOURCE["http_status"] == 200
    assert BOARD_URL == SOURCE["url"], (
        f"model_drift fetches {BOARD_URL} but the board was recorded from {SOURCE['url']}")


def test_model_drift_reads_the_recorded_board_end_to_end():
    out = model_drift("gpt-5-mini", fetch=_serve_only_the_recorded_url)
    assert "openai:gpt-5-mini" in out
    assert "newest run 2026-10-19" in out
    assert "accuracy" in out and "%" in out


def test_a_series_the_board_stopped_updating_is_labelled_not_current():
    """The two Meta series' last points are 2026-08-16; the board's newest is 2026-10-19.
    Shown unlabelled, a reader takes two-month-old numbers as today's standings."""
    out = model_drift("llama", fetch=_serve_only_the_recorded_url)
    for key in ("meta:llama-3.1-8b", "meta:llama-3.3-70b"):
        assert key in out
    assert out.count("NOT CURRENT") == 2
    assert "Last run 2026-08-16" in out


def test_the_full_listing_leaves_stopped_series_out_and_names_them():
    out = model_drift("", fetch=_serve_only_the_recorded_url)
    body, _, footer = out.partition("No longer updated")
    assert footer, f"the stopped series were not named: {out}"
    assert "llama" not in body
    assert "meta:llama-3.1-8b (last run 2026-08-16)" in footer
    assert "meta:llama-3.3-70b (last run 2026-08-16)" in footer
    live = {k for k, v in RECORDED["series"].items()
            if not k.startswith("mock:") and v[-1]["t"].startswith("2026-10-19")}
    assert len(live) == 17
    assert all(f"\n{k}  (" in body for k in live)
    assert "NOT CURRENT" not in out


def test_an_unknown_model_lists_current_and_stopped_separately():
    out = model_drift("mistral", fetch=_serve_only_the_recorded_url)
    tracked, _, stopped = out.partition("No longer updated")
    assert "openai:gpt-5" in tracked and "llama" not in tracked
    assert "meta:llama-3.1-8b" in stopped


def test_accuracy_under_the_reliability_floor_is_not_reported_as_a_move():
    """The board publishes rel_floor because a rate-limited run scores failed calls as
    wrong. 1.00 -> 0.30 accuracy at 20% reliability is an outage, not a regression."""
    ok = {"t": "2026-10-18T00:00:00Z", "acc": 1.0, "latency_ms": 900.0, "out_chars": 5.0,
          "reliability": 1.0, "refusal_rate": 0.0}
    bad = {**ok, "t": "2026-10-19T00:00:00Z", "acc": 0.30, "reliability": 0.20}
    out = summarize_drift({"rel_floor": 0.5, "series": {"openai:gpt-x": [ok, bad]}}, "gpt-x")
    acc_line = next(line for line in out.splitlines() if line.lstrip().startswith("accuracy"))
    assert "not trusted" in acc_line and "▼" not in acc_line
    rel_line = next(line for line in out.splitlines() if line.lstrip().startswith("reliability"))
    assert "▼" in rel_line, "the reliability drop is the true signal and must still show"


@pytest.mark.skipif(os.environ.get("MCPTOOLS_LIVE") != "1",
                    reason="network; CI runs it with MCPTOOLS_LIVE=1, including on a schedule")
def test_the_board_url_serves_the_board_live():
    """The only test that can see the next upstream rename: GET the real constant.
    A 404 surfaces from _fetch as ToolError and fails this."""
    board = _fetch(BOARD_URL)
    real = {k: v for k, v in board["series"].items() if not k.startswith("mock:") and v}
    assert real, "the board answered but carries no real series"
    point = next(iter(real.values()))[-1]
    assert {"t", "acc", "latency_ms", "out_chars", "reliability", "refusal_rate"} <= point.keys()
