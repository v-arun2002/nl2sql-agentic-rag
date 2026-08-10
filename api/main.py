from fastapi import FastAPI
from pydantic import BaseModel

from src.graph import build_graph
from src.agents.state import initial_state
from src.config import settings

app = FastAPI(title="Agentic NL2SQL API", version="0.1.0")
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
