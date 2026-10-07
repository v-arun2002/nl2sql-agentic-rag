"""
Read-only safety gate for SQL arriving from OUTSIDE the agent pipeline.

The pipeline's own executor (src/db/executor.py) runs SQL the generator just
wrote. The MCP tools accept SQL from any caller -- another agent, a person, a
prompt-injected document -- so the bar is higher, and it is enforced here, at
the tool boundary, on every call. execute() re-runs the gate itself rather
than trusting that validate() was called first: the tools are separately
callable, so "validated SQL" is a claim by the caller, not a fact.

THREE layers, outermost first:

  1. COMPILE-TIME AUTHORIZER (this module). SQLite consults an authorizer
     callback for every action while it compiles a statement. Only READ,
     SELECT, FUNCTION and RECURSIVE are allowed; INSERT, UPDATE, DELETE,
     CREATE, DROP, ALTER, ATTACH, PRAGMA, TRANSACTION and the rest are
     refused before a single row is touched. This uses SQLite's own parser,
     so there is no regex to fool -- a write hidden in a CTE, a subquery or
     odd whitespace is still a write to the compiler.
  2. PRAGMA query_only  (src/db/connection.py) -- statement-level.
  3. mode=ro URI        (src/db/connection.py) -- file-level.

Layers 2 and 3 are the existing runtime enforcement the pipeline already
uses. Layer 1 is what makes refusal *explainable*: a caller is told "denied:
DELETE on table molecule" up front, instead of discovering it as a runtime
"attempt to write a readonly database".

WHY A SEPARATE MODULE rather than adding the authorizer to connect_readonly():
the benchmark's 44.20% was measured through connect_readonly() as it stands.
Tightening that function would change the code path the number describes.
Keeping the gate here means the pipeline is untouched and the gate is purely
additive, at the new boundary.

Also enforced at execution: a row ceiling (results go into an LLM's context)
and a wall-clock timeout (a cartesian join on a 50MB table should not hang a
tool call). Neither exists in the pipeline executor, which fetchall()s.
"""

import os
import sqlite3
import time

from src.db.connection import connect_readonly

DEFAULT_MAX_ROWS = int(os.getenv("SQL_DEFAULT_MAX_ROWS", "200"))
MAX_ROWS_CEILING = int(os.getenv("SQL_MAX_ROWS_CEILING", "5000"))
TIMEOUT_SECONDS = float(os.getenv("SQL_TIMEOUT_SECONDS", "10"))

# The only authorizer actions a read needs. Values are SQLite's action codes;
# spelled out rather than taken from sqlite3.* because the module does not
# export every one on every Python build.
_ALLOWED_ACTIONS = {
    20: "READ",
    21: "SELECT",
    31: "FUNCTION",
    33: "RECURSIVE",  # WITH RECURSIVE
}

# Names for the refusal message. Not exhaustive by design -- anything not
# allowed is denied whether or not it has a friendly name here.
_ACTION_NAMES = {
    1: "CREATE INDEX", 2: "CREATE TABLE", 3: "CREATE TEMP INDEX",
    4: "CREATE TEMP TABLE", 5: "CREATE TEMP TRIGGER", 6: "CREATE TEMP VIEW",
    7: "CREATE TRIGGER", 8: "CREATE VIEW", 9: "DELETE", 10: "DROP INDEX",
    11: "DROP TABLE", 12: "DROP TEMP INDEX", 13: "DROP TEMP TABLE",
    14: "DROP TEMP TRIGGER", 15: "DROP TEMP VIEW", 16: "DROP TRIGGER",
    17: "DROP VIEW", 18: "INSERT", 19: "PRAGMA", 22: "TRANSACTION",
    23: "UPDATE", 24: "ATTACH", 25: "DETACH", 26: "ALTER TABLE",
    27: "REINDEX", 28: "ANALYZE", 29: "CREATE VTABLE", 30: "DROP VTABLE",
    32: "SAVEPOINT",
}

# FUNCTION is allowed as a class, but some functions are not reads. Python
# disables extension loading by default, so load_extension() would fail at
# runtime anyway -- it is denied here so the guarantee does not rest on a
# default someone could flip.
_DENIED_FUNCTIONS = {"load_extension"}


