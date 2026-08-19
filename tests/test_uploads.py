"""
Tests for demo SQLite uploads: validation, read-only enforcement, and cleanup.

No API keys and no network. Chroma indexing is real (the embedder is local), so
these are slower than test_units.py but still run on every push.

The read-only tests are the reason this file exists. A `mode=ro` flag that
silently didn't apply would mean generated SQL could DROP a visitor's uploaded
table, so these assert against the actual bytes on disk rather than trusting
that the connection was opened the way it was asked to be.
"""

import sqlite3

import pytest

from src import uploads
from src.db.connection import connect_readonly
from src.db.executor import executor_node, resolve_db_path
from src.retrieval.vector_store import SchemaVectorStore


def make_db_bytes(tmp_path, tables=("employees",), rows=3):
    """A small but real SQLite file, returned as bytes."""
    path = tmp_path / "source.sqlite"
    conn = sqlite3.connect(path)
    for t in tables:
        conn.execute(f"CREATE TABLE {t} (id INTEGER, name TEXT, salary INTEGER)")
        conn.executemany(
            f"INSERT INTO {t} VALUES (?, ?, ?)",
            [(i, f"person{i}", 100 + i) for i in range(rows)],
        )
    conn.commit()
    conn.close()
    data = path.read_bytes()
    path.unlink()
    return data


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """
    Point the registry and Chroma at throwaway directories, so tests never touch
    the committed chroma_db/ or a real upload directory.
    """
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(uploads, "UPLOAD_ROOT", root)
    monkeypatch.setattr(uploads, "REGISTRY_PATH", root / "registry.json")

    from src.config import settings
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path / "chroma"))
    return root


def _state(db_id, sql):
    return {"db_id": db_id, "sql_query": sql, "trace": []}


# --------------------------------------------------------------------------
# validation -- a bad upload must produce a message, not a traceback
# --------------------------------------------------------------------------

