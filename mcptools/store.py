"""Optional SQLite history for tool results — stdlib ``sqlite3``, no new dependency.

mcp-tools is stdlib-only and stays that way. Point ``MCPTOOLS_DB`` at a file and
the server records what ``grade_answer`` and ``model_drift`` actually returned —
one row per call — so a run leaves a queryable trail instead of scrolling past in
a log. Unset, nothing is written and the server behaves exactly as before, which
keeps the tests that don't opt in hermetic.

Schema changes are handled by a tiny hand-rolled migration runner keyed on
SQLite's ``PRAGMA user_version``: an ordered list of DDL steps, each bringing the
database up one version, run only from wherever it currently sits. That's the
zero-dependency form of what Alembic does with revision files — appropriate here
because pulling Alembic in would break the "no dependencies" guarantee that is
half the point of this project.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Optional

# Each entry brings the schema up by one version. Index i (0-based) migrates from
# user_version i to i+1, so a fresh database runs them all and an existing one
# runs only what it is missing. Append new steps; never edit a shipped one.
_MIGRATIONS: list[str] = [
    # v1 — one row per recorded tool call.
    """
    CREATE TABLE tool_events (
        id          INTEGER PRIMARY KEY,
        tool        TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        summary     TEXT NOT NULL,
        detail      TEXT
    );
    CREATE INDEX ix_tool_events_tool ON tool_events(tool, id);
    """,
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResultStore:
    """A thin table of tool-call results. Construct it with a path (a filename or
    ``:memory:``); the schema is created/upgraded on construction."""

    def __init__(self, path: str) -> None:
        self._path = path
        with closing(self._connect()) as c:
            self._migrate(c)

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path)
        c.row_factory = sqlite3.Row
        return c

    def _migrate(self, c: sqlite3.Connection) -> None:
        version = c.execute("PRAGMA user_version").fetchone()[0]
        for i in range(version, len(_MIGRATIONS)):
            c.executescript(_MIGRATIONS[i])
            c.execute(f"PRAGMA user_version = {i + 1}")   # PRAGMA can't be bound; i+1 is an int
        c.commit()

    def schema_version(self) -> int:
        with closing(self._connect()) as c:
            return int(c.execute("PRAGMA user_version").fetchone()[0])

    def record(self, tool: str, summary: str, detail: Optional[dict] = None) -> None:
        with closing(self._connect()) as c:
            c.execute(
                "INSERT INTO tool_events (tool, created_at, summary, detail) VALUES (?, ?, ?, ?)",
                (tool, _utcnow(), summary, json.dumps(detail) if detail is not None else None),
            )
            c.commit()

    def recent(self, tool: Optional[str] = None, limit: int = 20) -> list[dict]:
        """Most recent first, optionally filtered to one tool."""
        query = "SELECT tool, created_at, summary, detail FROM tool_events"
        params: tuple = ()
        if tool:
            query += " WHERE tool = ?"
            params = (tool,)
        query += " ORDER BY id DESC LIMIT ?"
        params += (limit,)
        with closing(self._connect()) as c:
            return [dict(r) for r in c.execute(query, params).fetchall()]
