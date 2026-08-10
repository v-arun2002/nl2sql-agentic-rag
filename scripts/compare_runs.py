import csv

def load(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))

base = load("eval/results_500_baseline.csv")[:50]
new = load("eval/results.csv")

b_correct = sum(1 for r in base if r["correct"] == "True")
n_correct = sum(1 for r in new if r["correct"] == "True")

print(f"Baseline (first 50): {b_correct}/50 = {b_correct/50:.1%}")
print(f"New prompt (same 50): {n_correct}/50 = {n_correct/50:.1%}")
print(f"Delta: {n_correct - b_correct:+d} questions\n")

# Which specific questions flipped, and which way
b_by_id = {r["question_id"]: r["correct"] == "True" for r in base}
fixed, broken = [], []
for r in new:
    qid = r["question_id"]
    if qid not in b_by_id:
        continue
    now = r["correct"] == "True"
    if now and not b_by_id[qid]:
        fixed.append(r)
    elif not now and b_by_id[qid]:
        broken.append(r)

print(f"FIXED by the new prompt: {len(fixed)}")
for r in fixed:
    print(f"  [{r['db_id']}] {r['question'][:80]}")

print(f"\nBROKEN by the new prompt: {len(broken)}")
for r in broken:
    print(f"  [{r['db_id']}] {r['question'][:80]}")