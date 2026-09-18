# MCP server

Exposes the agentic NL2SQL pipeline to any MCP client (Claude Desktop, Claude
Code, an IDE, a custom agent) as two callable tools.

## Architecture

```
MCP client  ──stdio/JSON-RPC──▶  mcp_server/server.py  ──HTTP──▶  api/main.py  ──▶  LangGraph agents
                                 (thin proxy)                      (the engine)
```

`server.py` is a **proxy**. It never imports `src.graph` and never calls
`build_graph()`. Two reasons:

- **Memory.** `build_graph()` loads Chroma plus the ONNX embedder. `k8s/03-api.yaml`
  sets the API container's limit to 1Gi because 512Mi was OOM-killed on the
  first real query with one copy of that stack resident. An in-process copy
  here would roughly double the footprint to compute answers the API already
  computes.
- **Drift.** One implementation of "run the agent" means MCP callers, the REST
  API, and the Streamlit UI cannot diverge.

The trade-off: **the API must be running and reachable.** When it is not, the
query tool returns `success=False` with an error naming the URL it tried,
rather than failing at the protocol level.

## Tools

| Tool | Purpose | Idempotent |
| --- | --- | --- |
| `nl2sql_query_database(question, db_id, evidence=None)` | Translate the question to SQL, execute it, return `success` / `sql` / `result` / `retries` / `error` | **No** — three pipeline stages call an LLM, so identical input can yield different SQL and rows |
| `nl2sql_list_databases()` | Static map of the six bundled BIRD-SQL Mini-Dev `db_id`s to one-line descriptions | Yes |

Both are annotated `readOnlyHint: True`. The query tool omits the REST API's
`trace` field: it is a verbose per-agent log for the Streamlit debugging panel,
and forwarding it would consume a large amount of the caller's context.

## Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `NL2SQL_API_URL` | `http://localhost:8000` | Base URL of the FastAPI service |
| `NL2SQL_MCP_TIMEOUT` | `180` | Per-query timeout in seconds; a run with several self-correction rounds is several LLM round trips |

## Running

Start the engine first, then the MCP server:

```bash
uvicorn api.main:app --port 8000
python -m mcp_server.server
```

Client config (Claude Desktop `claude_desktop_config.json`, or
`claude mcp add` for Claude Code):

```json
{
  "mcpServers": {
    "nl2sql_mcp": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/nl2sql-agentic-rag",
      "env": { "NL2SQL_API_URL": "http://localhost:8000" }
    }
  }
}
```

Use the interpreter that has `requirements.txt` installed — on Windows with the
bundled venv, that is `venv\Scripts\python.exe`.

## Note on stdio

The default transport is stdio: the host launches this file as a subprocess and
speaks JSON-RPC over stdin/stdout. **Nothing may print to stdout** anywhere in
this process — a stray `print()` corrupts the protocol stream. Use stderr.
