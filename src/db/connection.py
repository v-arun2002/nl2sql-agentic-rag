"""
Read-only SQLite connections.

Its own module so both the executor and the upload registry can use it without
importing each other (the executor resolves upload paths; the registry needs to
introspect an upload before registering it).

Generated SQL is model output, so it is treated as untrusted: every connection
the pipeline opens is read-only. The pipeline only ever needs SELECT, so this
costs nothing, and it means a hallucinated DROP or DELETE cannot damage either
a visitor's uploaded database or the bundled BIRD files the deployed demo
serves to everyone else.

Two independent mechanisms, because neither alone is quite enough:
  - `mode=ro` in the URI makes SQLite refuse writes at the file level, and also
    refuses to create the file if it is missing;
  - `PRAGMA query_only` blocks writes at the statement level, which additionally
    covers a database ATTACHed later in the same connection.
"""

import sqlite3
from pathlib import Path


def readonly_uri(path) -> str:
    """
    file: URI with mode=ro. Built via Path.as_uri() rather than string
    concatenation so Windows drive letters and any characters needing
    percent-encoding are handled correctly.
    """
    return Path(path).resolve().as_uri() + "?mode=ro"


def connect_readonly(path, timeout: float = 5) -> sqlite3.Connection:
    """
    Raises sqlite3.OperationalError if the file is missing or not a database --
    mode=ro will not create one.
    """
    conn = sqlite3.connect(readonly_uri(path), uri=True, timeout=timeout)
    try:
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error:
        # Very old SQLite builds lack query_only; mode=ro still holds.
        pass
    return conn


def list_tables(conn: sqlite3.Connection) -> list:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return [row[0] for row in cursor.fetchall()]
