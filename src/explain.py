"""
Plain-language summary of a query result, for the MCP explain tool.

NOT one of the five pipeline agents. The pipeline never explains its results;
this is a new capability that sits after it. Nothing here affects SQL
generation or the benchmark numbers.

The prompt's job is to stop the model from being helpful in the wrong way.
The tempting failure is enrichment: given `constructorId = 6`, a model that
"knows" Formula 1 will happily answer "Ferrari". That may even be right, but
it is not in the result -- and this tool's output gets read as a description
of what the database said. So the rules are: describe only what the rows
contain, call an ID an ID, and say plainly when the result is empty,
truncated, or does not actually answer the question asked.
"""

import json

from src.config import settings
from src.llm_providers import generate_text

# Rows sent to the model. Beyond this the summary is of a sample, and the
# prompt says so -- the full result is still what execute_query returned.
MAX_ROWS_IN_PROMPT = 50

_SYSTEM = """You summarise the result of a SQL query for a non-technical reader.

Rules, in priority order:
1. State ONLY what the result rows contain. Never add facts from your own
   knowledge. If a column is an ID (e.g. constructorId = 6), report the ID;
   do not guess what entity it refers to.
2. If the result does not actually answer the question (for example it
   returns an ID where a name was asked for), say so in one sentence.
3. If there are zero rows, say no rows matched. Do not speculate why.
4. If the result is marked truncated or sampled, say the summary covers only
   the rows shown.
5. Two to four sentences. Plain language. No SQL, no markdown."""


def explain(question: str, sql: str, columns: list, rows: list, truncated: bool = False) -> dict:
    sample = rows[:MAX_ROWS_IN_PROMPT]
    sampled = len(rows) > len(sample)

    user = (
        f"Question asked: {question or '(not provided)'}\n"
        f"SQL that was run: {sql or '(not provided)'}\n"
        f"Columns: {json.dumps(columns)}\n"
        f"Rows ({len(rows)} returned"
        f"{'; showing first ' + str(len(sample)) if sampled else ''}"
        f"{'; RESULT WAS TRUNCATED by the row limit' if truncated else ''}):\n"
        f"{json.dumps(sample, default=str)}"
    )

    summary = generate_text(
        provider=settings.explainer_provider,
        model=settings.explainer_model,
        system_prompt=_SYSTEM,
        user_prompt=user,
        max_output_tokens=300,
    ).strip()

    return {
        "summary": summary,
        "rows_considered": len(sample),
        "covers_full_result": not (sampled or truncated),
        "model": f"{settings.explainer_provider}/{settings.explainer_model}",
    }
