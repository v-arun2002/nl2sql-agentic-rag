# Agentic NL2SQL — Self-Correcting Multi-Agent SQL Generation
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
| Retrieval | Chroma + ONNX `all-MiniLM-L6-v2` | local embeddings, no API cost; the ONNX build rather than the PyTorch one, which cuts ~2GB of dependency and fits a 1GB container |
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

Or skip the backend entirely — `UI_DIRECT_MODE=true` invokes the graph in-process,
which is how the hosted demo runs on a single-process host:
```bash
UI_DIRECT_MODE=true streamlit run ui/app.py
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

**PyTorch didn't fit the deployment target.** The host allows roughly 1GB of
memory, and `sentence-transformers` pulls in PyTorch — well over that on its own.
Swapping to Chroma's ONNX build of the same `all-MiniLM-L6-v2` model dropped the
dependency to ~90MB on `onnxruntime`, which Chroma already requires. Verified
equivalent by rebuilding the index and confirming retrieval returned identical
tables and identical sample values.

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

**A trailing space in user input crashed the pipeline three layers down.** Typing
`superhero ` in the UI produced the Chroma collection name `schema_superhero `,
which fails Chroma's name validation — surfacing as an error that pointed at the
vector store rather than at the input. Sanitised once at the state boundary,
where it covers every caller.

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
