"""
One-off smoke test: runs a single real BIRD-SQL question through the full
agent pipeline and prints what happened at every step. Expect this to
surface real bugs on the first few tries -- that IS the useful part of
this step, not a sign something's broken.
"""

from src.graph import build_graph
from src.agents.state import initial_state

graph = build_graph()

state = initial_state(
    db_id="debit_card_specializing",
    question="What is the ratio of customers who pay in EUR against customers who pay in CZK?",
    max_retries=3,
)

result = graph.invoke(state)

print("=" * 60)
print("GENERATED SQL:")
print(result.get("sql_query"))
print()
print("SUCCESS:", result.get("success"))
print("RESULT:", result.get("execution_result"))
print("RETRIES:", result.get("retry_count"))
print()
print("FULL TRACE (every agent hop):")
for step in result.get("trace", []):
    print(" -", step)
print("=" * 60)
print()
print("For comparison, the GOLD SQL is:")
print("SELECT CAST(SUM(IIF(Currency = 'EUR', 1, 0)) AS FLOAT) / SUM(IIF(Currency = 'CZK', 1, 0)) AS ratio FROM customers")