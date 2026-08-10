"""
Runs the full agent graph against BIRD-SQL's Mini-Dev set, computes overall
+ per-difficulty + per-database execution accuracy, and writes a per-example
failure taxonomy to eval/results.csv.

BIRD-SQL's confirmed dev.json fields (verified against a real exported
example): question_id, db_id, question, evidence, SQL (capital -- not
"query"), and difficulty. By default this runs WITHOUT feeding `evidence`
into the agents (see src/config.py's include_evidence_in_prompts) -- a
harder test of whether the pipeline works from schema alone.

Results checkpoint to disk every 10 questions, and the run RESUMES from
whatever is already in eval/results.csv. To start fresh, delete that file
first. IMPORTANT: don't change models/providers mid-run -- resumed rows and
new rows would then describe two different systems, making the combined
number meaningless.
"""

import csv
import json
import os
import sqlite3
import time

from eval.metrics import execution_match
from src.agents.state import initial_state
from src.config import settings
from src.graph import build_graph

RESULTS_PATH = "eval/results.csv"


def load_dev_set(path: str) -> list:
    # Explicit utf-8: the dataset contains non-ASCII characters, and Windows
    # would otherwise default to cp1252 and fail on them.
    with open(f"{path}/dev.json", encoding="utf-8") as f:
        return json.load(f)


def get_gold_result(db_id: str, gold_sql: str) -> list:
    db_path = f"{settings.benchmark_data_path}/dev_databases/{db_id}/{db_id}.sqlite"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(gold_sql)
    result = cursor.fetchall()
    conn.close()
    return result


def is_correct_row(row: dict) -> bool:
    """
    Rows loaded from CSV store booleans as the strings "True"/"False", while
    freshly-computed rows hold real booleans. Normalising here avoids
    int("True") blowing up when summarising a resumed run.
    """
    return str(row.get("correct")) == "True"


def write_results(results: list) -> None:
    if not results:
        return
    # encoding + errors="replace": dataset text includes non-ASCII characters
    # that Windows' default cp1252 codec cannot encode, which would otherwise
    # crash the write AFTER a full expensive run.
    with open(RESULTS_PATH, "w", newline="", encoding="utf-8", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)


def print_summary(results: list, total_elapsed: float, questions_run_now: int) -> None:
    if not results:
        return

    correct = sum(1 for r in results if is_correct_row(r))
    print(f"\nOverall execution accuracy: {correct / len(results):.2%} ({correct}/{len(results)})")

    for key, label in (("difficulty", "By difficulty"), ("db_id", "By database")):
        groups: dict = {}
        for r in results:
            g = groups.setdefault(r[key], [0, 0])
            g[1] += 1
            g[0] += int(is_correct_row(r))
        print(f"\n{label}:")
        for name, (c, t) in sorted(groups.items()):
            print(f"  {name:>26}: {c / t:.2%} ({c}/{t})")

    if questions_run_now:
        avg = total_elapsed / questions_run_now
        print(f"\nThis session: {questions_run_now} questions in {total_elapsed / 60:.1f} min ({avg:.1f}s/question)")


def run_benchmark(limit: int | None = None) -> None:
    graph = build_graph()
    examples = load_dev_set(settings.benchmark_data_path)
    if limit:
        examples = examples[:limit]
        print(f"Running a LIMITED slice: {limit} of the full set.\n")
    else:
        print(f"Running the FULL set: {len(examples)} examples.\n")

    # Resume support: if results.csv already holds rows, skip the questions
    # they cover and append from there. Lets a long run survive interruption
    # across sessions instead of restarting from zero.
    results: list = []
    start_index = 0
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, encoding="utf-8", errors="replace") as f:
            existing = list(csv.DictReader(f))
        if existing:
            results = existing
            start_index = len(existing)
            print(f"Resuming: {start_index} questions already recorded, continuing from #{start_index + 1}.\n")

    correct = sum(1 for r in results if is_correct_row(r))
    start_time = time.monotonic()
    questions_run_now = 0

    for i, ex in enumerate(examples):
        if i < start_index:
            continue

        question_id = ex.get("question_id", i)
        db_id = ex["db_id"]
        question = ex["question"]
        gold_sql = ex["SQL"]
        evidence = ex.get("evidence")
        difficulty = ex.get("difficulty", "unknown")

        question_start = time.monotonic()
        state = initial_state(db_id, question, evidence=evidence, max_retries=settings.max_retries)

        fatal_error = None
        try:
            final_state = graph.invoke(state)
            gold_result = get_gold_result(db_id, gold_sql)
            row_correct = execution_match(final_state.get("execution_result"), gold_result)
        except Exception as e:
            fatal_error = f"{type(e).__name__}: {e}"
            final_state = {"sql_query": None, "trace": [{"node": "benchmark", "fatal_error": fatal_error}], "retry_count": 0}
            row_correct = False
        question_elapsed = time.monotonic() - question_start

        correct += int(row_correct)
        questions_run_now += 1

        error_classes_hit = [t.get("error_class") for t in final_state.get("trace", []) if "error_class" in t]
        schema_context = final_state.get("schema_context") or ""

        results.append({
            "index": i,
            "question_id": question_id,
            "db_id": db_id,
            "question": question,
            "evidence": evidence,
            "gold_sql": gold_sql,
            "predicted_sql": final_state.get("sql_query"),
            "correct": row_correct,
            "difficulty": difficulty,
            "retries": final_state.get("retry_count", 0),
            "error_classes_hit": ";".join(error_classes_hit),
            "fatal_error": fatal_error or "",
            "schema_context_chars": len(schema_context),
            "seconds": round(question_elapsed, 1),
        })

        # Checkpoint every 10 questions so an interruption costs at most the
        # last few questions rather than the entire run.
        if (i + 1) % 10 == 0 or limit:
            write_results(results)
            elapsed_now = time.monotonic() - start_time
            rate = elapsed_now / questions_run_now
            remaining = rate * (len(examples) - i - 1)
            print(
                f"  ...{i + 1}/{len(examples)} | {question_elapsed:.1f}s this one | "
                f"accuracy {correct}/{len(results)} | ~{remaining / 60:.0f} min left"
            )

    write_results(results)
    print_summary(results, time.monotonic() - start_time, questions_run_now)
    print(f"\nPer-example results written to {RESULTS_PATH}")


if __name__ == "__main__":
    limit_env = os.getenv("BENCHMARK_LIMIT")
    run_benchmark(limit=int(limit_env) if limit_env else None)