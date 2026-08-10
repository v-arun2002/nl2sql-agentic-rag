# Agentic NL2SQL — Self-Correcting Multi-Agent SQL Generation

A multi-agent system that turns natural language questions into SQLite
queries, benchmarked against **BIRD-SQL Mini-Dev** (500 examples across 11
real-world databases). The core differentiator
isn't "an agent that retries on failure" — it's an **error-classification
router** that sends each failure back to the *specific* agent responsible
for that class of mistake, instead of blindly re-prompting with the raw
error message.

## Architecture

```
User Question
     |
     v
[Schema Retriever] --(RAG over table/column embeddings, Chroma + sentence-transformers)
     |
     v
[Query Planner] --(LLM: decomposes intent into structured plan -- tables, joins, filters, aggregation)
     |
     v
[SQL Generator] --(LLM: plan + schema -> SQLite query)
     |
     v
[Executor] --(runs query against SQLite)
     |
     +-- success --> done
     |
     +-- failure --> [Error Classifier] --(rule-based first pass, LLM fallback for ambiguous cases)
                            |
              +-------------+-------------+
              |             |             |
        SCHEMA_ERROR   SYNTAX_ERROR   LOGIC_ERROR
              |             |             |
      back to Schema   back to SQL   back to Query
        Retriever       Generator       Planner
              |             |             |
              +-------------+-------------+
                            |
                    (loops back to Executor,
                     bounded by MAX_RETRIES)
```

Built as a [LangGraph](https://github.com/langchain-ai/langgraph) state
machine (`src/graph.py`) — see that file for the routing logic.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph | explicit state + conditional routing for the correction loop |
| LLM | Gemini + Groq (+ OpenAI, optional) -- see `src/llm_providers.py` | multi-provider by design; free tier on two of three |
| Retrieval | sentence-transformers + Chroma | local embeddings, no API cost, no external DB to provision |
| Backend | FastAPI | thin wrapper exposing the graph as `/query` |
| Demo UI | Streamlit | fastest path to a clickable, shareable demo |
| Ops | Docker, docker-compose, GitHub Actions | containerized + CI-gated regression tests |

**On the LLM layer:** each agent role (planner, generator, classifier) picks
its own provider + model independently via `src/config.py`. The setup used
for this project's actual benchmark run: **Gemini** for the planner (strong
reasoning, free), **GPT-5 mini** for the generator (the step that writes
the actual SQL -- worth paying for since correctness there drives the whole
accuracy number; $0.25/$2.00 per 1M tokens means a small ($3-5) budget covers
thousands of calls, far more than one benchmark run needs), and **Groq's
llama-3.1-8b-instant** for the classifier (needs speed and volume, not
depth -- free tier's highest daily quota). Deliberately not "one provider
for everything" -- each role's provider was chosen for what that role
actually needs.

OpenAI is off by default in `.env.example` if no budget is funded (new
accounts get GPT-3.5 Turbo at 3 requests/minute only -- not usable for this
project); the config above is the actual funded setup, ready to uncomment.

Both free tiers have real constraints worth knowing: Gemini's free-tier
content is used by Google to improve their products (fine for public
benchmark data like BIRD-SQL, worth knowing for anything sensitive), and
both Gemini and Groq enforce real rate limits (Groq: ~30 requests/minute
per model; Gemini: similar order of magnitude, varies by model).
`src/llm_providers.py` wraps every call with exponential backoff so a full
benchmark run degrades gracefully on 429s instead of crashing. Current
numbers shift over time -- check
[Gemini's rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
and [Groq's rate limits](https://console.groq.com/docs/rate-limits) before
a large benchmark run.

## Setup

```bash
git clone <your-repo-url>
cd nl2sql-agentic-rag
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and add whichever provider keys you're using --
# GEMINI_API_KEY (free: https://aistudio.google.com/apikey) and
# GROQ_API_KEY (free: https://console.groq.com) cover the defaults;
# OPENAI_API_KEY is optional and requires billing to actually work.
```

Download and place the dataset per `data/README.md`, then index each
database's schema into the vector store (see that file for the snippet).

## Running it

**API + benchmark, locally:**
```bash
uvicorn api.main:app --reload          # http://localhost:8000/docs
python -m eval.run_benchmark           # prints overall + per-difficulty accuracy
pytest tests/test_regression.py -v     # fixed regression set
```

**Demo UI:**
```bash
streamlit run ui/app.py
```

**Everything via Docker (now includes Redis for schema-retrieval caching):**
```bash
docker compose up --build
# API:   http://localhost:8000
# UI:    http://localhost:8501
# Redis: localhost:6379 (optional -- pipeline runs fine without it)
```

**On Kubernetes (validated locally via `kind`, cloud-portable):**
```bash
# see k8s/README.md for full setup -- brief version:
docker build -t nl2sql-agentic-rag-api:latest .
kind load docker-image nl2sql-agentic-rag-api:latest
kubectl apply -f k8s/00-namespace-configmap.yaml
kubectl apply -f k8s/02-redis.yaml -f k8s/03-api.yaml -f k8s/04-ui-ingress.yaml
```

**On AWS (Lambda + API Gateway via Terraform, free-tier-eligible):**
```bash
# see terraform/README.md for the required two-phase deploy (image doesn't
# exist until the ECR repo does) -- brief version:
cd terraform && terraform apply -target=aws_ecr_repository.api
# build, tag, push the image (see terraform/README.md), then:
terraform apply
```

## What this is (and isn't) — for interviews

- **It is:** a genuinely branching multi-agent system — the correction
  router makes different agent-routing decisions depending on *why* a query
  failed, not just *that* it failed.
- **Be ready to state your honest benchmark number** and the per-difficulty
  breakdown from `eval/results.csv`, including where the correction loop
  *doesn't* help (e.g. UNKNOWN_ERROR/TIMEOUT_ERROR cases end the loop rather
  than retry blindly — that's a deliberate design choice, not a gap to hide).
- **`error_classes_hit` in the results CSV is your failure taxonomy** —
  knowing which error class dominates your failures (and why) is a much
  stronger interview answer than a single accuracy number.
- **On the ops layer (Redis, K8s, Terraform):** be ready to explain *why*
  each exists, not just that it does — Redis caches repeated schema
  lookups across benchmark re-runs; the K8s manifests demonstrate a real
  Deployment/Service/HPA setup validated locally to avoid a managed
  cluster's idle cost; Terraform targets Lambda specifically because it's
  free at rest, unlike a cluster's control plane. Deliberate cost-aware
  choices, not the "biggest" option on each axis.

## Extending

- Swap the rule-based classifier's SQLite string-matching for a proper
  parser if you outgrow the current heuristics.
- `src/config.py` centralizes model choice per task — cheap experiments with
  swapping the classifier model don't touch any agent logic.
- CI (`.github/workflows/eval.yml`) only runs the small fixed regression
  set, not the full benchmark, to keep it fast — run
  `eval/run_benchmark.py` manually when you want the full picture.
