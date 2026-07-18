# mcp-tools

[![ci](https://github.com/egnaro9/mcp-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/egnaro9/mcp-tools/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-2025--06--18-6e40c9)](https://modelcontextprotocol.io)
[![tests](https://img.shields.io/badge/tests-11-brightgreen)](tests)

**A Model Context Protocol server, implemented from the spec — no MCP SDK, no dependencies.**

[MCP](https://modelcontextprotocol.io) is how a language-model client (Claude Desktop, an agent) discovers and calls tools a server exposes. It's JSON-RPC 2.0; a local server speaks it over **stdio**. This repo implements that protocol directly — the whole surface a tool server needs is `initialize` → `notifications/initialized` → `tools/list` → `tools/call` — so the protocol is legible instead of hidden behind a library.

It exposes two tools, both **safe by construction**:

| Tool | What it does | Why it's safe |
| --- | --- | --- |
| `calc` | Evaluate an arithmetic expression | Parses to an AST and allow-lists arithmetic nodes only — no `eval`, so `__import__('os')` is *rejected, not executed*. The **[OWASP LLM06 (Excessive Agency)](https://genai.owasp.org/llmrisk/llm06-2025-excessive-agency/)** mitigation: a tool that can do arithmetic and nothing else. |
| `search` | BM25 keyword search over a bundled corpus | Read-only, no network, no filesystem. The ranking is Okapi BM25 — the same length-normalised, saturation-aware scoring that [matches the published SciFact baseline in rag-eval-lab](https://github.com/egnaro9/rag-eval-lab), reimplemented here so this server has **zero dependencies**. |

## Use it with Claude Desktop

Add this to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "mcp-tools": { "command": "python", "args": ["-m", "mcptools"] }
  }
}
```

Restart Claude Desktop and ask it to *"search your notes for how rate limiting allows bursts"* or *"use calc to work out 17 * 23 + 4"* — it discovers the tools and calls them. Point `search` at **your own** notes with `"env": {"MCPTOOLS_CORPUS": "/path/to/notes.json"}` (a `{ "id": "text", ... }` file).

## Run it directly

```bash
pip install -e .
python -m mcptools        # serves on stdio; type/paste JSON-RPC, one message per line
```

```bash
# the handshake, by hand:
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"calc","arguments":{"expression":"2 + 3 * 4"}}}
# → {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"14"}],"isError":false}}
```

## The part worth stealing: it's testable without a client

An MCP server you can only exercise with Claude Desktop open isn't really testable. Because the protocol is plain JSON-RPC, the dispatch is a pure function of a message — so the [suite](tests/test_server.py) drives the real handshake directly *and* launches the server in a subprocess and speaks MCP to it over stdio, asserting that three requests get three replies and the notification gets none. The guardrail is tested through the protocol too: code thrown at `calc` comes back as an MCP tool-error (`isError: true`), so the model sees the failure and the server stays up.

```bash
pip install -e ".[dev]" && pytest -q     # 11 tests, stdlib only
```

## Design notes

- **Notifications get no reply.** A JSON-RPC message with no `id` is a notification; `notifications/initialized` is handled by producing nothing, per the spec.
- **Two error channels, on purpose.** An unknown *method* or a missing argument is a JSON-RPC protocol error (`-32601` / `-32602`); a *tool* that fails returns a result with `isError: true`. The model should adapt to a failed tool call, not have the connection torn down under it.
- **Why from scratch.** The official SDK is excellent and the right choice for production. Implementing the protocol directly here is the point of the repo: ~150 lines makes the whole lifecycle visible, and it keeps the dependency count at zero.

---
MIT · by [Erik Hill](https://egnaro9.github.io)
