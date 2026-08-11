# Agentic NL2SQL — Self-Correcting Multi-Agent SQL Generation

> **Ask a database a question in plain English. Get correct SQL back.**
> Five specialised agents, a self-correcting loop that routes each failure to
> whichever agent caused it, and a measured **44.20%** on 500 BIRD-SQL questions
> — without the hints most published numbers use.

![Python](https://img.shields.io/badge/python-3.11+-blue)
![LangGraph](https://img.shields.io/badge/LangGraph-state%20machine-orange)
![Benchmark](https://img.shields.io/badge/BIRD--SQL%20Mini--Dev-44.20%25-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

<!-- Replace with a screenshot of the running Streamlit UI showing SQL + agent trace -->
![Demo](docs/demo.png)

---

## Architecture

![Architecture](docs/architecture.svg)

Five agents wired as a [LangGraph](https://github.com/langchain-ai/langgraph)
state machine with conditional edges (`src/graph.py`). The correction loop is the
part worth looking at: instead of re-prompting with a raw error, an
**error-classification router** diagnoses *why* the query failed and sends it back
to the agent responsible — a wrong table goes to the retriever, bad syntax to the
generator, faulty logic to the planner.

### Why five agents instead of one prompt

Each exists to solve a specific failure of the naive "dump-the-schema-and-ask"
approach:

- **Schemas don't fit usefully in a prompt.** 199 columns buries the relevant
  tables in noise. The retriever narrows to the top 6 by semantic similarity.
- **Planning and writing are different skills.** Emitting a structured plan before
  SQL is what makes targeted correction possible — you can fix a *plan*
  independently of fixing *syntax*.
- **Different failures need different fixes.** A wrong table reference and a
  malformed `GROUP BY` are not the same problem, and shouldn't get the same retry.

---

## Results

| Difficulty | Accuracy |
|---|---|
| simple | 61.49% (91/148) |
| moderate | 41.60% (104/250) |
| challenging | 25.49% (26/102) |
| **overall** | **44.20% (221/500)** |

Run without BIRD's `evidence` field — the human-written hints that often contain
the answer's formula. That makes this a *schema-only* result: the system must
work out what a question means from table and column structure alone. Published
numbers on this split generally include evidence, so they aren't directly
comparable.

### Per-database accuracy — and the finding that matters

| Database | Columns | Accuracy |
|---|---|---|
| superhero | 31 | 65.38% |
| european_football_2 | **199** | 56.86% |
| codebase_community | 71 | 55.10% |
| student_club | 48 | 54.17% |
| toxicology | 11 | 42.50% |
| formula_1 | 94 | 42.42% |
| card_games | 115 | 38.46% |
| debit_card_specializing | 21 | 36.67% |
| thrombosis_prediction | 64 | 34.00% |
| financial | 55 | 21.88% |
| california_schools | 89 | **16.67%** |

**Schema size does not predict accuracy — semantic clarity does.**
`european_football_2` has the largest schema in the benchmark (199 columns) and
scores near the top. `california_schools` has less than half that and scores
worst. The difference is whether column names carry meaning on their own:
`superhero.publisher_name` is self-describing, while answering a
`california_schools` question requires knowing that "total enrollment" means
`Enrollment (K-12)` + `Enrollment (Ages 5-17)` — information present in neither
the schema nor the column names.

This runs against the intuition that bigger schemas are harder. Retrieval handles
size well; it cannot manufacture domain knowledge that was never written down.

---

## Why the schema index carries sample values

Column names alone caused a persistent, silent failure class. Two tables in
`debit_card_specializing` both have a `Date` column:

```
yearmonth.Date        (TEXT)  sample values: '201112', '201201', '201202'
transactions_1k.Date  (DATE)  sample values: '2012-08-24', '2012-08-23'
```

Without seeing actual values, the model reasonably assumed ISO dates and wrote
`strftime('%Y', Date) = '2012'` — which returns `NULL` against `'201208'` and
silently matches zero rows. No error, no exception, just a wrong answer. Indexing
real sample values alongside column names eliminated this category entirely.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph | explicit state + conditional routing for the correction loop |
| LLM | Multi-provider (OpenAI / Groq / Gemini), selected per agent role | see below |
| Retrieval | sentence-transformers + Chroma | local embeddings, no API cost, no external service |
| Backend | FastAPI | exposes the graph as `POST /query` |
| Demo UI | Streamlit | shows generated SQL, results, and the full agent trace |
| Ops | Docker, docker-compose, GitHub Actions | containerised, CI-gated regression tests |

**Provider routing is per-role, not global.** The classifier picks one of four
category labels — a large reasoning model is waste there, so it runs on Groq's
`llama-3.1-8b-instant`. The planner and generator do the actual reasoning and run
on OpenAI's `gpt-5-mini`. Configured entirely through environment variables
(`src/config.py`), so switching a provider is a config change, not a code change.

That abstraction paid for itself: Gemini's free tier turned out to allow **20
requests per day** for `gemini-3.6-flash`, making it unusable for a 500-question
benchmark. Moving the planner to another provider took one line in `.env`.

---

## Setup

```bash
git clone https://github.com/v-arun2002/nl2sql-agentic-rag.git
cd nl2sql-agentic-rag
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env
# add your API keys — see .env.example for which providers each role uses
```

Then fetch the dataset and build the schema index — see **`data/README.md`** for
the two-step process (questions from Hugging Face, `.sqlite` files from the BIRD
site), then:

```bash
python -m data.build_schema_index
```

## Running it

**Interactive demo** (two terminals):
```bash
uvicorn api.main:app --reload      # http://localhost:8000/docs
streamlit run ui/app.py            # http://localhost:8501
```

**Benchmark:**
```bash
python -m eval.run_benchmark               # full 500
BENCHMARK_LIMIT=50 python -m eval.run_benchmark   # quick slice
```
Results checkpoint to `eval/results.csv` every 10 questions, and a re-run resumes
from whatever is already there — a full run takes hours, and interruptions
shouldn't cost the whole thing.

**Regression tests:**
```bash
pytest tests/test_regression.py -v
```
Checks *correctness* (execution match against gold SQL), not merely that the
pipeline didn't crash.

**Containerised:**
```bash
docker compose up --build
```

---

## What this doesn't do

Honest limits, from the failure analysis in `eval/results.csv`:

**The correction loop cannot catch wrong-but-valid SQL.** On `california_schools`,
every failure had `retries: 0` — the SQL executed cleanly and returned rows, just
the wrong ones. There is no error to classify when a query succeeds with a
plausible wrong answer. Error-classification routing fixes *broken* SQL, not
*incorrect* SQL.

**Some questions are unanswerable schema-only by construction.** "Total enrollment
over 500" requires knowing it means the sum of two specific columns. That
information lives in BIRD's `evidence` field, which this configuration
deliberately withholds.

**Exact-match scoring penalises correct-but-differently-shaped answers.** Asked
for a "peak month," the system returned `'201307'` where the reference expects
`'07'`. Same logic, same underlying answer, scored wrong. A meaningful share of
the 279 failures are shape mismatches rather than reasoning errors.

### A negative result worth recording

Output-shape mismatches looked like the largest addressable failure category, so
the generator prompt was extended with explicit rules about column selection and
granularity. Measured on a fixed 50-question slice: **4 questions fixed, 2
broken, net +2 — within noise at that sample size.** Inspecting the regressions
showed the longer prompt had introduced *new* aggregation errors (`MAX(Consumption)`
where the previous prompt correctly summed per month first). The change was
reverted.

Reported here because a measured non-improvement is a result, and reverting on
evidence is the correct call.

---

## Notable bugs found and fixed

Each was diagnosed by isolating one layer and testing it directly, rather than
inferring from end-to-end output:

**Reasoning tokens consumed the entire output budget.** `max_completion_tokens=500`
on `gpt-5-mini` was fully spent on internal reasoning, returning `''` with no
error — the API call genuinely succeeded. Empty SQL then ran against SQLite as a
harmless no-op returning zero rows, which the pipeline recorded as *success*.
Three layers each behaved correctly in isolation while a silent failure passed
through all of them. Confirmed via `usage.reasoning_tokens=500` and
`finish_reason='length'`; fixed with a separate reasoning budget plus a guard
that treats empty SQL as an explicit failure.

**An empty vector index produced hallucinated schemas.** A rebuild command silently
never executed, leaving Chroma empty. Retrieval returned zero tables, which
became an empty schema string, which the planner accepted as valid context — so
the generator invented plausible tables (`gas_consumption`, `payments`) that
didn't exist. Accuracy sat at 5% for three runs. Found by testing retrieval in
isolation instead of trusting benchmark output.

**Gemini's free tier is 20 requests per day**, not per minute, for
`gemini-3.6-flash`. No amount of exponential backoff fixes a daily cap.

**`max_tokens` → `max_completion_tokens`** on newer OpenAI models. Handled with a
self-correcting fallback that tries the legacy parameter first and switches only
when a provider specifically objects, so Groq keeps working either way.

**Windows cp1252 encoding crashed the results write** *after* a completed run,
destroying several hours of output. Fixed with explicit UTF-8 plus
`errors="replace"`.

**No client timeouts.** A request that opened but never returned blocked a run
indefinitely — backoff only catches calls that *fail*, not ones that hang.

---

## Repository layout

```
src/
  graph.py                 LangGraph state machine + correction routing
  config.py                per-role provider/model configuration
  llm_providers.py         multi-provider dispatch, backoff, timeouts
  agents/                  schema_retriever, query_planner, sql_generator,
                           error_classifier, shared state
  retrieval/               Chroma vector store + optional Redis cache
  db/executor.py           SQLite execution with empty-query guard
eval/
  run_benchmark.py         benchmark harness (checkpointing + resume)
  metrics.py               execution accuracy (BIRD's EX metric)
  results_500_baseline.csv per-question results and failure taxonomy
data/build_schema_index.py schema introspection + sample-value extraction
api/ ui/                   FastAPI backend, Streamlit frontend
k8s/ terraform/            Kubernetes manifests, AWS Lambda IaC
scripts/                   diagnostic tools used during development
```

## Extending

- `src/config.py` centralises model choice per role — swapping providers requires
  no code changes.
- `eval/results.csv` carries `error_classes_hit` per question; grouping by it
  gives the failure taxonomy directly.
- CI (`.github/workflows/eval.yml`) runs the small fixed regression set only, to
  stay fast. Run the full benchmark manually.
