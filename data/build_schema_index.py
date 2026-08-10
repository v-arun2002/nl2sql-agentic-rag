"""
Schema indexing. Re-run any time you refresh the data or delete chroma_db/.

Introspects each .sqlite file directly (PRAGMA table_info) for column names
AND types, then samples a few real values per column. The sample values
matter enormously: BIRD's `yearmonth.Date` column stores compact strings
like '201308', not ISO dates -- without seeing an actual value, a model
reasonably guesses ISO format and writes strftime()/BETWEEN clauses that
silently match zero rows. Names alone can't convey that; one example can.

Also folds in BIRD's own database_description/*.csv column descriptions
per-column when present.
"""

import csv
import os
import sqlite3

from src.config import settings
from src.retrieval.vector_store import SchemaVectorStore

DB_IDS = [
    "california_schools", "card_games", "codebase_community",
    "debit_card_specializing", "european_football_2", "financial",
    "formula_1", "student_club", "superhero", "thrombosis_prediction",
    "toxicology",
]

SAMPLES_PER_COLUMN = 3


def load_column_descriptions(db_id: str) -> dict:
    """
    Returns {table_name: {column_name: description}}.
    Best-effort: BIRD's CSVs vary in encoding and header naming across
    releases, so a malformed file yields no descriptions rather than
    crashing the run.
    """
    descriptions: dict = {}
    desc_dir = f"{settings.benchmark_data_path}/dev_databases/{db_id}/database_description"
    if not os.path.isdir(desc_dir):
        return descriptions

    for filename in os.listdir(desc_dir):
        if not filename.endswith(".csv"):
            continue
        table_name = filename[:-4]
        per_column = {}
        try:
            with open(f"{desc_dir}/{filename}", encoding="utf-8-sig", errors="ignore") as f:
                for row in csv.DictReader(f):
                    col = row.get("original_column_name") or row.get("column_name")
                    desc = row.get("column_description") or ""
                    value_desc = row.get("value_description") or ""
                    combined = " ".join(p.strip() for p in (desc, value_desc) if p and p.strip())
                    if col and combined:
                        per_column[col.strip()] = combined
        except Exception:
            continue
        if per_column:
            descriptions[table_name] = per_column

    return descriptions


def sample_values(cursor, table_name: str, column_name: str) -> list:
    """A few distinct non-null values, as strings, for format disambiguation."""
    try:
        cursor.execute(
            f'SELECT DISTINCT "{column_name}" FROM "{table_name}" '
            f'WHERE "{column_name}" IS NOT NULL LIMIT {SAMPLES_PER_COLUMN}'
        )
        values = [row[0] for row in cursor.fetchall()]
    except Exception:
        return []

    out = []
    for v in values:
        s = str(v)
        if len(s) > 40:          # keep long text/blob values from bloating the prompt
            s = s[:37] + "..."
        out.append(repr(s) if isinstance(v, str) else s)
    return out


def build_tables_dict(db_id: str) -> dict:
    sqlite_path = f"{settings.benchmark_data_path}/dev_databases/{db_id}/{db_id}.sqlite"
    conn = sqlite3.connect(sqlite_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    table_names = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]

    all_descriptions = load_column_descriptions(db_id)

    tables = {}
    for table_name in table_names:
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        # PRAGMA columns: (cid, name, type, notnull, dflt_value, pk)
        raw_columns = cursor.fetchall()
        table_descriptions = all_descriptions.get(table_name, {})

        columns = []
        for row in raw_columns:
            col_name, col_type = row[1], row[2]
            columns.append({
                "name": col_name,
                "type": col_type or "",
                "samples": sample_values(cursor, table_name, col_name),
                "description": table_descriptions.get(col_name, ""),
            })

        tables[table_name] = {"columns": columns}

    conn.close()
    return tables


def main():
    store = SchemaVectorStore()
    for db_id in DB_IDS:
        tables = build_tables_dict(db_id)
        store.index_schema(db_id, tables)
        total_cols = sum(len(t["columns"]) for t in tables.values())
        print(f"Indexed {db_id}: {len(tables)} tables, {total_cols} columns (with types + samples)")


if __name__ == "__main__":
    main()