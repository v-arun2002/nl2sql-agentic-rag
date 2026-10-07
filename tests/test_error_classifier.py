"""
The error classifier must never take a question down with it.

Free and offline: generate_text is replaced, so no API key or network is
needed. The failure cases are the ones that actually happened -- Groq retired
the classifier's default model, and the unguarded call crashed graph.invoke
for every question whose error the rule-based pass could not place.
"""

import pytest

from src.agents import error_classifier
from src.agents.error_classifier import error_classifier_node
from src.graph import route_after_classification


def _state(execution_error="", retry_count=0):
    return {
        "question": "Which constructor won the most races?",
        "sql_query": "SELECT constructorId FROM results",
        "execution_error": execution_error,
        "retry_count": retry_count,
        "max_retries": 3,
        "trace": [],
    }


def _raise(exc):
    def fake(**kwargs):
        raise exc
    return fake


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Error code: 404 - The model `llama-3.1-8b-instant` does not exist"),
        TimeoutError("Request timed out."),
        RuntimeError("GROQ_API_KEY not set"),
        ConnectionError("provider unreachable"),
    ],
)
def test_llm_failure_falls_back_to_unknown(monkeypatch, exc):
    monkeypatch.setattr(error_classifier, "generate_text", _raise(exc))

    out = error_classifier_node(_state())  # must not raise

    assert out["error_class"] == "UNKNOWN_ERROR"
    assert out["retry_count"] == 1
    entry = out["trace"][-1]
    assert entry["source"] == "llm_failed"
    assert type(exc).__name__ in entry["classifier_failure"]


def test_fallback_ends_the_correction_loop(monkeypatch):
    # UNKNOWN_ERROR must route to END -- a finished failure, not a crash and
    # not a blind retry.
    monkeypatch.setattr(error_classifier, "generate_text", _raise(RuntimeError("boom")))
    out = error_classifier_node(_state())
    assert route_after_classification({**_state(), **out}) == "end"


@pytest.mark.parametrize("raw", ["", "I think it's a schema problem.", "schema_error", "SCHEMA_ERROR."])
def test_unrecognised_response_recorded_as_unknown(monkeypatch, raw):
    monkeypatch.setattr(error_classifier, "generate_text", lambda **kw: raw)
    out = error_classifier_node(_state())
    assert out["error_class"] == "UNKNOWN_ERROR"
    assert out["trace"][-1]["source"] == "llm_invalid"


@pytest.mark.parametrize("raw", ["schema_error", "SCHEMA_ERROR.", "nonsense"])
def test_unrecognised_response_routes_exactly_as_before(monkeypatch, raw):
    # Before the change, a non-exact response was stored verbatim and routed
    # to END. Mapping it to UNKNOWN_ERROR must route identically -- this fix
    # hardens the node, it must not start retrying anything that ended.
    monkeypatch.setattr(error_classifier, "generate_text", lambda **kw: raw)
    out = error_classifier_node(_state())
    assert route_after_classification({**_state(), **out}) == "end"
    assert route_after_classification({**_state(), "error_class": raw}) == "end"


@pytest.mark.parametrize("label", ["SCHEMA_ERROR", "SYNTAX_ERROR", "LOGIC_ERROR", "UNKNOWN_ERROR"])
def test_valid_response_passes_through_unchanged(monkeypatch, label):
    monkeypatch.setattr(error_classifier, "generate_text", lambda **kw: f"  {label}\n")
    out = error_classifier_node(_state())
    assert out["error_class"] == label
    assert out["trace"][-1]["source"] == "llm"
    assert "classifier_failure" not in out["trace"][-1]


def test_rule_based_path_never_calls_the_llm(monkeypatch):
    monkeypatch.setattr(error_classifier, "generate_text", _raise(AssertionError("LLM must not be called")))
    out = error_classifier_node(_state(execution_error="no such column: wins"))
    assert out["error_class"] == "SCHEMA_ERROR"
    assert out["trace"][-1]["source"] == "rule_based"


def test_quota_exhaustion_is_not_swallowed(monkeypatch):
    # Every other failure falls back to UNKNOWN_ERROR. Exhausted credit must
    # not: it invalidates the run, and absorbing it here would score the
    # question as wrong and let the run continue recording more of them.
    from src.llm_providers import QuotaExhaustedError
    monkeypatch.setattr(error_classifier, "generate_text", _raise(QuotaExhaustedError("no credit")))
    with pytest.raises(QuotaExhaustedError):
        error_classifier_node(_state())
