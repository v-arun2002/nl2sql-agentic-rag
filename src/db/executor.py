"""
Execution node -- runs the generated SQL against the target SQLite database
and captures either a result set or an error string for the classifier.
"""

import sqlite3

from src.config import settings
from src.agents.state import AgentState


def executor_node(state: AgentState) -> dict:
    db_path = f"{settings.benchmark_data_path}/dev_databases/{state['db_id']}/{state['db_id']}.sqlite"

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
        conn = sqlite3.connect(db_path, timeout=5)
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