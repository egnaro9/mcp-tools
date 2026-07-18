"""The MCP protocol, exercised — both the handlers directly and the real server
over stdio in a subprocess. An MCP server you can't test without Claude Desktop
open isn't really testable; this one is, because the protocol is just JSON-RPC.
"""
import json
import subprocess
import sys

from mcptools.server import PROTOCOL_VERSION, handle


def rpc(method, params=None, mid=1):
    return handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


# ── the lifecycle ──────────────────────────────────────────────────────
def test_initialize_agrees_on_protocol_version_and_advertises_tools():
    r = rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}})
    res = r["result"]
    assert res["protocolVersion"] == PROTOCOL_VERSION
    assert res["capabilities"]["tools"] is not None
    assert res["serverInfo"]["name"] == "mcp-tools"


def test_initialize_offers_its_own_version_when_client_asks_for_one_we_lack():
    r = rpc("initialize", {"protocolVersion": "1999-01-01"})
    assert r["result"]["protocolVersion"] == PROTOCOL_VERSION  # not the unsupported one


def test_initialized_notification_gets_no_reply():
    # No `id` → a notification → the server must stay silent.
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_a_protocol_error():
    r = rpc("resources/list")   # a capability we didn't advertise
    assert r["error"]["code"] == -32601


# ── tools/list ─────────────────────────────────────────────────────────
def test_tools_list_returns_both_tools_with_schemas():
    tools = {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}
    assert set(tools) == {"calc", "search"}
    assert tools["calc"]["inputSchema"]["required"] == ["expression"]


# ── tools/call: calc ───────────────────────────────────────────────────
def test_calc_evaluates():
    r = rpc("tools/call", {"name": "calc", "arguments": {"expression": "2 + 3 * 4"}})
    assert r["result"]["content"][0]["text"] == "14"
    assert r["result"]["isError"] is False


def test_calc_rejects_code_as_a_tool_error_not_a_crash():
    """The LLM06 guardrail, seen through the protocol: rejection, isError=true —
    the model gets to see it failed, and the server stays up."""
    r = rpc("tools/call", {"name": "calc", "arguments": {"expression": "__import__('os').system('ls')"}})
    assert r["result"]["isError"] is True
    assert "unsafe" in r["result"]["content"][0]["text"].lower()


# ── tools/call: search ─────────────────────────────────────────────────
def test_search_finds_the_relevant_doc():
    r = rpc("tools/call", {"name": "search", "arguments": {"query": "rank aware retrieval metric", "k": 1}})
    text = r["result"]["content"][0]["text"]
    assert "ndcg" in text.lower()


def test_call_unknown_tool_is_a_protocol_error():
    r = rpc("tools/call", {"name": "nope", "arguments": {}})
    assert r["error"]["code"] == -32602


def test_call_missing_argument_is_a_protocol_error():
    r = rpc("tools/call", {"name": "calc", "arguments": {}})
    assert r["error"]["code"] == -32602


# ── end to end: a real subprocess speaking MCP over stdio ──────────────
def test_full_handshake_over_stdio_subprocess():
    """Launch the server the way an MCP client does and speak the real protocol."""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},   # notification, no reply
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "calc", "arguments": {"expression": "40 + 2"}}},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in msgs)
    proc = subprocess.run([sys.executable, "-m", "mcptools"], input=stdin,
                          capture_output=True, text=True, timeout=30)
    replies = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]

    # Three requests → three replies; the notification produced none.
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[0]["result"]["serverInfo"]["name"] == "mcp-tools"
    assert {t["name"] for t in replies[1]["result"]["tools"]} == {"calc", "search"}
    assert replies[2]["result"]["content"][0]["text"] == "42"
