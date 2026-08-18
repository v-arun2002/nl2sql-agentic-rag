"""
Unit tests for the pure logic: no API keys, no network, no BIRD databases.

These exist because the expensive bugs in this project's history were all in
code that is trivially testable in isolation -- an empty vector index, SQL that
executed as a silent no-op, a trailing space in a db_id. The end-to-end
regression test in test_regression.py catches accuracy changes but needs live
providers; everything here runs on every push in a couple of seconds.

Where a test encodes a previously-shipped bug, the docstring says so.
"""

import sqlite3

import pytest

from eval.metrics import execution_match
from src.agents.state import initial_state
from src.db.executor import executor_node
from src.graph import route_after_classification, route_after_execution
from ui.sql_highlight import highlight_sql


# --------------------------------------------------------------------------
# eval/metrics.py -- execution_match
# --------------------------------------------------------------------------

class TestExecutionMatch:
    def test_identical_results_match(self):
        assert execution_match([(1, "a")], [(1, "a")]) is True

    def test_row_order_is_ignored(self):
        """BIRD's EX metric is order-insensitive: equivalent SQL may order rows
        differently, and that is not a wrong answer."""
        assert execution_match([(2,), (1,)], [(1,), (2,)]) is True

    def test_different_values_do_not_match(self):
        assert execution_match([(1,)], [(2,)]) is False

    def test_different_row_counts_do_not_match(self):
        assert execution_match([(1,), (2,)], [(1,)]) is False

    def test_lists_and_tuples_compare_equal(self):
        """sqlite3 returns tuples; gold fixtures are sometimes lists."""
        assert execution_match([[1, "a"]], [(1, "a")]) is True

    def test_two_empty_results_match(self):
        assert execution_match([], []) is True

    @pytest.mark.parametrize(
        "predicted,gold",
        [(None, [(1,)]), ([(1,)], None), (None, None)],
    )
    def test_none_never_matches(self, predicted, gold):
        """A failed query yields None. That must never score as correct --
        including None vs None, which would otherwise reward two failures."""
        assert execution_match(predicted, gold) is False

    def test_uncomparable_cell_types_fall_back_to_strict_equality(self):
        """sorted() raises TypeError comparing int with str; the fallback path
        must still return a bool rather than propagating."""
        mixed = [(1,), ("a",)]
        assert execution_match(mixed, list(mixed)) is True
        assert execution_match(mixed, [("a",), (1,)]) is False


# --------------------------------------------------------------------------
# src/db/executor.py -- the empty-SQL guard
# --------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """A minimal SQLite database laid out the way executor_node expects."""
    db_id = "testdb"
    db_dir = tmp_path / "dev_databases" / db_id
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(db_dir / f"{db_id}.sqlite")
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b")])
    conn.commit()
    conn.close()

    from src.config import settings
    monkeypatch.setattr(settings, "benchmark_data_path", str(tmp_path))
    return db_id


def _state(db_id, sql):
    return {"db_id": db_id, "sql_query": sql, "trace": []}


class TestExecutorEmptySQLGuard:
    @pytest.mark.parametrize("empty", [None, "", "   ", "\n\t "])
    def test_empty_sql_is_an_explicit_failure(self, empty, temp_db):
        """Regression: empty SQL is a silent no-op in SQLite -- zero rows, no
        exception -- which the pipeline previously recorded as SUCCESS. A
        reasoning model that spent its whole token budget thinking returned '',
        and the run scored it as a passing query."""
        out = executor_node(_state(temp_db, empty))

        assert out["success"] is False
        assert out["execution_result"] is None
        assert out["execution_error"]
        assert out["trace"][-1] == {
            "node": "executor",
            "status": "failed",
            "error": "empty_sql",
        }

    def test_empty_sql_never_opens_the_database(self, monkeypatch):
        """The guard must return before connecting, so it holds even when the
        database is missing entirely."""
        def explode(*a, **k):
            raise AssertionError("sqlite3.connect must not be called for empty SQL")

        monkeypatch.setattr(sqlite3, "connect", explode)
        assert executor_node(_state("nonexistent", ""))["success"] is False

    def test_valid_sql_succeeds_and_records_row_count(self, temp_db):
        out = executor_node(_state(temp_db, "SELECT id, name FROM t ORDER BY id"))

        assert out["success"] is True
        assert out["execution_result"] == [(1, "a"), (2, "b")]
        assert out["execution_error"] is None
        assert out["trace"][-1]["row_count"] == 2

    def test_broken_sql_fails_with_the_sqlite_error(self, temp_db):
        out = executor_node(_state(temp_db, "SELECT * FROM no_such_table"))

        assert out["success"] is False
        assert "no_such_table" in out["execution_error"]

    def test_trace_is_appended_not_replaced(self, temp_db):
        state = {"db_id": temp_db, "sql_query": "SELECT 1", "trace": [{"node": "planner"}]}
        trace = executor_node(state)["trace"]

        assert len(trace) == 2 and trace[0] == {"node": "planner"}


# --------------------------------------------------------------------------
# src/graph.py -- correction-loop routing
# --------------------------------------------------------------------------

class TestRouteAfterExecution:
    def test_success_ends_the_run(self):
        assert route_after_execution({"success": True, "retry_count": 0, "max_retries": 3}) == "end"

    def test_failure_under_the_retry_cap_goes_to_the_classifier(self):
        assert route_after_execution({"success": False, "retry_count": 0, "max_retries": 3}) == "classify_error"

    def test_failure_at_the_retry_cap_ends_the_run(self):
        assert route_after_execution({"success": False, "retry_count": 3, "max_retries": 3}) == "end"

    def test_success_wins_even_at_the_retry_cap(self):
        assert route_after_execution({"success": True, "retry_count": 3, "max_retries": 3}) == "end"


