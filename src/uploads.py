"""
Session-scoped SQLite uploads for the public demo.

Lets a visitor query their own database instead of only the bundled BIRD ones.
Everything here is deliberately ephemeral: files live in the OS temp directory
and vanish when the container restarts.

Why a file-backed registry rather than an in-process dict: the UI and the
executor are the same process on a single-process host (UI_DIRECT_MODE=true,
how the demo is deployed), but they are *different* processes when the UI talks
to the FastAPI backend. A small JSON file in the temp directory means path
resolution works in both, as long as the two share a filesystem. They do not in
docker-compose, where api/ and ui/ are separate containers -- so the UI warns
rather than silently offering a broken feature there.

Concurrency on the registry file is last-write-wins, like src/demo_limits.py.
Two visitors uploading in the same instant could lose one entry; the cost is a
re-upload, and the alternative is real locking for a portfolio demo.

Two cleanup mechanisms, because they fail in opposite ways:
  - an explicit Remove button, which is immediate but which most people will
    never click;
  - eviction on every registry read -- a TTL plus a hard cap on concurrent
    uploads, oldest evicted first. Without the automatic side, uploads
    accumulate until the ~1GB host runs out of memory.
"""

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from src.db.connection import connect_readonly, list_tables

MAX_UPLOAD_BYTES = int(os.getenv("UPLOAD_MAX_BYTES", str(50 * 1024 * 1024)))  # 50MB
MAX_CONCURRENT = int(os.getenv("UPLOAD_MAX_CONCURRENT", "3"))
TTL_SECONDS = int(os.getenv("UPLOAD_TTL_SECONDS", str(60 * 60)))  # 1 hour

UPLOAD_ROOT = Path(os.getenv("UPLOAD_DIR") or (Path(tempfile.gettempdir()) / "nl2sql_uploads"))
REGISTRY_PATH = UPLOAD_ROOT / "registry.json"

# Uploads get their own Chroma store, deliberately not settings.chroma_persist_dir.
# The shipped index is a committed build artifact -- the deployed app cannot
# rebuild it, since the BIRD .sqlite files aren't all there -- while upload
# segments are per-session garbage. Sharing one directory meant every upload
# dirtied a tracked path, and reaping orphans had to reason about two lifecycles
# in one place. Defaults inside UPLOAD_ROOT so the two halves of an upload (the
# database and its embeddings) are created and destroyed together, and nothing
# ephemeral is ever written inside the repository.
CHROMA_DIR = Path(os.getenv("UPLOAD_CHROMA_DIR") or (UPLOAD_ROOT / "chroma"))

SQLITE_MAGIC = b"SQLite format 3\x00"
ALLOWED_SUFFIXES = {".sqlite", ".db", ".sqlite3", ".db3"}


class UploadError(Exception):
    """A rejection with a message intended for the user, not a stack trace."""


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def _load() -> dict:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(registry: dict) -> None:
    try:
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text(json.dumps(registry), encoding="utf-8")
    except Exception:
        pass  # a failed registry write must not break the query path


def _slug(name: str) -> str:
    stem = Path(name).stem
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()[:40].strip("_")
    return slug or "db"


def _make_db_id(session_id: str, filename: str) -> str:
    """
    Unique per upload, and a valid Chroma collection name once prefixed with
    'schema_' (alphanumerics, underscores, hyphens; must end alphanumeric).
    Uniqueness is what keeps concurrent visitors from colliding on a collection.
    """
    return f"upload_{session_id[:8]}_{uuid.uuid4().hex[:6]}_{_slug(filename)}"


def _entry_path(entry: dict) -> Path:
    return Path(entry["path"])


def store():
    """
    The Chroma store for uploads, which is never the shipped one.

    Imported lazily on purpose: constructing it loads the embedding model, and
    the executor imports this module on every query only to resolve a path.
    """
    from src.retrieval.vector_store import SchemaVectorStore

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return SchemaVectorStore(persist_dir=str(CHROMA_DIR))


# --------------------------------------------------------------------------
# eviction
# --------------------------------------------------------------------------

def _delete_entry(db_id: str, entry: dict) -> None:
    """Remove the file and the Chroma collection. Best-effort on both."""
    try:
        path = _entry_path(entry)
        if path.exists():
            path.unlink()
        parent = path.parent
        if parent != UPLOAD_ROOT and parent.is_dir():
            shutil.rmtree(parent, ignore_errors=True)
    except Exception:
        pass

    try:
        store().delete_schema(db_id)
    except Exception:
        pass


