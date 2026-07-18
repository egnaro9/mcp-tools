"""The two tools that read live systems.

Formatting and parsing are pinned against fixtures so CI never depends on a
network round-trip; the network path itself is only checked for *failing
gracefully*, since a tool that raises kills the server for every other tool.
"""
import pytest

from mcptools.live import (compare_runs, model_drift, summarize_comparison, summarize_drift)
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