class TestValidation:
    def test_wrong_extension_is_rejected(self):
        with pytest.raises(uploads.UploadError, match="Expected a SQLite file"):
            uploads.validate_bytes(uploads.SQLITE_MAGIC + b"x", "data.csv")

    def test_empty_file_is_rejected(self):
        with pytest.raises(uploads.UploadError, match="empty"):
            uploads.validate_bytes(b"", "empty.sqlite")

    def test_non_sqlite_content_is_rejected_on_the_header(self):
        """A .db from another engine carries the right name and wrong bytes."""
        with pytest.raises(uploads.UploadError, match="doesn't look like a SQLite"):
            uploads.validate_bytes(b"-- MySQL dump 10.13\n" + b"0" * 100, "dump.db")

    def test_oversized_file_is_rejected_with_the_cap_in_the_message(self, monkeypatch):
        monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1024)
        with pytest.raises(uploads.UploadError, match="caps uploads at"):
            uploads.validate_bytes(uploads.SQLITE_MAGIC + b"0" * 2048, "big.sqlite")

    def test_size_is_checked_before_anything_touches_disk(self, isolated, monkeypatch):
        monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1024)
        with pytest.raises(uploads.UploadError):
            uploads.register(uploads.SQLITE_MAGIC + b"0" * 2048, "big.sqlite", "s1")
        assert list(isolated.glob("upload_*")) == []

    def test_a_database_with_no_tables_is_rejected(self, isolated, tmp_path):
        """
        Valid header, opens cleanly, nothing to query. Note the PRAGMA: a fresh
        sqlite3.connect() leaves a 0-byte file until something forces a write,
        which the empty-file check would catch first for the wrong reason.
        """
        empty = tmp_path / "empty.sqlite"
        conn = sqlite3.connect(empty)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        assert empty.read_bytes().startswith(uploads.SQLITE_MAGIC)

        with pytest.raises(uploads.UploadError, match="no tables"):
            uploads.register(empty.read_bytes(), "empty.sqlite", "s1")

    def test_truncated_database_is_rejected_and_leaves_nothing_behind(self, isolated, tmp_path):
        data = make_db_bytes(tmp_path)
        with pytest.raises(uploads.UploadError):
            uploads.register(data[: len(data) // 2], "truncated.sqlite", "s1")
        assert uploads._load() == {}
        assert list(isolated.glob("upload_*")) == []


# --------------------------------------------------------------------------
# register / resolve
# --------------------------------------------------------------------------

class TestRegister:
    def test_happy_path_indexes_and_resolves(self, isolated, tmp_path):
        entry = uploads.register(make_db_bytes(tmp_path), "My Report.sqlite", "sessionabcd")

        assert entry["tables"] == ["employees"]
        assert entry["original_name"] == "My Report.sqlite"
        assert uploads.resolve_path(entry["db_id"])
        assert uploads.is_upload(entry["db_id"])
        assert SchemaVectorStore().has_schema(entry["db_id"])

    def test_db_id_is_a_valid_chroma_collection_name(self, isolated, tmp_path):
        """Chroma requires [a-zA-Z0-9._-] and an alphanumeric last character;
        a filename with spaces and punctuation must not produce an invalid one."""
        entry = uploads.register(make_db_bytes(tmp_path), "wéird name (2).sqlite", "s1")
        name = f"schema_{entry['db_id']}"
        assert name[-1].isalnum()
        assert all(c.isalnum() or c in "._-" for c in name)

    def test_concurrent_sessions_get_distinct_ids_for_the_same_filename(self, isolated, tmp_path):
        data = make_db_bytes(tmp_path)
        a = uploads.register(data, "shared.sqlite", "sessionAAAA")
        b = uploads.register(data, "shared.sqlite", "sessionBBBB")

        assert a["db_id"] != b["db_id"]
        assert uploads.resolve_path(a["db_id"]) != uploads.resolve_path(b["db_id"])

    def test_sessions_only_see_their_own_uploads(self, isolated, tmp_path):
        data = make_db_bytes(tmp_path)
        a = uploads.register(data, "a.sqlite", "sessionAAAA")
        uploads.register(data, "b.sqlite", "sessionBBBB")

        mine = uploads.list_for_session("sessionAAAA")
        assert [e["db_id"] for e in mine] == [a["db_id"]]

    def test_progress_callback_reports_every_table(self, isolated, tmp_path):
        data = make_db_bytes(tmp_path, tables=("t1", "t2", "t3"))
        seen = []
        uploads.register(data, "multi.sqlite", "s1", on_progress=lambda d, t: seen.append((d, t)))

        assert seen, "progress callback was never invoked"
        assert seen[-1] == (3, 3)

    def test_resolve_path_is_none_for_a_bundled_db_id(self, isolated):
        assert uploads.resolve_path("superhero") is None
        assert uploads.is_upload("superhero") is False

    def test_executor_falls_back_to_the_bird_layout(self, isolated):
        """A non-upload db_id must still resolve to the BIRD path."""
        assert resolve_db_path("superhero").endswith("dev_databases/superhero/superhero.sqlite")


# --------------------------------------------------------------------------
# read-only enforcement -- asserted against bytes on disk
# --------------------------------------------------------------------------

class TestReadOnlyEnforcement:
    @pytest.mark.parametrize(
        "label,sql",
        [
            ("insert", "INSERT INTO employees VALUES (99, 'mallory', 1)"),
            ("update", "UPDATE employees SET salary = 0"),
            ("delete", "DELETE FROM employees"),
            ("drop", "DROP TABLE employees"),
            ("create", "CREATE TABLE evil (x INTEGER)"),
            ("alter", "ALTER TABLE employees ADD COLUMN pwned TEXT"),
        ],
    )
    def test_writes_are_rejected_and_the_file_is_untouched(self, isolated, tmp_path, label, sql):
        entry = uploads.register(make_db_bytes(tmp_path), "ro.sqlite", "s1")
        path = uploads.resolve_path(entry["db_id"])
        before = open(path, "rb").read()

        out = executor_node(_state(entry["db_id"], sql))

        assert out["success"] is False, f"{label} was NOT blocked"
        assert "readonly" in (out["execution_error"] or "").lower()
        assert open(path, "rb").read() == before, f"{label} modified the file"

    def test_select_still_works(self, isolated, tmp_path):
        entry = uploads.register(make_db_bytes(tmp_path, rows=3), "ro.sqlite", "s1")
        out = executor_node(_state(entry["db_id"], "SELECT COUNT(*) FROM employees"))

        assert out["success"] is True
        assert out["execution_result"] == [(3,)]

    def test_bundled_databases_are_also_read_only(self, tmp_path):
        """Not just uploads: a generated DROP against a bundled BIRD database
        would corrupt the deployed demo's shipped data until redeploy."""
        out = executor_node(_state("superhero", "CREATE TABLE evil (x INTEGER)"))
        assert out["success"] is False
        assert "readonly" in (out["execution_error"] or "").lower()

    def test_write_to_an_attached_database_is_blocked(self, isolated, tmp_path):
        """mode=ro only covers the main database. PRAGMA query_only is what
        stops an ATTACH from becoming a write escape."""
        entry = uploads.register(make_db_bytes(tmp_path), "ro.sqlite", "s1")
        side = tmp_path / "writable.sqlite"
        conn = sqlite3.connect(side)
        conn.execute("CREATE TABLE loot (x TEXT)")
        conn.commit()
        conn.close()
        before = side.read_bytes()

        ro = connect_readonly(uploads.resolve_path(entry["db_id"]))
        ro.execute(f"ATTACH DATABASE '{side.as_posix()}' AS w")  # a read; permitted
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro.execute("INSERT INTO w.loot VALUES ('pwned')")
        ro.close()

        assert side.read_bytes() == before

    def test_missing_file_does_not_get_created(self, tmp_path):
        """mode=ro must refuse to bring a database into existence."""
        ghost = tmp_path / "ghost.sqlite"
        with pytest.raises(sqlite3.OperationalError):
            connect_readonly(ghost)
        assert not ghost.exists()


# --------------------------------------------------------------------------
# cleanup -- explicit, and automatic as a backstop
# --------------------------------------------------------------------------

class TestExplicitRemoval:
    def test_remove_frees_the_file_and_the_collection(self, isolated, tmp_path):
        """Both, not one: freeing the file alone leaks a Chroma collection per
        upload for the lifetime of the container."""
        entry = uploads.register(make_db_bytes(tmp_path), "gone.sqlite", "s1")
        db_id = entry["db_id"]
        path = uploads.resolve_path(db_id)

        assert SchemaVectorStore().has_schema(db_id)

        assert uploads.remove(db_id) is True

        assert not __import__("os").path.exists(path)
        assert SchemaVectorStore().has_schema(db_id) is False
        assert uploads.resolve_path(db_id) is None
        assert db_id not in uploads._load()

    def test_remove_leaves_no_referenced_segment_behind(self, isolated, tmp_path):
        """
        Chroma's delete_collection drops the metadata rows but leaves the HNSW
        segment directory on disk. What removal must guarantee is that no segment
        stays *referenced* -- an unreferenced directory is reclaimable, and is
        reclaimed by reap_orphaned_segments (immediately on Linux, on the next
        call on Windows, where Chroma holds the files mmap'd until process exit).
        """
        entry = uploads.register(make_db_bytes(tmp_path), "seg.sqlite", "s1")
        meta = tmp_path / "chroma" / "chroma.sqlite3"
        segments = lambda: {
            r[0] for r in sqlite3.connect(meta).execute("SELECT id FROM segments")
        }
        assert segments(), "expected segments after indexing"

        uploads.remove(entry["db_id"])

        assert segments() == set(), "a segment is still referenced after removal"

    def test_reaper_deletes_an_unreferenced_segment_directory(self, isolated, tmp_path):
        """
        The reap logic itself, on a directory with no open handles -- which is
        the state every orphan is in after a restart, and the state all of them
        are in on Linux.
        """
        uploads.register(make_db_bytes(tmp_path), "live.sqlite", "s1")
        chroma_dir = tmp_path / "chroma"

        orphan = chroma_dir / "00000000-0000-4000-8000-000000000000"
        orphan.mkdir()
        (orphan / "data_level0.bin").write_bytes(b"stale")

        assert SchemaVectorStore().reap_orphaned_segments() == 1
        assert not orphan.exists()

    def test_reaper_ignores_non_uuid_directories(self, isolated, tmp_path):
        """Only segment directories are fair game; anything else is left alone."""
        uploads.register(make_db_bytes(tmp_path), "live.sqlite", "s1")
        keep = tmp_path / "chroma" / "not-a-segment"
        keep.mkdir()

        SchemaVectorStore().reap_orphaned_segments()
        assert keep.exists()

    def test_reaper_keeps_directories_that_are_still_referenced(self, isolated, tmp_path):
        """The reaper must not delete a live collection's segments."""
        entry = uploads.register(make_db_bytes(tmp_path), "live.sqlite", "s1")
        store = SchemaVectorStore()

        assert store.reap_orphaned_segments() == 0
        assert store.has_schema(entry["db_id"])
        assert store.retrieve_relevant_tables(entry["db_id"], "salary", top_k=2)

    def test_remove_is_idempotent(self, isolated, tmp_path):
        entry = uploads.register(make_db_bytes(tmp_path), "gone.sqlite", "s1")
        assert uploads.remove(entry["db_id"]) is True
        assert uploads.remove(entry["db_id"]) is False

    def test_removing_one_upload_leaves_another_intact(self, isolated, tmp_path):
        data = make_db_bytes(tmp_path)
        keep = uploads.register(data, "keep.sqlite", "s1")
        drop = uploads.register(data, "drop.sqlite", "s1")

        uploads.remove(drop["db_id"])

        assert uploads.resolve_path(keep["db_id"])
        assert SchemaVectorStore().has_schema(keep["db_id"])


class TestAutomaticEviction:
    def test_oldest_is_evicted_over_the_concurrency_cap(self, isolated, tmp_path, monkeypatch):
        monkeypatch.setattr(uploads, "MAX_CONCURRENT", 2)
        data = make_db_bytes(tmp_path)

        first = uploads.register(data, "first.sqlite", "s1")
        second = uploads.register(data, "second.sqlite", "s1")
        third = uploads.register(data, "third.sqlite", "s1")

        assert uploads.resolve_path(first["db_id"]) is None, "oldest should have been evicted"
        assert uploads.resolve_path(second["db_id"])
        assert uploads.resolve_path(third["db_id"])

    def test_eviction_also_frees_the_evicted_collection(self, isolated, tmp_path, monkeypatch):
        monkeypatch.setattr(uploads, "MAX_CONCURRENT", 1)
        data = make_db_bytes(tmp_path)

        first = uploads.register(data, "first.sqlite", "s1")
        uploads.register(data, "second.sqlite", "s1")

        assert SchemaVectorStore().has_schema(first["db_id"]) is False

    def test_entries_past_their_ttl_are_evicted(self, isolated, tmp_path, monkeypatch):
        entry = uploads.register(make_db_bytes(tmp_path), "old.sqlite", "s1")

        # Backdate the entry rather than sleeping.
        reg = uploads._load()
        reg[entry["db_id"]]["created_at"] -= uploads.TTL_SECONDS + 60
        uploads._save(reg)

        assert uploads.evict() == [entry["db_id"]]
        assert uploads.resolve_path(entry["db_id"]) is None

    def test_a_fresh_entry_survives_eviction(self, isolated, tmp_path):
        entry = uploads.register(make_db_bytes(tmp_path), "fresh.sqlite", "s1")
        assert uploads.evict() == []
        assert uploads.resolve_path(entry["db_id"])

    def test_an_entry_whose_file_vanished_is_dropped(self, isolated, tmp_path):
        """The container's temp directory can be cleared under us; a registry
        entry pointing at nothing must not stay in the dropdown."""
        entry = uploads.register(make_db_bytes(tmp_path), "vanish.sqlite", "s1")
        __import__("os").unlink(uploads._load()[entry["db_id"]]["path"])

        assert uploads.evict() == [entry["db_id"]]
        assert entry["db_id"] not in uploads._load()
