"""
Execution Accuracy (EX) -- BIRD-SQL's own accuracy metric: compares result
sets ignoring row order (since equivalent SQL can return rows in different
order). Using the same metric BIRD-SQL itself uses means results here are
directly comparable to published numbers on the same benchmark.
"""

from typing import Optional, Sequence


def execution_match(predicted_result: Optional[Sequence], gold_result: Optional[Sequence]) -> bool:
    if predicted_result is None or gold_result is None:
        return False
    try:
        return sorted(map(tuple, predicted_result)) == sorted(map(tuple, gold_result))
    except TypeError:
        # Unhashable/uncomparable cell types (rare) -- fall back to strict equality.
        return list(predicted_result) == list(gold_result)