class TestRouteAfterClassification:
    @pytest.mark.parametrize(
        "error_class,expected",
        [
            ("SCHEMA_ERROR", "schema_retriever"),
            ("SYNTAX_ERROR", "sql_generator"),
            ("LOGIC_ERROR", "query_planner"),
        ],
    )
    def test_each_error_class_routes_to_the_agent_responsible(self, error_class, expected):
        """The project's actual differentiator: a wrong table goes back to the
        retriever, bad syntax to the generator, faulty logic to the planner --
        rather than blindly re-prompting with the raw error."""
        state = {"error_class": error_class, "retry_count": 0, "max_retries": 3}
        assert route_after_classification(state) == expected

    @pytest.mark.parametrize("error_class", ["UNKNOWN_ERROR", "TIMEOUT_ERROR", None])
    def test_unactionable_classes_end_rather_than_retry_blindly(self, error_class):
        state = {"error_class": error_class, "retry_count": 0, "max_retries": 3}
        assert route_after_classification(state) == "end"

    def test_retry_cap_beats_an_actionable_error_class(self):
        """The cap is checked first, so a SCHEMA_ERROR at the limit still ends
        the run instead of looping forever."""
        state = {"error_class": "SCHEMA_ERROR", "retry_count": 3, "max_retries": 3}
        assert route_after_classification(state) == "end"

    def test_zero_max_retries_disables_the_correction_loop(self):
        state = {"error_class": "SYNTAX_ERROR", "retry_count": 0, "max_retries": 0}
        assert route_after_classification(state) == "end"


# --------------------------------------------------------------------------
# src/agents/state.py -- initial_state input sanitisation
# --------------------------------------------------------------------------

class TestInitialState:
    def test_trailing_whitespace_is_stripped_from_db_id(self):
        """Regression: 'superhero ' produced the Chroma collection name
        'schema_superhero ', which fails Chroma's name validation three layers
        down and surfaced as a vector-store error rather than a bad input."""
        assert initial_state("superhero ", "q")["db_id"] == "superhero"

    @pytest.mark.parametrize("raw", ["  superhero", "superhero\n", "\tsuperhero\t"])
    def test_whitespace_is_stripped_from_either_side(self, raw):
        assert initial_state(raw, "q")["db_id"] == "superhero"

    def test_question_is_stripped(self):
        assert initial_state("db", "  How many schools?  ")["question"] == "How many schools?"

    def test_evidence_is_stripped_when_present(self):
        assert initial_state("db", "q", evidence="  hint  ")["evidence"] == "hint"

    def test_absent_evidence_stays_none(self):
        """Must not become an empty string -- prompt assembly branches on None."""
        assert initial_state("db", "q")["evidence"] is None

    def test_empty_evidence_stays_none(self):
        assert initial_state("db", "q", evidence="")["evidence"] is None

    def test_max_retries_is_carried_through(self):
        assert initial_state("db", "q", max_retries=7)["max_retries"] == 7

    def test_fresh_state_starts_unsuccessful_with_an_empty_trace(self):
        state = initial_state("db", "q")
        assert state["retry_count"] == 0
        assert state["trace"] == []
        assert state["success"] is False
        assert state["sql_query"] is None


# --------------------------------------------------------------------------
# ui/sql_highlight.py -- highlight_sql
# --------------------------------------------------------------------------

class TestHighlightSQL:
    def test_keywords_are_wrapped(self):
        assert '<span class="sql-kw">SELECT</span>' in highlight_sql("SELECT 1")

    def test_keyword_matching_is_case_insensitive(self):
        assert '<span class="sql-kw">select</span>' in highlight_sql("select 1")

    def test_numbers_are_wrapped(self):
        assert '<span class="sql-num">200</span>' in highlight_sql("WHERE height_cm > 200")

    def test_string_literals_are_wrapped_with_quotes_intact(self):
        """quote=False in html.escape is load-bearing: escaping single quotes to
        &#x27; would stop the string-literal pattern from ever matching."""
        out = highlight_sql("WHERE name = 'Alameda'")
        assert "<span class=\"sql-str\">'Alameda'</span>" in out

    def test_comments_are_wrapped(self):
        assert '<span class="sql-comment">-- note</span>' in highlight_sql("SELECT 1 -- note")

    def test_angle_brackets_are_escaped(self):
        out = highlight_sql("WHERE a < 5 AND b > 2")
        assert "&lt;" in out and "&gt;" in out

    def test_html_in_sql_cannot_inject_markup(self):
        """The result is rendered with unsafe_allow_html, so anything that
        reaches it must already be escaped."""
        out = highlight_sql("SELECT '<script>alert(1)</script>'")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_ampersand_is_escaped_once(self):
        assert highlight_sql("a & b").count("&amp;") == 1

    @pytest.mark.parametrize("empty", [None, ""])
    def test_empty_input_returns_empty_string(self, empty):
        """The generator can return None; the UI must render blank, not crash."""
        assert highlight_sql(empty) == ""

    def test_substrings_of_words_are_not_highlighted(self):
        """\\b anchors keep 'as' inside 'last_name' from being marked a keyword."""
        assert "sql-kw" not in highlight_sql("SELECT last_name").split("last_name")[1]
