"""MCP server that exposes the agentic NL2SQL pipeline to MCP clients.

WHAT MCP IS, IN ONE PARAGRAPH
-----------------------------
The Model Context Protocol is a wire protocol that lets an LLM host (Claude
Desktop, Claude Code, an IDE, a custom agent) discover and call tools that live
in a separate process. The host speaks JSON-RPC to this process over stdio (or
HTTP/SSE). Every function decorated with `@mcp.tool()` below is advertised to
the host during the `tools/list` handshake -- name, description, and a JSON
Schema for its arguments -- and the host's model decides when to call it. The
docstrings in this file are not documentation for humans alone: FastMCP lifts
them verbatim into the tool description the model reads when deciding whether a
tool is relevant. Vague docstrings produce a model that calls the wrong tool.

WHY THIS IS A PROXY AND NOT AN IN-PROCESS AGENT
-----------------------------------------------
This module does NOT import `src.graph` or call `build_graph()`. It forwards
HTTP requests to the FastAPI service in `api/main.py` instead. Two reasons:

1. Memory. `build_graph()` pulls in Chroma plus the ONNX embedder used for
   schema retrieval. `k8s/03-api.yaml` sizes the API container at a 1Gi limit
   precisely because 512Mi got OOM-killed (exit 137) on the first real query
   with exactly ONE copy of that stack resident. Building the graph here too
   would mean a second full copy -- roughly double the footprint -- to compute
   answers the API can already compute.

2. Drift. An in-process copy is a second code path. The moment `api/main.py`
   changes how it seeds state, handles retries, or shapes its response, this
   file would silently keep serving the old behavior. As a proxy, there is
   exactly one implementation of "run the agent," and MCP callers get the same
   answers as the REST API and the Streamlit UI by construction.

The cost of the proxy design is that the API must be running and reachable.
That is a deployment concern, handled with a clear error message below rather
than by duplicating the engine.

RUNNING IT
----------
    uvicorn api.main:app --port 8000     # the real engine, started separately
    python -m mcp_server.server          # this process, speaking stdio MCP

See mcp_server/README.md for client configuration.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ValidationError

# The server name is part of the MCP handshake. Hosts display it and use it to
# namespace tools, so it should be stable and unique across the servers a user
# has installed.
mcp = FastMCP("nl2sql_mcp")

# Where the real engine lives. Env-configurable because the same image runs in
# three places with three different addresses: localhost during development,
# the `nl2sql-api` ClusterIP Service in Kubernetes, and whatever host a user
# points at when running this server from a desktop MCP client.
API_URL = os.getenv("NL2SQL_API_URL", "http://localhost:8000").rstrip("/")

# A full agent run is a planner call, a generator call, execution, and up to
# MAX_RETRIES correction rounds -- each round is another LLM round trip. httpx
# defaults to 5 seconds, which would abort nearly every real query, so the
# timeout is raised well past the worst realistic case.
TIMEOUT_SECONDS = float(os.getenv("NL2SQL_MCP_TIMEOUT", "180"))

# The six BIRD-SQL Mini-Dev databases committed to this repo. The full Mini-Dev
# set has eleven; the other five are excluded by .gitignore because they blow
# past GitHub's file-size limit. Keeping this list as a literal (rather than
# scanning the filesystem, as ui/app.py does) is deliberate: this process may
# run on a different machine from the API and has no view of its disk.
DATABASES: dict[str, str] = {
    "california_schools": (
        "California public schools with SAT/ACT scores, funding, and free-lunch "
        "eligibility, joined to per-school administrative records."
    ),
    "formula_1": (
        "Formula 1 racing history: races, circuits, drivers, constructors, "
        "lap times, pit stops, qualifying, and championship standings."
    ),
    "student_club": (
        "A university club's operations: members, majors, events, attendance, "
        "budgets, and expense reimbursements."
    ),
    "superhero": (
        "Comic-book superheroes with publishers, powers, alignments, races, "
        "and physical attributes across normalized lookup tables."
    ),
    "thrombosis_prediction": (
        "Anonymized medical records for thrombosis research: patients, "
        "examinations, and time-series laboratory test results."
    ),
    "toxicology": (
        "Molecular chemistry data: molecules labeled carcinogenic or not, with "
        "their constituent atoms and the bonds between them."
    ),
}


class QueryRequest(BaseModel):
    """Request body for `POST /query`, mirroring `api.main.QueryRequest`.

    Modeling the outgoing payload rather than hand-building a dict means a
    field rename on either side fails loudly here instead of silently sending
    a key the API ignores.
    """

    db_id: str
    question: str
    evidence: str | None = None


class QueryResult(BaseModel):
    """What this tool hands back to the MCP client.

    A fixed shape on BOTH the success and failure paths. Tool results are read
    by a model, not by exception-handling code, so every failure -- validation,
    connection refused, HTTP 500, malformed JSON -- comes back as a normal
    result with `success=False` and a populated `error`, never as a raised
    exception. A model that gets a structured failure can explain it or retry;
    a model that gets a protocol-level error usually just stops.

    Note this intentionally drops the `trace` field the REST API returns. The
    trace is a verbose per-agent log meant for the Streamlit UI's debugging
    panel; pushing it through an MCP tool would burn a large amount of the
    caller's context for information the model cannot act on.
    """

    success: bool = Field(description="True only if the agent produced SQL that executed cleanly.")
    sql: str | None = Field(default=None, description="The final generated SQL, if any was produced.")
    result: list | None = Field(default=None, description="Result rows from executing the SQL.")
    retries: int = Field(default=0, description="Self-correction rounds the agent needed (0 means first attempt worked).")
    error: str | None = Field(default=None, description="Verbatim failure text when success is False.")


@mcp.tool(
    annotations={
        "title": "Query a database in natural language",
        "readOnlyHint": True,
        # The pipeline calls an LLM at the planning, generation, and error
        # classification steps. LLM sampling is stochastic, so the same
        # (question, db_id) can yield different SQL -- and therefore different
        # rows -- on two consecutive calls. Advertising idempotency here would
        # invite hosts to dedupe or cache repeated calls, which would hide that
        # variance and make the self-correction loop's retries invisible.
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
def nl2sql_query_database(
    question: Annotated[
        str,
        Field(
            min_length=1,
            description="The question to answer, in plain English. E.g. 'Which constructor won the most races?'",
        ),
    ],
    db_id: Annotated[
        str,
        Field(
            min_length=1,
            description="Which database to query. Call nl2sql_list_databases first to see valid values.",
        ),
    ],
    evidence: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional domain hint mapping vague wording to concrete columns, "
                "e.g. \"'eligible free rate' = Free Meal Count / Enrollment\"."
            ),
        ),
    ] = None,
) -> QueryResult:
    """Answer a natural-language question about a relational database.

    Translates the question into SQL, runs it, and returns both the SQL and the
    rows. Use this whenever a question needs facts that live in one of the
    bundled databases -- do not guess at the data, and do not ask the user to
    write SQL.

    HOW THE ANSWER IS PRODUCED
    --------------------------
    The request goes to a multi-agent LangGraph pipeline (running behind the
    project's FastAPI service) that runs four stages:

      1. Schema retriever -- embeds the question and does a vector search over
         Chroma for the tables and columns most likely to be relevant. Only
         those reach the prompt, so a 30-table schema does not have to fit in
         the generator's context window.
      2. Query planner -- an LLM writes a step-by-step plan (which tables, which
         joins, which aggregation) before any SQL is written.
      3. SQL generator -- an LLM turns the plan plus the retrieved schema into
         a single SQLite statement.
      4. Executor + error classifier -- runs the SQL. On failure, an LLM
         classifies the error (bad column, bad join, syntax) and feeds that
         diagnosis back to the generator for another attempt, up to the
         server's retry budget.

    WHAT THE FIELDS MEAN
    --------------------
    - `success`: the SQL executed without raising. It does NOT certify that the
      SQL answers the question correctly -- syntactically valid SQL against the
      wrong column still succeeds. Read the returned `sql` before trusting the
      numbers.
    - `sql`: the final statement. Present even on some failures, which is what
      makes a failure debuggable.
    - `result`: the rows, as a list. Empty list means the query ran and matched
      nothing -- a real answer, not an error.
    - `retries`: how many self-correction rounds were needed. A non-zero value
      on a successful call is normal, not a warning.
    - `error`: populated only when `success` is False.

    ABOUT `evidence`
    ----------------
    BIRD-SQL ships a human-written hint with each benchmark question, because
    many questions are unanswerable without domain knowledge that is nowhere in
    the schema ("eligible free rate" is not a column; it is a ratio of two).
    Passing that kind of hint measurably helps. Note the server only forwards
    it into agent prompts when INCLUDE_EVIDENCE_IN_PROMPTS is enabled in its
    configuration -- if it is off, this argument is accepted and ignored.

    NOT IDEMPOTENT
    --------------
    Three of the four stages call an LLM, so repeated identical calls can
    return different SQL and different rows. Treat each call as a fresh sample,
    not a cache lookup.

    Args:
        question: The question to answer, in plain English.
        db_id: Which database to query; see `nl2sql_list_databases`.
        evidence: Optional domain hint. Omit it if you do not have one --
            inventing a hint is worse than passing none.

    Returns:
        A QueryResult. Never raises; failures arrive as `success=False` with
        `error` set.
    """
    # Membership is checked here, at runtime, rather than typed as a Literal
    # enum on the parameter. A Literal would make FastMCP reject a bad db_id
    # with a protocol-level validation error, breaking the uniform result shape
    # this tool promises. A structured error that names the valid options is
    # something the calling model can actually recover from.
    if db_id not in DATABASES:
        return QueryResult(
            success=False,
            error=(
                f"Unknown db_id {db_id!r}. Available databases: "
                f"{', '.join(sorted(DATABASES))}. Call nl2sql_list_databases for descriptions."
            ),
        )

    try:
        payload = QueryRequest(db_id=db_id, question=question, evidence=evidence)
    except ValidationError as exc:
        return QueryResult(success=False, error=f"Invalid arguments: {exc}")

    try:
        response = httpx.post(
            f"{API_URL}/query",
            json=payload.model_dump(),
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
    except httpx.TimeoutException:
        return QueryResult(
            success=False,
            error=(
                f"The NL2SQL API at {API_URL} did not respond within {TIMEOUT_SECONDS:.0f}s. "
                "A query needing several self-correction rounds can be slow; raise "
                "NL2SQL_MCP_TIMEOUT if this recurs."
            ),
        )
    except httpx.HTTPStatusError as exc:
        # Include the body: FastAPI puts the actual cause in there, and a bare
        # status code sends the caller hunting through server logs.
        return QueryResult(
            success=False,
            error=f"NL2SQL API returned HTTP {exc.response.status_code}: {exc.response.text}",
        )
    except httpx.HTTPError as exc:
        return QueryResult(
            success=False,
            error=(
                f"Could not reach the NL2SQL API at {API_URL}: {exc}. "
                "This MCP server is a proxy; start the API with "
                "`uvicorn api.main:app --port 8000` or point NL2SQL_API_URL at a running instance."
            ),
        )
    except ValueError as exc:
        # response.json() on a non-JSON body -- typically a proxy or load
        # balancer error page sitting in front of the API.
        return QueryResult(success=False, error=f"NL2SQL API returned a non-JSON response: {exc}")

    return QueryResult(
        success=data.get("success", False),
        sql=data.get("sql"),
        result=data.get("result"),
        retries=data.get("retries", 0),
        error=None if data.get("success") else "The agent could not produce SQL that executed successfully.",
    )


@mcp.tool(
    annotations={
        "title": "List available databases",
        "readOnlyHint": True,
        # Pure lookup over a module-level constant: same input, same output,
        # every time, with no side effects. The opposite of the query tool, and
        # a useful contrast for understanding what these hints are for.
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def nl2sql_list_databases() -> dict[str, str]:
    """List the databases `nl2sql_query_database` can query, with descriptions.

    Call this FIRST when you do not already know which `db_id` fits the user's
    question. The descriptions say what subject matter each database covers, so
    a question about lap times routes to `formula_1` and one about lab results
    routes to `thrombosis_prediction`.

    These are six databases from the BIRD-SQL Mini-Dev benchmark, the set this
    project is evaluated against. The full Mini-Dev suite has eleven; the other
    five exceed GitHub's file-size limit and are not committed to this repo.

    The list is a static constant rather than a call to the API. This tool is
    metadata-only, and a discovery step should not fail just because the
    backend happens to be down -- a model can then at least report which
    database it WOULD have used.

    Returns:
        A dict mapping each valid `db_id` to a one-line description of its
        contents. The keys are exactly the values accepted by the `db_id`
        argument of `nl2sql_query_database`.
    """
    return DATABASES


if __name__ == "__main__":
    # stdio is FastMCP's default transport: the host launches this file as a
    # subprocess and speaks JSON-RPC over its stdin/stdout. Nothing may be
    # printed to stdout anywhere in this process -- a stray print() corrupts
    # the protocol stream. Use stderr for any debugging output.
    mcp.run()