class SQLRefused(Exception):
    """The gate refused the statement. `category` is machine-readable."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def _authorizer(denials: list):
    def authorize(action, arg1, arg2, db_name, trigger):
        if action == 31 and (arg2 or "").lower() in _DENIED_FUNCTIONS:
            denials.append(f"function {arg2}()")
            return sqlite3.SQLITE_DENY
        if action in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_OK
        name = _ACTION_NAMES.get(action, f"action {action}")
        target = arg1 or arg2
        denials.append(f"{name} on {target}" if target else name)
        return sqlite3.SQLITE_DENY

    return authorize


def _open_guarded(db_path: str):
    """Read-only connection (layers 2+3) with the authorizer installed (layer 1)."""
    conn = connect_readonly(db_path, timeout=5)
    denials: list = []
    conn.set_authorizer(_authorizer(denials))
    return conn, denials


def _statement_type(sql: str) -> str:
    # Skip leading line comments so "-- note\nSELECT" reports SELECT.
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return stripped.split(None, 1)[0].upper()
    return ""


def validate(db_path: str, sql: str) -> dict:
    """
    Compile `sql` against the target database WITHOUT running it.

    Compiling (via EXPLAIN, which prepares the statement and returns its
    bytecode instead of executing it) runs the authorizer over every action
    the statement would take, and checks that every table and column it names
    actually exists in this database. So "valid" here means: a single,
    read-only statement that compiles against this schema.

    Raises SQLRefused on any failure; returns a description on success.
    """
    sql = (sql or "").strip()
    if not sql:
        raise SQLRefused("empty", "No SQL provided.")

    conn, denials = _open_guarded(db_path)
    try:
        conn.execute("EXPLAIN " + sql).fetchall()
    except sqlite3.ProgrammingError as e:
        if "one statement" in str(e):
            raise SQLRefused(
                "multiple_statements",
                "Only a single statement is accepted. Stacked statements "
                "(SELECT ...; DELETE ...) are refused outright.",
            ) from e
        raise SQLRefused("invalid", str(e)) from e
    except sqlite3.DatabaseError as e:
        if denials:
            raise SQLRefused(
                "not_read_only",
                "Refused: this statement is not read-only. Denied at compile "
                f"time: {', '.join(denials)}. Only SELECT queries are permitted.",
            ) from e
        # Syntax errors and unknown tables/columns land here.
        raise SQLRefused("does_not_compile", str(e)) from e
    finally:
        conn.close()

    return {"statement_type": _statement_type(sql)}


def _jsonable(value):
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    return value


def execute(db_path: str, sql: str, max_rows: int | None = None) -> dict:
    """
    Run `sql` read-only and return at most `max_rows` rows.

    Re-validates first -- see the module docstring for why that is not
    redundant. The authorizer is ALSO live on the executing connection, so
    even if validation were skipped, the statement that actually runs is
    compiled under the same gate.
    """
    meta = validate(db_path, sql)

    limit = DEFAULT_MAX_ROWS if max_rows is None else max_rows
    if limit < 1:
        raise SQLRefused("bad_argument", "max_rows must be at least 1.")
    limit = min(limit, MAX_ROWS_CEILING)

    conn, denials = _open_guarded(db_path)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    # Called every N SQLite VM steps; a non-zero return aborts the statement.
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)

    started = time.monotonic()
    try:
        cur = conn.execute(sql.strip())
        # One past the limit tells us whether we truncated, without paying
        # to count the full result.
        fetched = cur.fetchmany(limit + 1)
        columns = [d[0] for d in (cur.description or [])]
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise SQLRefused(
                "timeout",
                f"Query exceeded the {TIMEOUT_SECONDS:g}s execution limit and was aborted.",
            ) from e
        raise SQLRefused("execution_error", str(e)) from e
    except sqlite3.DatabaseError as e:
        if denials:
            raise SQLRefused("not_read_only", f"Refused at execution: {', '.join(denials)}") from e
        raise SQLRefused("execution_error", str(e)) from e
    finally:
        conn.close()

    truncated = len(fetched) > limit
    rows = [[_jsonable(v) for v in row] for row in fetched[:limit]]
    return {
        **meta,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows_applied": limit,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


def enforcement_facts() -> dict:
    """What the gate enforces, for the capability manifest. Read from the same
    constants the gate uses, so the manifest cannot describe a limit that is
    not actually applied."""
    return {
        "read_only_layers": [
            "compile-time SQLite authorizer: only READ, SELECT, FUNCTION, RECURSIVE allowed",
            "PRAGMA query_only = ON (statement-level)",
            "mode=ro database URI (file-level)",
        ],
        "allowed_authorizer_actions": sorted(_ALLOWED_ACTIONS.values()),
        "denied_functions": sorted(_DENIED_FUNCTIONS),
        "single_statement_only": True,
        "default_max_rows": DEFAULT_MAX_ROWS,
        "max_rows_ceiling": MAX_ROWS_CEILING,
        "timeout_seconds": TIMEOUT_SECONDS,
    }
