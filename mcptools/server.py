"""A Model Context Protocol server, implemented from the spec — no MCP SDK.

MCP is JSON-RPC 2.0. A local server speaks it over stdio: newline-delimited JSON
in on stdin, out on stdout. The lifecycle is `initialize` → `notifications/
initialized` → then `tools/list` and `tools/call`. That's the whole surface a
tool server needs, and it's small enough to implement directly — which is also
the point: it makes the protocol legible instead of hidden behind a library.

    python -m mcptools          # serve on stdio (what an MCP client launches)

The dispatch is a plain dict of method → handler. Notifications (no `id`) get no
reply. A tool that raises returns an MCP tool-error result (`isError: true`), not
a protocol error — the model should see the failure and adapt, not have the call
torn down. Unknown *methods* are protocol errors (-32601); unknown *tools* and
bad arguments are -32602, per the spec.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, Optional, TextIO

from . import __version__
from .tools import ToolError, calc, search

PROTOCOL_VERSION = "2025-06-18"

# The advertised tools. inputSchema is JSON Schema — how the model learns to call them.
TOOLS = [
    {
        "name": "calc",
        "description": "Evaluate an arithmetic expression safely (no code execution; "
                       "names, calls and imports are rejected).",
        "inputSchema": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "e.g. '2 + 3 * 4'"}},
            "required": ["expression"],
        },
    },
    {
        "name": "search",
        "description": "BM25 keyword search over a small bundled document corpus; "
                       "returns the top matches with their scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search terms"},
                "k": {"type": "integer", "description": "how many results (default 3)", "default": 3},
            },
            "required": ["query"],
        },
    },
]

_DISPATCH: Dict[str, Callable[[dict], Any]] = {}


def _text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _method(name: str):
    def deco(fn):
        _DISPATCH[name] = fn
        return fn
    return deco


@_method("initialize")
def _initialize(params: dict) -> dict:
    # Echo the client's protocol version if we support it; otherwise offer ours.
    client_version = params.get("protocolVersion", PROTOCOL_VERSION)
    return {
        "protocolVersion": client_version if client_version == PROTOCOL_VERSION else PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "mcp-tools", "version": __version__},
        "instructions": "Two safe tools: `calc` (arithmetic, no eval) and `search` (BM25 over a bundled corpus).",
    }


@_method("ping")
def _ping(params: dict) -> dict:
    return {}


@_method("tools/list")
def _tools_list(params: dict) -> dict:
    return {"tools": TOOLS}


@_method("tools/call")
def _tools_call(params: dict) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    try:
        if name == "calc":
            return _text_result(calc(str(args["expression"])))
        if name == "search":
            k = int(args.get("k", 3))
            return _text_result(search(str(args["query"]), k=max(1, min(k, 10))))
    except ToolError as e:
        return _text_result(f"error: {e}", is_error=True)
    except KeyError as e:
        raise _RpcError(-32602, f"missing required argument: {e}")
    raise _RpcError(-32602, f"unknown tool: {name!r}")


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def handle(message: dict) -> Optional[dict]:
    """Process one JSON-RPC message. Returns a response dict, or None for a notification."""
    mid = message.get("id")
    method = message.get("method", "")
    is_notification = mid is None

    handler = _DISPATCH.get(method)
    if handler is None:
        # Notifications we don't handle (e.g. notifications/initialized) are fine to ignore.
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}

    try:
        result = handler(message.get("params") or {})
    except _RpcError as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": e.message}}

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
