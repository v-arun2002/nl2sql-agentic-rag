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

import json
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

    def index_schema(self, db_id: str, tables: Dict[str, Dict[str, Any]]) -> None:
        """
        tables: {table_name: {"columns": [{"name", "type", "samples", "description"}, ...]}}
        See data/build_schema_index.py for how this is built from the .sqlite files.
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

        if documents:
            collection.upsert(documents=documents, ids=ids, metadatas=metadatas)

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