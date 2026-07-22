"""Server observability: structured stderr logs, per-tool metrics, and optional
result history — all stdlib, so the zero-dependency guarantee holds.

Logs go to **stderr**, deliberately. stdout is the JSON-RPC channel the MCP
client is parsing line by line; a stray log line there corrupts the protocol.
Getting that right is the single most important thing in a stdio server, so it's
worth stating in code: this module never writes to stdout.

Every ``tools/call`` produces one JSON log line (tool, duration, isError, and the
size of each argument) and updates an in-process counter. If ``MCPTOOLS_DB`` is
set, the results of ``grade_answer`` and ``model_drift`` are also persisted via
``store.ResultStore`` — off by default, so the default path stays pure.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from typing import Optional

from .store import ResultStore

logger = logging.getLogger("mcptools")

# Attributes already on a bare LogRecord; anything else was passed via extra=.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"taskName"}

# Tools whose actual output is worth keeping a trail of.
_PERSISTED_TOOLS = {"grade_answer", "model_drift"}


class _StderrJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name,
                   "msg": record.getMessage()}
        for key, val in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = val
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Attach a JSON formatter to a stderr handler. Idempotent (replaces its own
    named handler), and it never touches stdout."""
    level = os.environ.get("MCPTOOLS_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_StderrJson())
    handler.set_name("mcptools-stderr")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [h for h in root.handlers if h.get_name() != "mcptools-stderr"]
    root.addHandler(handler)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.errors = 0
        self.by_tool: dict[str, dict] = {}

    def record(self, tool: str, is_error: bool, ms: float) -> None:
        with self._lock:
            self.calls += 1
            self.errors += int(is_error)
            t = self.by_tool.setdefault(tool, {"calls": 0, "errors": 0, "total_ms": 0.0})
            t["calls"] += 1
            t["errors"] += int(is_error)
            t["total_ms"] = round(t["total_ms"] + ms, 3)

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "errors": self.errors,
                    "by_tool": {k: dict(v) for k, v in self.by_tool.items()}}


metrics = Metrics()

_store_cache: dict[str, ResultStore] = {}


def _current_store() -> Optional[ResultStore]:
    """The result store for MCPTOOLS_DB, or None if it's unset. Cached per path so
    a long-lived server opens the database once."""
    path = os.environ.get("MCPTOOLS_DB")
    if not path:
        return None
    if path not in _store_cache:
        _store_cache[path] = ResultStore(path)
    return _store_cache[path]


def _result_text(result: Optional[dict]) -> str:
    if not result:
        return ""
    try:
        return result["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def record_call(name: Optional[str], args: dict, result: Optional[dict],
                is_error: bool, ms: float) -> None:
    """Log the call, update metrics, and persist the result for the tools we keep
    a trail of. Never raises into the request path — observability must not be
    able to fail a tool call."""
    tool = name or "?"
    try:
        arg_sizes = {k: len(str(v)) for k, v in (args or {}).items()}
        metrics.record(tool, is_error, ms)
        logger.info("tools/call", extra={
            "tool": tool,
            "duration_ms": round(ms, 2),
            "isError": is_error,
            "arg_sizes": arg_sizes,
        })
        if not is_error and tool in _PERSISTED_TOOLS:
            store = _current_store()
            if store is not None:
                text = _result_text(result)
                summary = text.splitlines()[0][:200] if text else tool
                store.record(tool, summary, detail={"args": args, "result": text})
    except Exception as exc:  # noqa: BLE001 — never let telemetry break a tool call
        logger.warning("observability error", extra={"error": str(exc)})
