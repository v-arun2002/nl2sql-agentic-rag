"""
Shared state passed between every node in the LangGraph state machine.

Design note: `trace` accumulates one entry per node hop, so the full
correction-loop path (which agent fired, why, how many retries) can be
inspected after the fact -- this is what feeds the eval harness's failure
taxonomy and the Streamlit UI's "agent trace" panel.
"""

from typing import TypedDict, List, Optional, Literal, Any

ErrorClass = Literal[
    "SCHEMA_ERROR",
    "SYNTAX_ERROR",
    "LOGIC_ERROR",
    "TIMEOUT_ERROR",
    "UNKNOWN_ERROR",
]


class AgentState(TypedDict):
    db_id: str
    question: str
    evidence: Optional[str]  # BIRD-SQL's external-knowledge hint; see src/config.py's
                              # include_evidence_in_prompts for whether agents actually see it

    schema_context: Optional[str]
    plan: Optional[dict]
    sql_query: Optional[str]

    execution_result: Optional[List[Any]]
    execution_error: Optional[str]
    error_class: Optional[ErrorClass]

    retry_count: int
    max_retries: int

    trace: List[dict]
    success: bool
    final_answer: Optional[str]


def initial_state(db_id: str, question: str, evidence: Optional[str] = None, max_retries: int = 3) -> AgentState:
    return {
        "db_id": db_id,
        "question": question,
        "evidence": evidence,
        "schema_context": None,
        "plan": None,
        "sql_query": None,
        "execution_result": None,
        "execution_error": None,
        "error_class": None,
        "retry_count": 0,
        "max_retries": max_retries,
        "trace": [],
        "success": False,
        "final_answer": None,
    }
