"""
Fixed regression set: a small, hand-picked subset of queries re-run on every
commit so a prompt/agent change that regresses accuracy is caught in CI
before it reaches the benchmark. This is the "changed X, accuracy moved from
Y to Z" artifact that's rare in student portfolios and worth highlighting.

Checks CORRECTNESS (execution match against gold SQL), not just "didn't
crash" -- a query that runs without error but returns the wrong answer
should fail this test, not pass it.

Grow this list over time by pulling in specific BIRD-SQL examples that
previously failed and were fixed -- that way regressions on hard-won fixes
get caught automatically.
"""

import pytest

from eval.metrics import execution_match
from eval.run_benchmark import get_gold_result
from src.agents.state import initial_state
from src.config import settings
from src.graph import build_graph

# Real example verified against an actual exported dev.json entry
# (question_id 1471), not a guessed placeholder.
REGRESSION_SET = [
    {
        "question_id": 1471,
        "db_id": "debit_card_specializing",
        "question": "What is the ratio of customers who pay in EUR against customers who pay in CZK?",
        "gold_sql": (
            "SELECT CAST(SUM(IIF(Currency = 'EUR', 1, 0)) AS FLOAT) / "
            "SUM(IIF(Currency = 'CZK', 1, 0)) AS ratio FROM customers"
        ),
    },
    # Add more {question_id, db_id, question, gold_sql} entries here as the
    # regression set grows -- pull them straight from dev.json to guarantee
    # they're real, not guessed.
]


@pytest.fixture(scope="module")
def graph():
    return build_graph()


@pytest.mark.parametrize("case", REGRESSION_SET, ids=lambda c: str(c["question_id"]))
def test_regression_query_correct(graph, case):
    state = initial_state(case["db_id"], case["question"], max_retries=settings.max_retries)
    result = graph.invoke(state)

    assert result["success"] is True, f"Query failed to execute: {result.get('execution_error')}"

    gold_result = get_gold_result(case["db_id"], case["gold_sql"])
    assert execution_match(result.get("execution_result"), gold_result), (
        f"Predicted SQL ran but gave the wrong answer.\n"
        f"Predicted: {result.get('sql_query')}\n"
        f"Gold:      {case['gold_sql']}"
    )