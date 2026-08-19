"""
Execution node -- runs the generated SQL against the target SQLite database
and captures either a result set or an error string for the classifier.

The connection is always READ-ONLY. The SQL being run is model output, and the
target may be a database a demo visitor uploaded moments ago; a hallucinated
DROP or DELETE must fail rather than destroy data. The pipeline only needs
SELECT, so nothing is given up. See src/db/connection.py for the two mechanisms.
"""

from src import uploads
from src.config import settings
from src.agents.state import AgentState
from src.db.connection import connect_readonly


def resolve_db_path(db_id: str) -> str:
    """
    Uploaded databases live in a temp directory keyed by db_id; bundled BIRD
    ones are laid out under benchmark_data_path. Uploads are checked first so a
    db_id can never be shadowed by a same-named bundled database.
    """
    uploaded = uploads.resolve_path(db_id)
    if uploaded:
        return uploaded
    return f"{settings.benchmark_data_path}/dev_databases/{db_id}/{db_id}.sqlite"


def executor_node(state: AgentState) -> dict:
    db_path = resolve_db_path(state["db_id"])

    # Guard: an empty query string is a silent no-op in SQLite -- it returns
    # zero rows with no exception, which would otherwise be recorded as a
    # SUCCESS with an empty result. Treat "no SQL" as an explicit failure so
    # the correction loop and the eval both see it for what it is.
    sql = (state.get("sql_query") or "").strip()
    if not sql:
        trace_entry = {"node": "executor", "status": "failed", "error": "empty_sql"}
        return {
            "execution_result": None,
            "execution_error": "The generator produced no SQL query.",
            "success": False,
            "trace": state["trace"] + [trace_entry],
        }

    try:
        conn = connect_readonly(db_path, timeout=5)
        cursor = conn.cursor()
        cursor.execute(sql)
        result = cursor.fetchall()
        conn.close()

        trace_entry = {
            "node": "executor",
            "status": "success",
            "row_count": len(result),
        }
        return {
            "execution_result": result,
            "execution_error": None,
            "success": True,
            "trace": state["trace"] + [trace_entry],
        }

    except Exception as e:
        trace_entry = {
            "node": "executor",
            "status": "failed",
            "error": str(e),
        }
        return {
            "execution_result": None,
            "execution_error": str(e),
            "success": False,
            "trace": state["trace"] + [trace_entry],
        }