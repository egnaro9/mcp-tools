"""Server observability: per-tool metrics, stderr-only JSON logging, and the
opt-in result history."""
import json

from mcptools import obs, server


def _call(name, args):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})


def test_calls_are_counted_with_a_per_tool_breakdown():
    before = obs.metrics.snapshot()["calls"]
    _call("calc", {"expression": "2 + 2"})
    snap = obs.metrics.snapshot()
    assert snap["calls"] == before + 1
    assert snap["by_tool"]["calc"]["calls"] >= 1


def test_a_tool_error_increments_the_error_count():
    before = obs.metrics.snapshot()["by_tool"].get("calc", {}).get("errors", 0)
    _call("calc", {"expression": "__import__('os')"})       # rejected → isError result
    assert obs.metrics.snapshot()["by_tool"]["calc"]["errors"] == before + 1


def test_log_line_is_json_on_stderr_never_stdout(capsys):
    obs.configure_logging()                                 # binds to capsys's stderr
    _call("calc", {"expression": "1 + 1"})
    captured = capsys.readouterr()
    assert captured.out == ""                               # nothing leaks to the protocol channel
    line = [ln for ln in captured.err.splitlines() if '"tools/call"' in ln][-1]
    obj = json.loads(line)
    assert obj["tool"] == "calc"
    assert obj["isError"] is False
    assert obj["arg_sizes"]["expression"] == len("1 + 1")   # arg sizes, not arg values


def test_persists_grade_answer_only_when_db_is_configured(tmp_path, monkeypatch):
    from mcptools.store import ResultStore
    db = str(tmp_path / "h.db")
    monkeypatch.setenv("MCPTOOLS_DB", db)
    obs._store_cache.clear()                                # don't reuse another path's handle
    _call("grade_answer", {"answer": "The sky is green.",
                           "sources": ["The sky appears blue from Rayleigh scattering."]})
    rows = ResultStore(db).recent(tool="grade_answer")
    assert len(rows) == 1
    assert rows[0]["summary"]                                # a non-empty finding was stored