def evict(registry: Optional[dict] = None) -> list:
    """
    Drop expired entries, then oldest-first while over the concurrency cap.
    Returns the db_ids evicted. Called on every registry read, so it is the
    backstop that does not depend on anyone pressing Remove.
    """
    reg = _load() if registry is None else registry
    now = time.time()
    evicted = []

    for db_id, entry in sorted(reg.items(), key=lambda kv: kv[1].get("created_at", 0)):
        expired = TTL_SECONDS > 0 and now - entry.get("created_at", 0) > TTL_SECONDS
        missing = not _entry_path(entry).exists()
        if expired or missing:
            evicted.append(db_id)

    remaining = [k for k in reg if k not in evicted]
    if MAX_CONCURRENT > 0 and len(remaining) > MAX_CONCURRENT:
        oldest = sorted(remaining, key=lambda k: reg[k].get("created_at", 0))
        evicted.extend(oldest[: len(remaining) - MAX_CONCURRENT])

    for db_id in evicted:
        _delete_entry(db_id, reg.pop(db_id))

    if evicted:
        _save(reg)
    return evicted


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate_bytes(data: bytes, filename: str) -> None:
    """
    Cheap checks that need no disk: extension, size, magic header. Raises
    UploadError with a message worth showing a user.
    """
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        raise UploadError(
            "Expected a SQLite file (%s). Got '%s'."
            % (", ".join(sorted(ALLOWED_SUFFIXES)), Path(filename).name)
        )
    if not data:
        raise UploadError("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(
            "That file is %.1fMB. The demo caps uploads at %dMB, because the host "
            "has about 1GB of memory and a larger database would exhaust it. Run "
            "the project locally for bigger databases."
            % (len(data) / 1024 / 1024, MAX_UPLOAD_BYTES // 1024 // 1024)
        )
    if not data.startswith(SQLITE_MAGIC):
        raise UploadError(
            "That doesn't look like a SQLite database -- the file header is wrong. "
            "A .db from another engine (MySQL, Postgres dump) won't work; export "
            "to SQLite first."
        )


def _validate_file(path: Path) -> list:
    """
    Structural check, which needs the bytes on disk: does SQLite open it, and
    does it contain at least one table? A file can carry a valid header and
    still be truncated or schema-less.
    """
    try:
        conn = connect_readonly(path)
    except sqlite3.Error as e:
        raise UploadError("SQLite could not open that file: %s" % e)

    try:
        # Reads a page of every table's schema, so a truncated file fails here
        # rather than later mid-query.
        conn.execute("PRAGMA schema_version").fetchone()
        tables = list_tables(conn)
    except sqlite3.DatabaseError as e:
        raise UploadError(
            "That file is a SQLite database but appears corrupt: %s" % e
        )
    finally:
        conn.close()

    if not tables:
        raise UploadError("That database has no tables, so there's nothing to query.")
    return tables


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def register(data: bytes, filename: str, session_id: str, on_progress=None) -> dict:
    """
    Validate, store, and index an upload. Returns its registry entry.
    Raises UploadError for anything a user can act on.

    Indexing happens here rather than lazily at query time so a bad upload
    fails at the moment it is uploaded, while the user is still looking at it.
    """
    validate_bytes(data, filename)
    evict()

    db_id = _make_db_id(session_id, filename)
    target_dir = UPLOAD_ROOT / db_id
    target = target_dir / f"{db_id}.sqlite"

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as e:
        raise UploadError("Could not save that upload: %s" % e)

    try:
        tables = _validate_file(target)
    except UploadError:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    try:
        from data.build_schema_index import introspect_sqlite

        schema = introspect_sqlite(str(target))
        store().index_schema(db_id, schema, on_progress=on_progress)
    except Exception as e:
        # Never leave a half-indexed database registered: retrieval would
        # return a partial schema and the generator would invent the rest,
        # which is the silent-hallucination failure this project already has
        # a scar from.
        shutil.rmtree(target_dir, ignore_errors=True)
        try:
            store().delete_schema(db_id)
        except Exception:
            pass
        raise UploadError("Could not index that schema: %s" % e)

    entry = {
        "db_id": db_id,
        "path": str(target),
        "original_name": Path(filename).name,
        "session_id": session_id,
        "tables": tables,
        "bytes": len(data),
        "created_at": time.time(),
    }
    reg = _load()
    reg[db_id] = entry
    _save(reg)
    evict()
    return entry


def resolve_path(db_id: str) -> Optional[str]:
    """
    Absolute path for an uploaded db_id, or None if it isn't one. The executor
    calls this on every query, so it stays cheap: no embedding model, no Chroma.
    """
    entry = _load().get(db_id)
    if not entry:
        return None
    path = _entry_path(entry)
    return str(path) if path.exists() else None


def is_upload(db_id: str) -> bool:
    return bool(db_id) and db_id.startswith("upload_")


def list_for_session(session_id: str) -> list:
    """Entries belonging to one visitor, newest first, after eviction."""
    evict()
    entries = [e for e in _load().values() if e.get("session_id") == session_id]
    return sorted(entries, key=lambda e: e.get("created_at", 0), reverse=True)


def remove(db_id: str) -> bool:
    """Explicit cleanup: delete the file and its Chroma collection."""
    reg = _load()
    entry = reg.pop(db_id, None)
    if entry is None:
        return False
    _delete_entry(db_id, entry)
    _save(reg)
    return True
