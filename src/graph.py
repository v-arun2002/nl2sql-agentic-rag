"""
Builds the LangGraph state machine:

    schema_retriever -> query_planner -> sql_generator -> executor
                                                              |
                                          success ------------+------- failure
                                             |                          |
                                            END                 classify_error
                                                                        |
                                        +---------------+---------------+
                                        |               |               |
                                  SCHEMA_ERROR    SYNTAX_ERROR     LOGIC_ERROR
                                        |               |               |
                                schema_retriever  sql_generator   query_planner
                                        |               |               |
                                        +---------------+---------------+
                                                        |
                                                 (loops back to executor,
                                                  bounded by max_retries)

The routing logic (route_after_classification) is the actual differentiator
of this project: most NL2SQL correction loops just re-prompt with the raw
error message. Routing to the specific agent responsible for that error
class is a more deliberate design and is what should come up in interviews.
"""

from langgraph.graph import StateGraph, END

from src.agents.state import AgentState
from src.agents.schema_retriever import schema_retriever_node
from src.agents.query_planner import query_planner_node
from src.agents.sql_generator import sql_generator_node
from src.agents.error_classifier import error_classifier_node
from src.db.executor import executor_node


def route_after_execution(state: AgentState) -> str:
    if state["success"]:
        return "end"
    if state["retry_count"] >= state["max_retries"]:
        return "end"
    return "classify_error"


def route_after_classification(state: AgentState) -> str:
    error_class = state["error_class"]
    if state["retry_count"] >= state["max_retries"]:
        return "end"
    if error_class == "SCHEMA_ERROR":
        return "schema_retriever"
    if error_class == "SYNTAX_ERROR":
        return "sql_generator"
    if error_class == "LOGIC_ERROR":
        return "query_planner"
    return "end"  # UNKNOWN_ERROR / TIMEOUT_ERROR: not worth retrying blindly


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("schema_retriever", schema_retriever_node)
    graph.add_node("query_planner", query_planner_node)
    graph.add_node("sql_generator", sql_generator_node)
    graph.add_node("executor", executor_node)
    graph.add_node("classify_error", error_classifier_node)

    graph.set_entry_point("schema_retriever")
    graph.add_edge("schema_retriever", "query_planner")
    graph.add_edge("query_planner", "sql_generator")
    graph.add_edge("sql_generator", "executor")

    graph.add_conditional_edges(
        "executor",
        route_after_execution,
        {"end": END, "classify_error": "classify_error"},
    )

    graph.add_conditional_edges(
        "classify_error",
        route_after_classification,
        {
            "schema_retriever": "schema_retriever",
            "sql_generator": "sql_generator",
            "query_planner": "query_planner",
            "end": END,
        },
    )

    return graph.compile()
