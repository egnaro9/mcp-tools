"""Re-record tests/fixtures/drift_board.json from the live model-drift board.

    python scripts/record_drift_board.py

It GETs `mcptools.live.BOARD_URL` and refuses to write anything unless that returns
HTTP 200 with a board-shaped body, so a fixture can only ever be recorded from a URL
that actually serves the board. The full board is about 1 MB; the fixture keeps every
top-level key and every series verbatim but only each series' last three points.
tests/fixtures/drift_board.source.json records where and when it came from, and
tests/test_live.py holds BOARD_URL to that recorded URL.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import urllib.request
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcptools.live import BOARD_URL, USER_AGENT

KEEP_POINTS = 3


def main() -> int:
    req = urllib.request.Request(BOARD_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:  # raises on any non-2xx
        status, body = r.status, r.read()
    board = json.loads(body)
    series = board.get("series")
    if status != 200 or not isinstance(series, dict) or not series:
        print(f"refusing to record: HTTP {status}, series={type(series).__name__}")
        return 1
    trimmed = {**board, "series": {k: v[-KEEP_POINTS:] for k, v in series.items()}}
    out = ROOT / "tests" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    (out / "drift_board.json").write_text(json.dumps(trimmed, indent=1) + "\n")
    (out / "drift_board.source.json").write_text(json.dumps({
        "url": BOARD_URL,
        "http_status": status,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "full_body_bytes": len(body),
        "full_body_sha256": hashlib.sha256(body).hexdigest(),
        "trim": f"every top-level key verbatim; each series cut to its last {KEEP_POINTS} points",
    }, indent=1) + "\n")
    print(f"recorded {len(series)} series from {BOARD_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
