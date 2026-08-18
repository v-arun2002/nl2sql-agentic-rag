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

# These call live providers, so they skip rather than fail when the relevant
# keys aren't configured -- an absent key is "not run here", not a regression.
# Only the providers actually routed to are required: swapping a role to a
# different provider changes which key this needs, with no edit here.
_PROVIDER_KEYS = {
    "openai": settings.openai_api_key,
    "groq": settings.groq_api_key,
    "gemini": settings.gemini_api_key,
}
_REQUIRED = {
    settings.planner_provider,
    settings.generator_provider,
    settings.classifier_provider,
}
_MISSING = sorted(p for p in _REQUIRED if not _PROVIDER_KEYS.get(p))

pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=(
        f"No API key for configured provider(s): {', '.join(_MISSING)}. "
        "Set them in .env locally, or as repository secrets in CI."
    ),
)

# Cases are pulled verbatim from dev.json, and restricted to databases the repo
# actually commits (.gitignore ships 6 of BIRD's 11). An earlier version pinned
# this to debit_card_specializing, which is not one of them -- it passed locally,
# where all 11 are present, and failed in CI on every push.
#
# Both cases below are ones the 500-question baseline already answers correctly
# with retries: 0, so a failure here means a real regression rather than a
# borderline question flipping.
REGRESSION_SET = [
    {
        "question_id": 717,
        "db_id": "superhero",
        "question": "Please list all the superpowers of 3-D Man.",
        "gold_sql": (
            "SELECT T3.power_name FROM superhero AS T1 "
            "INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id "
            "INNER JOIN superpower AS T3 ON T2.power_id = T3.id "
            "WHERE T1.superhero_name = '3-D Man'"
        ),
    },
    {
        "question_id": 719,
        "db_id": "superhero",
        "question": (
            'Among the superheroes with the super power of "Super Strength", '
            "how many of them have a height of over 200cm?"
        ),
        "gold_sql": (
            "SELECT COUNT(T1.id) FROM superhero AS T1 "
            "INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id "
            "INNER JOIN superpower AS T3 ON T2.power_id = T3.id "
            "WHERE T3.power_name = 'Super Strength' AND T1.height_cm > 200"
        ),
    },
    # Add more {question_id, db_id, question, gold_sql} entries here as the
    # regression set grows -- pull them straight from dev.json to guarantee
    # they're real, not guessed, and keep to the 6 committed databases.
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