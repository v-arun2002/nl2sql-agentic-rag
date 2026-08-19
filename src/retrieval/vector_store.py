"""
Schema retrieval store.

Two distinct representations per table, deliberately:
  - the EMBEDDED document is table name + column names + descriptions,
    which is what semantic search matches a question against;
  - the STORED schema_text is a richer, prompt-ready block including column
    types and real sample values, handed to the planner/generator.

Keeping them separate matters: stuffing sample values into the embedded text
adds noise to retrieval, while withholding them from the prompt is what
causes format-guessing bugs (e.g. treating '201308' as an ISO date).
"""

import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import List, Dict, Any

import chromadb
from chromadb.utils import embedding_functions

from src.config import settings


class SchemaVectorStore:
    def __init__(self, persist_dir: str = None):
        self.client = chromadb.PersistentClient(path=persist_dir or settings.chroma_persist_dir)
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()

    def _collection_name(self, db_id: str) -> str:
        return f"schema_{db_id}"

    @staticmethod
    def _build_schema_text(table_name: str, columns: List[Dict[str, Any]]) -> str:
        lines = [f"Table: {table_name}"]
        for col in columns:
            parts = [f"  - {col['name']}"]
            if col.get("type"):
                parts.append(f"({col['type']})")
            if col.get("samples"):
                parts.append(f"-- sample values: {', '.join(col['samples'])}")
            if col.get("description"):
                desc = col["description"]
                parts.append(f"-- {desc[:120]}")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def index_schema(
        self,
        db_id: str,
        tables: Dict[str, Dict[str, Any]],
        on_progress=None,
        batch_size: int = 8,
    ) -> None:
        """
        tables: {table_name: {"columns": [{"name", "type", "samples", "description"}, ...]}}
        See data/build_schema_index.py for how this is built from the .sqlite files.

        on_progress(done, total) is called after each batch is embedded. Embedding
        is the slow step and a wide schema takes real time, so the demo's upload
        flow needs something to report while it waits. Batching rather than
        per-table upsert keeps the fast path fast.
        """
        collection = self.client.get_or_create_collection(
            name=self._collection_name(db_id),
            embedding_function=self.embedding_fn,
        )

        documents, ids, metadatas = [], [], []
        for table_name, info in tables.items():
            columns = info.get("columns", [])
            col_names = [c["name"] for c in columns]
            col_descriptions = " ".join(c.get("description", "") for c in columns)[:500]

            documents.append(f"Table: {table_name}. Columns: {', '.join(col_names)}. {col_descriptions}")
            ids.append(table_name)
            metadatas.append({
                "table_name": table_name,
                "schema_text": self._build_schema_text(table_name, columns),
            })

        total = len(documents)
        if not total:
            return

        for start in range(0, total, batch_size):
            stop = min(start + batch_size, total)
            collection.upsert(
                documents=documents[start:stop],
                ids=ids[start:stop],
                metadatas=metadatas[start:stop],
            )
            if on_progress:
                on_progress(stop, total)

    def delete_schema(self, db_id: str) -> bool:
        """
        Drop a database's collection entirely. Returns False if it wasn't there.

        Needed because an uploaded database's index has to be reclaimable --
        otherwise collections accumulate in the persist directory for every
        upload the demo has ever seen.
        """
        try:
            self.client.delete_collection(name=self._collection_name(db_id))
        except Exception:
            return False
        self.reap_orphaned_segments()
        return True

    def reap_orphaned_segments(self) -> int:
        """
        Delete HNSW segment directories no longer referenced by any collection,
        and return how many were removed.

        `delete_collection` removes the metadata rows but leaves the segment
        directory on disk -- ~170KB for a small schema and more for a wide one.
        Verified by deleting a collection and finding the directory still there
        with its `data_level0.bin` intact, unreferenced by the `segments` table.
        Without this, every demo upload leaks a directory that is never
        reclaimed, which is the same accumulation the upload TTL exists to stop.

        This reads Chroma's own sqlite metadata, so it is coupled to Chroma's
        on-disk layout: it is written to no-op rather than guess if the schema
        isn't what's expected, and only ever deletes directories whose name is a
        UUID absent from `segments`.

        Best-effort and idempotent, because Chroma keeps a deleted segment's
        files mmap'd for the life of the process and does not release them even
        on clear_system_cache(). On Linux -- the deployed demo -- unlinking an
        open file succeeds, so the space is reclaimed immediately. On Windows the
        directory survives until the process exits, and is then reclaimed by the
        next call. Only directories actually removed are counted, so the return
        value never overstates what was freed.
        """
        persist = Path(self.client.get_settings().persist_directory or settings.chroma_persist_dir)
        meta = persist / "chroma.sqlite3"
        if not meta.exists():
            return 0

        try:
            conn = sqlite3.connect(meta)
            try:
                # Confirm the table exists before trusting an empty result: a
                # genuinely empty store (last collection just deleted) and a
                # layout we don't understand both yield no rows, and only the
                # first should license deleting directories.
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='segments'"
                ).fetchone()
                if not exists:
                    return 0
                live = {row[0] for row in conn.execute("SELECT id FROM segments")}
            finally:
                conn.close()
        except sqlite3.Error:
            return 0  # unexpected layout -- leave the directories alone

        removed = 0
        for child in persist.iterdir():
            if not child.is_dir():
                continue
            try:
                uuid.UUID(child.name)
            except ValueError:
                continue  # not a segment directory
            if child.name not in live:
                shutil.rmtree(child, ignore_errors=True)
                # Count only what is genuinely gone: rmtree swallows the
                # PermissionError raised while the files are still mapped.
                if not child.exists():
                    removed += 1
        return removed

    def has_schema(self, db_id: str) -> bool:
        """Whether a collection exists, without creating one as a side effect."""
        name = self._collection_name(db_id)
        try:
            return any(c.name == name for c in self.client.list_collections())
        except Exception:
            return False

    def retrieve_relevant_tables(self, db_id: str, question: str, top_k: int = 6) -> List[Dict[str, Any]]:
        collection = self.client.get_or_create_collection(
            name=self._collection_name(db_id),
            embedding_function=self.embedding_fn,
        )
        results = collection.query(query_texts=[question], n_results=top_k)

        if not results["documents"] or not results["documents"][0]:
            return []

        return [
            {"table_name": meta["table_name"], "schema_text": meta.get("schema_text", "")}
            for meta in results["metadatas"][0]
        ]