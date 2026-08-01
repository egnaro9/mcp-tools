#!/usr/bin/env bash
# A real MCP session over stdio — no client, no key, no network.
#
# MCP is JSON-RPC 2.0 over newline-delimited JSON. This drives the server the
# way a client would: initialize, notifications/initialized, then two
# tools/call requests. Nothing is stubbed; the JSON below goes in on stdin and
# the server's own responses come back on stdout. jq only narrows those
# responses to the field worth reading.
#
# Deliberately calls calc and grade_answer only. model_drift and compare_runs
# reach raw.githubusercontent.com and eval-history, so including them would
# make this depend on the network.
#
#   ./demo/session.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

rpc() { printf '%s\n' "$1"; }

{
  rpc '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1"}}}'
  rpc '{"jsonrpc":"2.0","method":"notifications/initialized"}'

  # 1. The calculator is the agent's most dangerous surface: it takes a string
  #    and evaluates it. Hand it an actual RCE payload.
  rpc '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"calc","arguments":{"expression":"__import__(\"os\").system(\"rm -rf ~\")"}}}'

  # 2. The server is still up. Ask it to check a draft answer whose second
  #    sentence invents a statistic the source never makes.
  rpc '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"grade_answer","arguments":{"answer":"Solar generation grew rapidly over the past decade. Costs fell 89% between 2010 and 2019.","sources":["Solar generation grew rapidly over the past decade as capacity expanded worldwide."]}}}'
} | python3 -m mcptools 2>/dev/null | python3 -c '
import json, sys

LABELS = {
    2: "calc  <-  __import__(\"os\").system(\"rm -rf ~\")",
    3: "grade_answer  <-  a draft answer and its one source",
}
for line in sys.stdin:
    msg = json.loads(line)
    rid = msg.get("id")
    if rid == 1:
        info = msg["result"]["serverInfo"]
        print("connected: {} {}  (stdio, JSON-RPC 2.0)\n".format(info["name"], info["version"]))
        continue
    if rid not in LABELS:
        continue
    result = msg["result"]
    flag = "isError: true" if result.get("isError") else "isError: false"
    print(f"{LABELS[rid]}\n  {flag}")
    for line_out in result["content"][0]["text"].splitlines():
        print(f"  {line_out}" if line_out else "")
    print()
'
