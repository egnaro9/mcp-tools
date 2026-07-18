"""Two tools that answer questions about live systems, not bundled data.

`model_drift` reads the public board at
[model-drift](https://github.com/egnaro9/model-drift) — a frozen suite run
weekly against 16 models — so an agent can ask "is the model I'm about to rely
on still scoring what it used to?" `compare_runs` asks
[eval-history](https://github.com/egnaro9/eval-history) whether a project's most
recent eval run regressed against the one before it.

Both are **read-only GETs against public endpoints** — no keys, no writes,
nothing to leak. The network is the one thing here that can fail, so a fetch
problem comes back as an MCP tool error the model can read and route around,
never an exception that kills the server.

The parsing and wording live in pure functions (`summarize_drift`,
`summarize_comparison`) so the tests can pin the output shape without depending
on a network round-trip in CI.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional

from .tools import ToolError

METRICS_URL = "https://raw.githubusercontent.com/egnaro9/model-drift/main/dashboard/metrics.json"
EVAL_HISTORY = "https://eval-history.onrender.com"
# Some hosts (Cloudflare in front of an API) reject urllib's default agent outright.
USER_AGENT = "mcp-tools/1.0 (+https://github.com/egnaro9/mcp-tools)"
TIMEOUT = 30


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise ToolError(f"{url} returned HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ToolError(f"could not reach {url} ({getattr(e, 'reason', e)})")
    except json.JSONDecodeError:
        raise ToolError(f"{url} did not return JSON")


# ── model_drift ────────────────────────────────────────────────────────
_METRIC_LABELS = [("acc", "accuracy", "pct"), ("latency_ms", "median latency", "ms"),
                  ("out_chars", "answer length", "chars"), ("reliability", "reliability", "pct"),
                  ("refusal_rate", "refusal rate", "pct")]


def _fmt(value, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "ms":
        return f"{value / 1000:.2f} s" if value >= 1000 else f"{value:.0f} ms"
    return f"{value:.0f} chars"


def summarize_drift(data: Dict, query: str = "") -> str:
    """Format the board's latest numbers for whichever models match `query`."""
    series: Dict[str, List[dict]] = data.get("series", {})
    real = {k: v for k, v in series.items() if not k.startswith("mock:") and v}
    if not real:
        return "The board has no recorded runs yet."

    q = query.strip().lower().replace(" ", "-")
    matches = [k for k in real if q in k.lower()] if q else list(real)
    if not matches:
        return (f"No tracked model matches {query!r}. Tracked: "
                + ", ".join(sorted(real)) + ".")

    lines = [f"Live board — last updated {data.get('updated', 'unknown')}"]
    for key in sorted(matches):
        points = real[key]
        latest, prev = points[-1], (points[-2] if len(points) > 1 else None)
        lines.append(f"\n{key}  ({len(points)} run(s))")
        for field, label, kind in _METRIC_LABELS:
            now = latest.get(field)
            cell = f"  {label:16} {_fmt(now, kind):>10}"
            if prev is not None and now is not None and prev.get(field) is not None:
                d = now - prev[field]
                if abs(d) > (1e-9 if kind == "pct" else 0.5):
                    arrow = "▲" if d > 0 else "▼"
                    cell += f"   {arrow} {_fmt(abs(d), kind)} vs previous run"
            lines.append(cell)
    if len(matches) == 1 and len(real[matches[0]]) < 2:
        lines.append("\n(Only one run so far — no trend to compare against yet.)")
    return "\n".join(lines)


def model_drift(model: str = "", fetch: Optional[Callable[[str], dict]] = None) -> str:
    # resolved at call time, not bound as a default — otherwise the real fetcher is
    # captured at import and no test could substitute it (CI would hit the network)
    return summarize_drift((fetch or _fetch)(METRICS_URL), model)


# ── compare_runs ───────────────────────────────────────────────────────
def summarize_comparison(cmp: Dict, suite: str) -> str:
    verdict = cmp.get("verdict", "unknown")
    regs, imps = cmp.get("regressions") or [], cmp.get("improvements") or []
    flagged = cmp.get("newly_flagged") or []
    lines = [f"{suite}: **{verdict}** — {len(regs)} regression(s), {len(imps)} improvement(s)"]
    if flagged:
        lines.append(f"Newly flagged case(s): {', '.join(flagged[:5])}")
    for d in regs[:8]:
        lines.append(f"  ▼ {d.get('q', '?')[:70]} · {d.get('metric', '?')}: "
                     f"{d.get('before')} → {d.get('after')}")
    for d in imps[:5]:
        lines.append(f"  ▲ {d.get('q', '?')[:70]} · {d.get('metric', '?')}: "
                     f"{d.get('before')} → {d.get('after')}")
    if not regs and not imps:
        lines.append("Nothing moved between the two most recent runs.")
    return "\n".join(lines)


def compare_runs(suite: str, fetch: Optional[Callable[[str], dict]] = None) -> str:
    from urllib.parse import quote
    if not suite or not suite.strip():
        raise ToolError("give a suite name (the eval-history 'run' name, e.g. 'rag-eval-lab')")
    cmp = (fetch or _fetch)(f"{EVAL_HISTORY}/suites/{quote(suite.strip())}/latest-comparison")
    if not cmp or cmp.get("verdict") is None:
        return f"{suite}: not enough runs stored yet to compare (needs two)."
    return summarize_comparison(cmp, suite.strip())
