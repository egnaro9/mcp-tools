"""The optional SQLite result store: migration versioning, record, and query."""
from mcptools.store import _MIGRATIONS, ResultStore


def test_fresh_db_runs_all_migrations(tmp_path):
    store = ResultStore(str(tmp_path / "h.db"))
    assert store.schema_version() == len(_MIGRATIONS)


def test_opening_an_up_to_date_db_is_a_noop(tmp_path):
    path = str(tmp_path / "h.db")
    ResultStore(path)
    again = ResultStore(path)                 # re-running migrations must not error
    assert again.schema_version() == len(_MIGRATIONS)


def test_record_and_recent_query(tmp_path):
    store = ResultStore(str(tmp_path / "h.db"))
    store.record("grade_answer", "1 unsupported claim", detail={"n": 1})
    store.record("model_drift", "gpt-4o steady", detail={"status": "ok"})
    store.record("grade_answer", "fully grounded", detail={"n": 0})

    rows = store.recent()
    assert len(rows) == 3
    assert rows[0]["summary"] == "fully grounded"           # most recent first
    graded = store.recent(tool="grade_answer")
    assert len(graded) == 2 and all(r["tool"] == "grade_answer" for r in graded)


def test_recent_respects_limit(tmp_path):
    store = ResultStore(str(tmp_path / "h.db"))
    for i in range(5):
        store.record("model_drift", f"snap {i}")
    assert len(store.recent(limit=2)) == 2
