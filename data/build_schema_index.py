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

from src.config import settings
from src.db.connection import connect_readonly
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


def introspect_sqlite(sqlite_path: str, all_descriptions: dict = None) -> dict:
    """
    Path-based introspection, so this works for an arbitrary .sqlite file and
    not only for a BIRD database resolved from a db_id -- which is what the
    demo's upload feature needs (see src/uploads.py).

    Opened read-only: introspection has no business writing, and an upload is
    someone else's data.
    """
    all_descriptions = all_descriptions or {}
    conn = connect_readonly(sqlite_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    table_names = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]

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


def sqlite_path_for(db_id: str) -> str:
    return f"{settings.benchmark_data_path}/dev_databases/{db_id}/{db_id}.sqlite"


def build_tables_dict(db_id: str) -> dict:
    """BIRD database by db_id, with its shipped column descriptions folded in."""
    return introspect_sqlite(sqlite_path_for(db_id), load_column_descriptions(db_id))


def main():
    # DB_IDS lists all 11 BIRD databases, but the repo commits only 6 and the
    # BIRD download is opt-in, so a missing .sqlite is the normal case rather
    # than an error. Skip it and carry on -- without this guard the run dies on
    # whichever database happens to be missing first and indexes nothing after.
    store = SchemaVectorStore()
    indexed, skipped = 0, []
    for db_id in DB_IDS:
        if not os.path.isfile(sqlite_path_for(db_id)):
            skipped.append(db_id)
            continue
        tables = build_tables_dict(db_id)
        store.index_schema(db_id, tables)
        total_cols = sum(len(t["columns"]) for t in tables.values())
        print(f"Indexed {db_id}: {len(tables)} tables, {total_cols} columns (with types + samples)")
        indexed += 1

    if skipped:
        print(f"\nSkipped {len(skipped)} database(s) with no .sqlite file: {', '.join(skipped)}")
        print("See data/README.md for how to fetch the full BIRD dev set.")
    if indexed == 0:
        raise SystemExit(
            f"No databases indexed -- found no .sqlite files under "
            f"{settings.benchmark_data_path}/dev_databases/. "
            "An empty index silently produces hallucinated schemas, so this is a hard failure."
        )


if __name__ == "__main__":
    main()