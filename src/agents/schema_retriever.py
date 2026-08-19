"""
Schema Retriever Agent.

Entry point of the graph, and the re-entry point when the error classifier
flags a SCHEMA_ERROR -- in that case it retrieves a wider slice of the
schema on retry.
"""

from src import uploads
from src.retrieval.vector_store import SchemaVectorStore
from src.retrieval.cache import get_cached_retrieval, set_cached_retrieval
from src.agents.state import AgentState

_shipped_store = SchemaVectorStore()
_upload_store = None


def store_for(db_id: str) -> SchemaVectorStore:
    """
    Uploads live in their own Chroma directory (see src/uploads.py): the shipped
    index is a committed artifact the deployed app cannot rebuild, while upload
    segments are per-session garbage. Same reason the executor resolves upload
    paths separately -- one db_id namespace, two stores with different lifecycles.
    """
    global _upload_store
    if not uploads.is_upload(db_id):
        return _shipped_store
    if _upload_store is None:
        _upload_store = uploads.store()
    return _upload_store


def schema_retriever_node(state: AgentState) -> dict:
    # On a schema-error retry, widen the search since the first pass likely
    # missed the table/column the question actually needed.
    top_k = 6 if state["retry_count"] == 0 else 10

    relevant_tables = get_cached_retrieval(state["db_id"], state["question"], top_k)
    cache_hit = relevant_tables is not None

    if not cache_hit:
        relevant_tables = store_for(state["db_id"]).retrieve_relevant_tables(
            state["db_id"], state["question"], top_k=top_k
        )
        set_cached_retrieval(state["db_id"], state["question"], top_k, relevant_tables)

    schema_text = "\n\n".join(t["schema_text"] for t in relevant_tables if t.get("schema_text"))

    trace_entry = {
        "node": "schema_retriever",
        "retry_count": state["retry_count"],
        "retrieved_tables": [t["table_name"] for t in relevant_tables],
        "cache_hit": cache_hit,
    }

    return {
        "schema_context": schema_text,
        "trace": state["trace"] + [trace_entry],
    }