import csv

with open("eval/results.csv", encoding="utf-8", errors="replace") as f:
    rows = list(csv.DictReader(f))

incorrect = [r for r in rows if r["correct"] == "False"]
no_sql = [r for r in incorrect if not r["predicted_sql"]]

print(f"{len(incorrect)} incorrect out of {len(rows)}")
print(f"{len(no_sql)} produced NO SQL at all")
sizes = [int(r["schema_context_chars"]) for r in rows if r.get("schema_context_chars")]
if sizes:
    print(f"Schema context size: min={min(sizes)} max={max(sizes)} avg={sum(sizes)//len(sizes)} chars\n")

for r in incorrect[:6]:
    print("=" * 60)
    print("Question:  ", r["question"])
    print("Difficulty:", r["difficulty"], "| schema chars:", r.get("schema_context_chars"))
    print("Gold SQL:  ", r["gold_sql"])
    print("Predicted: ", r["predicted_sql"] or "(none produced)")
    print("Error classes:", r["error_classes_hit"], "| Fatal:", r.get("fatal_error") or "(none)")
    print()