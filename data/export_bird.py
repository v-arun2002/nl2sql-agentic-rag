from datasets import load_dataset
import json, os

ds = load_dataset('birdsql/bird_mini_dev')
examples = ds['mini_dev_sqlite']

os.makedirs('data/bird-mini-dev', exist_ok=True)
with open('data/bird-mini-dev/dev.json', 'w') as f:
    json.dump(list(examples), f, indent=2)

print(f"Wrote {len(examples)} examples to data/bird-mini-dev/dev.json")
print()
print("First example (real fields, not assumed ones):")
print(json.dumps(examples[0], indent=2))