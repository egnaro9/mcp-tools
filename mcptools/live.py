"""Two tools that answer questions about live systems, not bundled data.

`model_drift` reads the public board at
[model-drift](https://github.com/egnaro9/model-drift), a frozen suite run on a
schedule against every model it tracks, so an agent can ask "is the model I'm
about to rely on still scoring what it used to?" Series the board has stopped
updating are reported as such, with the date of their last run, never as
current numbers. `compare_runs` asks
[eval-history](https://github.com/egnaro9/eval-history) whether a project's most
recent eval run regressed against the one before it.

Both are **read-only GETs against public endpoints**: no keys, no writes,
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
from collections.abc import Callable
from datetime import datetime

from .tools import ToolError

# model-drift renamed dashboard/metrics.json to dashboard/drift_board.json on
# 2026-08-30 (model-drift 9ee59e3), and the old path has returned HTTP 404 since.
# tests/fixtures/drift_board.source.json records the URL the fixture was fetched
# from; a test holds this constant to it, and the opt-in live test GETs it.
BOARD_URL = "https://raw.githubusercontent.com/egnaro9/model-drift/main/dashboard/drift_board.json"
# A series whose newest run is this many days older than the board's newest run
# is no longer being updated (the two Meta series stopped on 2026-08-16). Its
# numbers are reported as its last run, labelled, never as current standings.
STALE_AFTER_DAYS = 14
# eval-history's hosted instance was retired; its read routes are exported as
# static files that mirror the old API paths with a `.json` suffix.
EVAL_HISTORY = "https://erikhill.dev/eval-history"
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


def _when(point: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(point.get("t", "")))
    except ValueError:
        return None


def summarize_drift(data: dict, query: str = "") -> str:
    """Format the board's latest numbers for whichever models match `query`.

    With no query, only series the board is still updating are listed; the ones it
    stopped are named in a closing line with their last-run date. Asked for by
    name, a stopped series is shown with its final numbers and labelled as such.
    """
    series: dict[str, list[dict]] = data.get("series", {})
    real = {k: v for k, v in series.items() if not k.startswith("mock:") and v}
    if not real:
        return "The board has no recorded runs yet."

    last = {k: _when(v[-1]) for k, v in real.items()}
    dated = [t for t in last.values() if t is not None]
    newest = max(dated) if dated else None

    def lag_days(key: str) -> int | None:
        t = last[key]
        return None if newest is None or t is None else (newest - t).days

    stopped = sorted(k for k in real if (lag_days(k) or 0) > STALE_AFTER_DAYS)
    current = sorted(k for k in real if k not in stopped)

    def stopped_note(keys: list[str]) -> str:
        return "; ".join(f"{k} (last run {last[k]:%Y-%m-%d})" for k in keys)

    q = query.strip().lower().replace(" ", "-")
    matches = sorted(k for k in real if q in k.lower()) if q else current
    if not matches:
        msg = f"No tracked model matches {query!r}. Tracked: " + ", ".join(current) + "."
        if stopped:
            msg += " No longer updated: " + stopped_note(stopped) + "."
        return msg

    # The board's own `updated` is stamped on every write, including mock-only CI
    # runs, so the newest real measurement is the honest date when there is one.
    when = f"{newest:%Y-%m-%d %H:%M} UTC" if newest else data.get("updated", "unknown")
    floor = data.get("rel_floor")

    def trusted(p: dict) -> bool:
        rel = p.get("reliability")
        return floor is None or rel is None or rel >= floor

    lines = [f"Live board, newest run {when}"]
    for key in matches:
        points = real[key]
        latest, prev = points[-1], (points[-2] if len(points) > 1 else None)
        lines.append(f"\n{key}  ({len(points)} run(s))")
        if key in stopped:
            lines.append(f"  NOT CURRENT: the board stopped updating this series. Last run "
                         f"{last[key]:%Y-%m-%d}, {lag_days(key)} days before the newest run; "
                         f"the numbers below are from that last run.")
        for field, label, kind in _METRIC_LABELS:
            now = latest.get(field)
            cell = f"  {label:16} {_fmt(now, kind):>10}"
            if field == "acc" and now is not None and not trusted(latest):
                # Failed calls score as wrong, so accuracy under the reliability floor
                # reads as a regression that is really a rate limit or an outage.
                cell += f"   (not trusted: reliability below the board's {floor:.0%} floor)"
                lines.append(cell)
                continue
            if prev is not None and now is not None and prev.get(field) is not None \
                    and (field != "acc" or trusted(prev)):
                d = now - prev[field]
                if abs(d) > (1e-9 if kind == "pct" else 0.5):
                    arrow = "▲" if d > 0 else "▼"
                    cell += f"   {arrow} {_fmt(abs(d), kind)} vs previous run"
            lines.append(cell)
    if len(matches) == 1 and len(real[matches[0]]) < 2:
        lines.append("\n(Only one run so far, so there is no trend to compare against yet.)")
    if not q and stopped:
        lines.append("\nNo longer updated, so not listed above (ask for one by name to see "
                     "its last numbers): " + stopped_note(stopped) + ".")
    return "\n".join(lines)


def model_drift(model: str = "", fetch: Callable[[str], dict] | None = None) -> str:
    # resolved at call time, not bound as a default; otherwise the real fetcher is
    # captured at import and no test could substitute it (CI would hit the network)
    return summarize_drift((fetch or _fetch)(BOARD_URL), model)


# ── compare_runs ───────────────────────────────────────────────────────
def summarize_comparison(cmp: dict, suite: str) -> str:
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


def compare_runs(suite: str, fetch: Callable[[str], dict] | None = None) -> str:
    from urllib.parse import quote
    if not suite or not suite.strip():
        raise ToolError("give a suite name (the eval-history 'run' name, e.g. 'rag-eval-lab')")
    cmp = (fetch or _fetch)(f"{EVAL_HISTORY}/suites/{quote(suite.strip())}/latest-comparison.json")
    if not cmp or cmp.get("verdict") is None:
        return f"{suite}: not enough runs stored yet to compare (needs two)."
    return summarize_comparison(cmp, suite.strip())
