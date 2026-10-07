"""
The read-only gate behind the MCP validate/execute tools (src/db/sql_guard.py).

Free and offline: builds a throwaway SQLite file, so it needs neither the BIRD
data nor any API key. The write cases each assert TWO things -- that the gate
refused, and that the database is unchanged afterwards -- because a gate that
reports "refused" while the write went through anyway would pass a test that
only checked the first.
"""

import sqlite3

import pytest

from src.db import sql_guard
from src.db.sql_guard import SQLRefused


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE molecule (molecule_id TEXT PRIMARY KEY, label TEXT);
        INSERT INTO molecule VALUES ('m1', '+'), ('m2', '-'), ('m3', '+');
        CREATE TABLE big (n INTEGER);
        """
    )
    conn.executemany("INSERT INTO big VALUES (?)", [(i,) for i in range(500)])
    conn.commit()
    conn.close()
    return str(path)


def _row_count(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM molecule").fetchone()[0]
    finally:
        conn.close()


# --- reads pass --------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM molecule WHERE label = '+'",
        "SELECT COUNT(*) FROM molecule;",                       # trailing semicolon
        "-- leading comment\nSELECT * FROM molecule",
        "WITH plus AS (SELECT * FROM molecule WHERE label='+') SELECT COUNT(*) FROM plus",
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<5) SELECT * FROM n",
        "SELECT upper(label), strftime('%Y','now') FROM molecule",
        "SELECT 1; -- trailing comment is not a second statement",
    ],
)
def test_reads_validate(db, sql):
    assert sql_guard.validate(db, sql)


def test_execute_returns_rows_and_columns(db):
    out = sql_guard.execute(db, "SELECT molecule_id, label FROM molecule ORDER BY molecule_id")
    assert out["columns"] == ["molecule_id", "label"]
    assert out["rows"] == [["m1", "+"], ["m2", "-"], ["m3", "+"]]
    assert out["truncated"] is False


# --- writes are refused, and nothing changes -----------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM molecule",
        "DROP TABLE molecule",
        "INSERT INTO molecule VALUES ('x', '?')",
        "UPDATE molecule SET label = '?'",
        "CREATE TABLE evil (x)",
        "ALTER TABLE molecule ADD COLUMN x",
        "ATTACH DATABASE ':memory:' AS m",
        "PRAGMA writable_schema = 1",
        "BEGIN",
        # A write wrapped in a CTE is still a write to the compiler.
        "WITH x AS (SELECT 1) DELETE FROM molecule",
    ],
)
@pytest.mark.parametrize("entry", ["validate", "execute"])
def test_writes_refused_and_db_unchanged(db, sql, entry):
    before = _row_count(db)
    with pytest.raises(SQLRefused) as exc:
        getattr(sql_guard, entry)(db, sql)
    assert exc.value.category == "not_read_only"
    assert "Denied at compile time" in str(exc.value) or "Refused" in str(exc.value)
    assert _row_count(db) == before


@pytest.mark.parametrize("entry", ["validate", "execute"])
def test_stacked_statements_refused(db, entry):
    before = _row_count(db)
    with pytest.raises(SQLRefused) as exc:
        getattr(sql_guard, entry)(db, "SELECT 1; DELETE FROM molecule")
    assert exc.value.category == "multiple_statements"
    assert _row_count(db) == before


def test_load_extension_refused(db):
    # Passes SQLite's authorizer as an ordinary FUNCTION -- denied by name.
    with pytest.raises(SQLRefused) as exc:
        sql_guard.validate(db, "SELECT load_extension('anything')")
    assert exc.value.category == "not_read_only"
    assert "load_extension" in str(exc.value)


def test_refusal_names_the_denied_action(db):
    with pytest.raises(SQLRefused) as exc:
        sql_guard.validate(db, "DELETE FROM molecule")
    assert "DELETE" in str(exc.value)


# --- non-compiling SQL ------------------------------------------------------------

@pytest.mark.parametrize(
    "sql,category",
    [
        ("SELEC 1", "does_not_compile"),
        ("SELECT * FROM no_such_table", "does_not_compile"),
        ("SELECT no_such_column FROM molecule", "does_not_compile"),
        ("", "empty"),
        ("   ", "empty"),
    ],
)
def test_non_compiling_sql(db, sql, category):
    with pytest.raises(SQLRefused) as exc:
        sql_guard.validate(db, sql)
    assert exc.value.category == category


# --- execution bounds ---------------------------------------------------------------

def test_row_ceiling_truncates(db):
    out = sql_guard.execute(db, "SELECT n FROM big ORDER BY n", max_rows=10)
    assert out["row_count"] == 10
    assert out["truncated"] is True
    assert out["rows"][0] == [0]


def test_exact_fit_is_not_truncated(db):
    out = sql_guard.execute(db, "SELECT * FROM molecule", max_rows=3)
    assert out["row_count"] == 3
    assert out["truncated"] is False


def test_max_rows_clamped_to_ceiling(db, monkeypatch):
    monkeypatch.setattr(sql_guard, "MAX_ROWS_CEILING", 25)
    out = sql_guard.execute(db, "SELECT n FROM big", max_rows=10_000)
    assert out["max_rows_applied"] == 25
    assert out["truncated"] is True


def test_max_rows_below_one_refused(db):
    with pytest.raises(SQLRefused) as exc:
        sql_guard.execute(db, "SELECT 1", max_rows=0)
    assert exc.value.category == "bad_argument"


def test_timeout_aborts_runaway_query(db, monkeypatch):
    monkeypatch.setattr(sql_guard, "TIMEOUT_SECONDS", 0.2)
    runaway = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n) "
        "SELECT COUNT(*) FROM n"
    )
    with pytest.raises(SQLRefused) as exc:
        sql_guard.execute(db, runaway)
    assert exc.value.category == "timeout"


def test_blobs_are_json_safe(tmp_path):
    path = tmp_path / "b.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE b (x BLOB)")
    conn.execute("INSERT INTO b VALUES (?)", (b"\x00\x01\x02",))
    conn.commit()
    conn.close()
    out = sql_guard.execute(str(path), "SELECT x FROM b")
    assert out["rows"] == [["<blob 3 bytes>"]]


def test_enforcement_facts_match_constants():
    facts = sql_guard.enforcement_facts()
    assert facts["max_rows_ceiling"] == sql_guard.MAX_ROWS_CEILING
    assert facts["timeout_seconds"] == sql_guard.TIMEOUT_SECONDS
    assert set(facts["allowed_authorizer_actions"]) == {"READ", "SELECT", "FUNCTION", "RECURSIVE"}
