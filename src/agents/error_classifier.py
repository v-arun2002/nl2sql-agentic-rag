"""
Error Classifier Agent -- the piece that makes correction "smart" instead of
"retry blindly." SQLite error strings are highly regular, so a cheap
rule-based pass catches most cases for free; the LLM (on the cheap model) is
only invoked for ambiguous cases like an empty/unexpected result set, where
there's no exception to pattern-match against.

The classification decides WHERE the correction router sends the state next
-- see graph.py's route_after_classification.
"""

from src.config import settings
from src.llm_providers import generate_text
from src.agents.state import AgentState, ErrorClass

CLASSIFIER_SYSTEM_PROMPT = """You classify SQL execution failures into exactly \
one category:
- SCHEMA_ERROR: wrong table or column reference
- SYNTAX_ERROR: malformed SQL
- LOGIC_ERROR: the query ran but its joins/aggregation/filters don't answer \
the question correctly (e.g. empty result when rows are clearly expected, or \
an obviously wrong aggregation)
- UNKNOWN_ERROR: none of the above apply

Respond with ONLY the category name, nothing else."""


def _rule_based_classify(error_msg: str) -> ErrorClass | None:
    msg = error_msg.lower()
    if "no such table" in msg or "no such column" in msg or "ambiguous column name" in msg:
        return "SCHEMA_ERROR"
    if "syntax error" in msg or "unrecognized token" in msg:
        return "SYNTAX_ERROR"
    if "timeout" in msg or "database is locked" in msg:
        return "TIMEOUT_ERROR"
    return None


def error_classifier_node(state: AgentState) -> dict:
    error_msg = state.get("execution_error") or ""
    error_class = _rule_based_classify(error_msg)

    if error_class is None:
        # Ambiguous case (e.g. query succeeded but result looks wrong) -- ask the model.
        user_prompt = (
            f"Question: {state['question']}\n"
            f"SQL: {state['sql_query']}\n"
            f"Error or result: {error_msg or 'Query ran but returned an empty or unexpected result.'}"
        )
        raw = generate_text(
            provider=settings.classifier_provider,
            model=settings.classifier_model,
            system_prompt=CLASSIFIER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_output_tokens=20,
        )
        error_class = raw.strip()

    trace_entry = {
        "node": "error_classifier",
        "retry_count": state["retry_count"],
        "error_class": error_class,
        "source": "rule_based" if _rule_based_classify(error_msg) else "llm",
    }

    return {
        "error_class": error_class,
        "retry_count": state["retry_count"] + 1,
        "trace": state["trace"] + [trace_entry],
    }
