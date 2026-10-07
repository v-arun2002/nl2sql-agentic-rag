"""
Error Classifier Agent -- the piece that makes correction "smart" instead of
"retry blindly." SQLite error strings are highly regular, so a cheap
rule-based pass catches most cases for free; the LLM (on the cheap model) is
only invoked for ambiguous cases like an empty/unexpected result set, where
there's no exception to pattern-match against.

The classification decides WHERE the correction router sends the state next
-- see graph.py's route_after_classification.
"""

from typing import get_args

from src.config import settings
from src.llm_providers import QuotaExhaustedError, generate_text
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


_VALID_CLASSES = set(get_args(ErrorClass))


def error_classifier_node(state: AgentState) -> dict:
    error_msg = state.get("execution_error") or ""
    error_class = _rule_based_classify(error_msg)
    source = "rule_based"
    failure = None

    if error_class is None:
        # Ambiguous case (e.g. query succeeded but result looks wrong) -- ask the model.
        source = "llm"
        user_prompt = (
            f"Question: {state['question']}\n"
            f"SQL: {state['sql_query']}\n"
            f"Error or result: {error_msg or 'Query ran but returned an empty or unexpected result.'}"
        )
        # A failed classification must never fail the QUESTION. Unguarded,
        # any exception here -- a retired model, a timeout, a provider outage
        # -- propagated out of graph.invoke and took down the whole question;
        # in the benchmark that is a fatal_error row, and in the API a 500.
        # Classifying as UNKNOWN_ERROR instead ends the correction loop
        # cleanly: the question finishes as a normal failure with its SQL and
        # error intact. Retrying blindly would be no better -- with no idea
        # what went wrong there is no agent to route the retry to.
        try:
            raw = generate_text(
                provider=settings.classifier_provider,
                model=settings.classifier_model,
                system_prompt=CLASSIFIER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_output_tokens=20,
            )
            error_class = raw.strip()
        except QuotaExhaustedError:
            # The one exception NOT absorbed here. An out-of-credit account
            # invalidates the whole run, not this question -- swallowing it
            # would score the question as wrong and let the run carry on
            # recording more of them. It propagates so run_benchmark.py stops.
            raise
        except Exception as e:  # noqa: BLE001 -- deliberately total; see above
            error_class = "UNKNOWN_ERROR"
            source = "llm_failed"
            failure = f"{type(e).__name__}: {e}"[:300]

        # A response that is not a known class (prose, punctuation, a
        # hallucinated label) is recorded as UNKNOWN_ERROR. This changes no
        # routing: route_after_classification already sends anything that is
        # not SCHEMA/SYNTAX/LOGIC to END. Matching is deliberately exact --
        # loosening it (case-folding, stripping punctuation) would start
        # RETRYING answers that end today, which would change pipeline
        # behaviour rather than just harden it.
        if error_class not in _VALID_CLASSES:
            if failure is None:
                failure = f"unrecognised classifier response: {error_class[:100]!r}"
                source = "llm_invalid"
            error_class = "UNKNOWN_ERROR"

    trace_entry = {
        "node": "error_classifier",
        "retry_count": state["retry_count"],
        "error_class": error_class,
        "source": source,
    }
    if failure:
        trace_entry["classifier_failure"] = failure

    return {
        "error_class": error_class,
        "retry_count": state["retry_count"] + 1,
        "trace": state["trace"] + [trace_entry],
    }
