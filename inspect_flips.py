import csv

def load(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))

base = {r["question_id"]: r for r in load("eval/results_500_baseline.csv")[:50]}
new = {r["question_id"]: r for r in load("eval/results.csv")}

for qid, n in new.items():
    b = base.get(qid)
    if not b:
        continue
    if b["correct"] == "True" and n["correct"] == "False":
        print("=" * 70)
        print("BROKE:", n["question"][:100])
        print("\nGOLD:\n", n["gold_sql"][:400])
        print("\nOLD PROMPT (correct):\n", b["predicted_sql"][:400])
        print("\nNEW PROMPT (wrong):\n", n["predicted_sql"][:400])
        print()