"""
The benchmark harness stops the whole run on exhausted credit.

Free and offline: the graph, dev set and gold results are replaced, so the
real run_benchmark() loop -- checkpointing, resume, scoring -- runs with no
LLM, database or network. The case under test is the night the OpenAI balance
ran out at question 93: the harness kept going, scoring each remaining
question as wrong after ~143s of retries.
"""

import csv

import pytest

from eval import run_benchmark as rb
from src.llm_providers import QuotaExhaustedError

N = 6


class FakeGraph:
    """Answers correctly, except it raises `fail_with` on the question indices in `fail_at`."""

    def __init__(self, fail_at=(), fail_with=None):
        self.fail_at, self.fail_with, self.invoked = set(fail_at), fail_with, []

    def invoke(self, state):
        idx = int(state["question"].split("#")[1])
        self.invoked.append(idx)
        if idx in self.fail_at:
            raise self.fail_with
        return {"sql_query": "SELECT 1", "execution_result": [(1,)], "trace": [], "retry_count": 0, "success": True}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    results = tmp_path / "results.csv"
    monkeypatch.setattr(rb, "RESULTS_PATH", str(results))
    monkeypatch.setattr(rb, "METADATA_PATH", str(tmp_path / "meta.json"))
    monkeypatch.setattr(rb, "load_dev_set", lambda path: [
        {"question_id": 100 + i, "db_id": "db", "question": f"question #{i}", "SQL": "SELECT 1",
         "evidence": "", "difficulty": "simple"}
        for i in range(N)
    ])
    monkeypatch.setattr(rb, "get_gold_result", lambda db_id, sql: [(1,)])

    def run(graph):
        monkeypatch.setattr(rb, "build_graph", lambda: graph)
        rb.run_benchmark()

    def rows():
        if not results.exists():
            return []
        with open(results, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    return run, rows


def test_quota_exhaustion_stops_the_run(harness):
    run, rows = harness
    graph = FakeGraph(fail_at={3}, fail_with=QuotaExhaustedError("no credit remaining"))

    with pytest.raises(SystemExit) as exc:
        run(graph)

    assert exc.value.code == rb.EXIT_QUOTA_EXHAUSTED
    assert graph.invoked == [0, 1, 2, 3]  # stopped at once -- 4 and 5 never attempted
    saved = rows()
    assert [r["question_id"] for r in saved] == ["100", "101", "102"]  # the failing question is NOT recorded
    assert all(r["correct"] == "True" for r in saved)


def test_resume_after_topup_reruns_the_stopped_question(harness):
    run, rows = harness
    with pytest.raises(SystemExit):
        run(FakeGraph(fail_at={3}, fail_with=QuotaExhaustedError("no credit remaining")))

    resumed = FakeGraph()  # credit restored
    run(resumed)

    assert resumed.invoked == [3, 4, 5]  # picks up exactly where it stopped
    saved = rows()
    assert [r["question_id"] for r in saved] == [str(100 + i) for i in range(N)]
    assert all(r["correct"] == "True" for r in saved) and all(not r["fatal_error"] for r in saved)


def test_quota_message_without_the_type_still_stops(harness):
    # Detection also matches the raw billing code, in case an error reaches
    # the harness without passing through _with_backoff's conversion.
    run, rows = harness
    raw = RuntimeError("Error code: 429 - {'error': {'code': 'credit_balance_exhausted'}}")
    with pytest.raises(SystemExit):
        run(FakeGraph(fail_at={1}, fail_with=raw))
    assert len(rows()) == 1


def test_an_ordinary_crash_is_still_a_counted_failure(harness):
    # Unchanged behaviour: any other exception is recorded as a fatal row,
    # scored incorrect, and the run carries on.
    run, rows = harness
    graph = FakeGraph(fail_at={2}, fail_with=RuntimeError("something else broke"))
    run(graph)
    saved = rows()
    assert len(saved) == N
    assert graph.invoked == list(range(N))
    assert saved[2]["correct"] == "False" and "something else broke" in saved[2]["fatal_error"]
