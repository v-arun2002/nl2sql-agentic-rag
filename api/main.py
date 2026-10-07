import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src import demo_limits
from src.graph import build_graph
from src.agents.state import initial_state
from src.config import settings
from src.db import sql_guard
from src.db.executor import resolve_db_path
from src.explain import explain

app = FastAPI(title="Agentic NL2SQL API", version="0.2.0")
graph = build_graph()


class QueryRequest(BaseModel):
    db_id: str
    question: str
    evidence: str | None = None  # optional BIRD-SQL-style domain hint; only
                                   # reaches agent prompts if INCLUDE_EVIDENCE_IN_PROMPTS=true


class QueryResponse(BaseModel):
    sql: str | None
    result: list | None
    success: bool
    retries: int
    trace: list


@app.post("/query", response_model=QueryResponse)
def run_query(req: QueryRequest):
    state = initial_state(req.db_id, req.question, evidence=req.evidence, max_retries=settings.max_retries)
    result = graph.invoke(state)

    return QueryResponse(
        sql=result.get("sql_query"),
        result=result.get("execution_result"),
        success=result.get("success", False),
        retries=result.get("retry_count", 0),
        trace=result.get("trace", []),
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Granular endpoints behind the multi-tool MCP server (mcp_server/server.py).
#
# /query above runs the whole pipeline in one call. These split it into
# separately callable steps so an external agent can generate SQL, inspect or
# edit it, check it, run it, and summarise it -- each as its own decision.
#
# The split is NOT a cut through the middle of the graph. Self-correction is
# driven by execution errors: the classifier routes on what went wrong when
# the SQL ran. So /sql/generate still runs the full graph, including its
# internal read-only trial executions, and returns the CORRECTED SQL. Cutting
# the graph before the executor would return first-draft SQL and silently
# score below the benchmarked 44.20% -- the number describes the corrected
# output, so that is what this endpoint has to return.
# ---------------------------------------------------------------------------


def _db_path(db_id: str) -> str:
    path = resolve_db_path(db_id.strip())
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Unknown database: {db_id!r}")
    return path


def _spending_guard() -> None:
    """The demo's daily cap guards LLM spend, so it applies to the two
    endpoints that call an LLM -- not to validate/execute, which are free
    local SQLite reads. The per-session half of demo_limits is Streamlit
    session state and has no meaning for an API caller, hence 0."""
    allowed, reason, _, _ = demo_limits.check(0)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)


class GenerateRequest(BaseModel):
    db_id: str
    question: str
    evidence: str | None = None


@app.post("/sql/generate")
def generate_sql(req: GenerateRequest):
    _db_path(req.db_id)
    _spending_guard()

    # Identical call to /query and to eval/run_benchmark.py: same graph, same
    # initial_state, same max_retries. That identity is what lets this
    # endpoint inherit the benchmark numbers.
    state = initial_state(req.db_id, req.question, evidence=req.evidence, max_retries=settings.max_retries)
    result = graph.invoke(state)
    demo_limits.record()

    return {
        "sql": result.get("sql_query"),
        "pipeline_trial_succeeded": bool(result.get("success")),
        "retries": result.get("retry_count", 0),
        "trial_error": result.get("execution_error"),
    }


class ValidateRequest(BaseModel):
    db_id: str
    sql: str


@app.post("/sql/validate")
def validate_sql(req: ValidateRequest):
    path = _db_path(req.db_id)
    # A refusal is a normal, expected answer from a safety gate -- 200 with
    # valid=false, not an HTTP error the caller has to special-case.
    try:
        meta = sql_guard.validate(path, req.sql)
    except sql_guard.SQLRefused as e:
        return {"valid": False, "refusal_category": e.category, "reason": str(e)}
    return {"valid": True, **meta}


class ExecuteRequest(BaseModel):
    db_id: str
    sql: str
    max_rows: int | None = Field(default=None, ge=1)


@app.post("/sql/execute")
def execute_sql(req: ExecuteRequest):
    path = _db_path(req.db_id)
    try:
        out = sql_guard.execute(path, req.sql, max_rows=req.max_rows)
    except sql_guard.SQLRefused as e:
        return {"success": False, "refusal_category": e.category, "reason": str(e)}
    return {"success": True, **out}


class ExplainRequest(BaseModel):
    question: str | None = None
    sql: str | None = None
    columns: list = []
    rows: list = []
    truncated: bool = False


@app.post("/sql/explain")
def explain_result(req: ExplainRequest):
    _spending_guard()
    out = explain(req.question, req.sql, req.columns, req.rows, req.truncated)
    demo_limits.record()
    return out


@app.get("/capabilities")
def capabilities():
    """Enforcement facts, read from the constants the code actually enforces,
    so the MCP capability manifest cannot advertise a limit that isn't real."""
    return {
        "sql_gate": sql_guard.enforcement_facts(),
        "spending_guard": {
            "applies_to": ["/sql/generate", "/sql/explain"],
            "enabled": demo_limits.ENABLED,
            "daily_limit": demo_limits.DAILY_LIMIT if demo_limits.ENABLED else None,
        },
        "explainer_model": f"{settings.explainer_provider}/{settings.explainer_model}",
        "max_retries": settings.max_retries,
    }
