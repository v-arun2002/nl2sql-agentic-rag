import csv
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "california_schools"

with open("eval/results.csv", encoding="utf-8", errors="replace") as f:
    rows = [r for r in csv.DictReader(f) if r["db_id"] == db]

incorrect = [r for r in rows if r["correct"] == "False"]
print(f"{db}: {len(incorrect)} incorrect of {len(rows)}\n")

for r in incorrect[:6]:
    print("=" * 60)
    print("Q:        ", r["question"])
    print("Evidence: ", (r["evidence"] or "(none)")[:200])
    print("Gold:     ", r["gold_sql"][:300])
    print("Predicted:", (r["predicted_sql"] or "(none)")[:300])
    print("Retries:  ", r["retries"], "| Errors:", r["error_classes_hit"])
    print()
    