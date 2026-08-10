from src.graph import build_graph
from src.agents.state import initial_state

graph = build_graph()

state = initial_state(
    db_id="debit_card_specializing",
    question="In 2012, who had the least consumption in LAM?",
    max_retries=3,
)

result = graph.invoke(state)
print(result)