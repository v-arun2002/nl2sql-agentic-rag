"""
Streamlit front end.

Design note: the trace renders as a vertical execution plan -- the native
artifact of this domain (cf. EXPLAIN ANALYZE) -- rather than as JSON dumps.
Correction hops are offset and marked, so the loop back through the state
machine is legible at a glance instead of buried in a list.

SQL is highlighted in-house rather than via st.code, which carries its own
light theme and ignores the surrounding palette.

Runs in two modes:
  - API mode (default): posts to the FastAPI backend at API_URL
  - Direct mode (UI_DIRECT_MODE=true): invokes the graph in-process, for
    single-process hosts like Streamlit Community Cloud
"""

import html
import os
import sys
import time
from pathlib import Path

import streamlit as st

# `streamlit run ui/app.py` puts ui/ on sys.path, not the project root, so
# `src` is not importable without this. Needed for direct mode and for the
# database discovery below.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402
from src import demo_limits  # noqa: E402
from ui.sql_highlight import highlight_sql  # noqa: E402,F401

API_URL = os.getenv("API_URL", "http://localhost:8000")
DIRECT_MODE = os.getenv("UI_DIRECT_MODE", "false").lower() == "true"


def _available_databases() -> list:
    """
    Derived from disk rather than hardcoded: locally all 11 databases are
    present, while the deployed build ships only the six small enough for
    GitHub's file-size limit. Detecting them means one code path works in both
    places, and the dropdown can never offer a database that isn't there.
    """
    root = os.path.join(settings.benchmark_data_path, "dev_databases")
    if not os.path.isdir(root):
        return []
    return [
        name
        for name in sorted(os.listdir(root))
        if os.path.isfile(os.path.join(root, name, name + ".sqlite"))
    ]


DATABASES = _available_databases()

# One starter question per database so the demo is usable without knowing any
# schema up front. Entries for databases not present on disk are simply unused.
EXAMPLES = {
    "california_schools": "How many schools are there?",
    "card_games": "How many cards have a converted mana cost greater than 5?",
    "codebase_community": "How many users have a reputation above 1000?",
    "debit_card_specializing": "What is the ratio of customers who pay in EUR against customers who pay in CZK?",
    "european_football_2": "Which league has the most matches?",
    "financial": "How many accounts are there in each region?",
    "formula_1": "Which driver has won the most races?",
    "student_club": "How many members are in the Business major?",
    "superhero": "Which publisher has the most superheroes?",
    "thrombosis_prediction": "How many patients are female?",
    "toxicology": "How many molecules are carcinogenic?",
}

AGENT_LABELS = {
    "schema_retriever": "Schema Retriever",
    "query_planner": "Query Planner",
    "sql_generator": "SQL Generator",
    "executor": "Executor",
    "classify_error": "Error Classifier",
    "error_classifier": "Error Classifier",
}

st.set_page_config(page_title="Agentic NL2SQL", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
:root {
  --ink: #0f1117; --panel: #161923; --panel-2: #1d2130; --line: #2a3040;
  --text: #e8eaf2; --muted: #858ba3; --violet: #a78bfa; --amber: #fbbf24;
  --teal: #2dd4bf; --rose: #fb7185;
}
.stApp { background: var(--ink); }
.block-container { padding-top: 2.2rem !important; max-width: 1320px; }
html, body, [class*="st-"], .stMarkdown, p, label, span, div {
  font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, sans-serif;
}
.masthead { border-bottom: 1px solid var(--line); padding-bottom: 1.1rem; margin-bottom: 1.6rem; }
.masthead h1 { font-size: 1.65rem; font-weight: 700; letter-spacing: -0.02em; color: var(--text); margin: 0 0 .3rem 0; }
.masthead .sub { font-family: 'IBM Plex Mono', monospace; font-size: .78rem; color: var(--muted); }
.masthead .sub b { color: var(--teal); font-weight: 600; }
.eyebrow { font-family: 'IBM Plex Mono', monospace; font-size: .68rem; letter-spacing: .16em;
  text-transform: uppercase; color: var(--muted); margin: 0 0 .7rem 0; }
.stats { display: flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; margin: .4rem 0 1.4rem 0; }
.stat { flex: 1; padding: .8rem 1rem; background: var(--panel); border-right: 1px solid var(--line); }
.stat:last-child { border-right: none; }
.stat .k { font-family: 'IBM Plex Mono', monospace; font-size: .64rem; letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted); display: block; margin-bottom: .28rem; }
.stat .v { font-family: 'IBM Plex Mono', monospace; font-size: 1.15rem; font-weight: 600; color: var(--text); }
.stat .v.ok { color: var(--teal); }
.stat .v.warn { color: var(--amber); }
.stat .v.bad { color: var(--rose); }
.sqlbox { background: var(--panel); border: 1px solid var(--line); border-radius: 7px;
  padding: .9rem 1rem; overflow-x: auto; margin: 0; }
.sqlbox pre { margin: 0; font-family: 'IBM Plex Mono', monospace; font-size: .8rem;
  line-height: 1.65; color: var(--text); white-space: pre-wrap; word-break: break-word; }
.sql-kw { color: var(--violet); font-weight: 500; }
.sql-str { color: var(--teal); }
.sql-num { color: #f0a882; }
.sql-comment { color: #5c6379; font-style: italic; }
.plan { position: relative; padding-left: 1.15rem; }
.plan::before { content: ''; position: absolute; left: 5px; top: 10px; bottom: 10px; width: 1px; background: var(--line); }
.node { position: relative; margin-bottom: .55rem; }
.node::before { content: ''; position: absolute; left: -1.15rem; top: 14px; width: 11px; height: 11px;
  border-radius: 50%; background: var(--ink); border: 2px solid var(--violet); }
.node.correction::before { border-color: var(--amber); }
.node.ok::before { border-color: var(--teal); }
.node.bad::before { border-color: var(--rose); }
.node-body { background: var(--panel); border: 1px solid var(--line); border-radius: 7px; padding: .65rem .85rem; }
.node.correction .node-body { background: #1e1a12; border-color: #4a3a18; }
.node-head { display: flex; align-items: baseline; gap: .55rem; margin-bottom: .3rem; flex-wrap: wrap; }
.node-name { font-size: .88rem; font-weight: 600; color: var(--text); letter-spacing: -.01em; }
.node-tag { font-family: 'IBM Plex Mono', monospace; font-size: .6rem; letter-spacing: .1em;
  text-transform: uppercase; padding: .13rem .42rem; border-radius: 3px;
  background: var(--panel-2); color: var(--muted); border: 1px solid var(--line); }
.node-tag.pass { color: var(--amber); border-color: #4a3a18; background: #201b10; }
.node-tag.ok { color: var(--teal); border-color: #14403a; background: #0f2420; }
.node-tag.bad { color: var(--rose); border-color: #4a1f28; background: #24121a; }
.node-detail { font-family: 'IBM Plex Mono', monospace; font-size: .74rem; color: var(--muted);
  line-height: 1.55; word-break: break-word; }
.node-detail .hl { color: #b9c0d6; }
.chip { display: inline-block; font-family: 'IBM Plex Mono', monospace; font-size: .68rem;
  padding: .1rem .4rem; margin: .12rem .22rem .12rem 0; border-radius: 3px;
  background: var(--panel-2); border: 1px solid var(--line); color: #b9c0d6; }
.routing { font-family: 'IBM Plex Mono', monospace; font-size: .72rem; color: var(--amber); padding: .4rem 0 .55rem 0; }
.rt { width: 100%; border-collapse: collapse; font-family: 'IBM Plex Mono', monospace; font-size: .78rem; }
.rt td { border: 1px solid var(--line); padding: .42rem .6rem; color: var(--text); }
.rt tr:nth-child(even) td { background: var(--panel); }
.empty-note { font-family: 'IBM Plex Mono', monospace; font-size: .76rem; color: var(--muted); }
.hint-note { font-family: 'IBM Plex Mono', monospace; font-size: .7rem; color: #5c6379;
  line-height: 1.5; margin: -.2rem 0 .6rem 0; }
.quota { font-family: 'IBM Plex Mono', monospace; font-size: .7rem; color: #5c6379;
  margin: 1.6rem 0 0 0; padding-top: .8rem; border-top: 1px solid var(--line); }
.stButton > button { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: .8rem;
  letter-spacing: .06em; text-transform: uppercase; background: var(--violet); color: #14121f;
  border: none; border-radius: 6px; padding: .55rem 1.1rem; transition: opacity .15s ease; }
.stButton > button:hover { opacity: .86; color: #14121f; }
.stButton > button:focus-visible { outline: 2px solid var(--teal); outline-offset: 2px; }
.stButton > button:disabled { background: var(--panel-2); color: var(--muted); }
.stTextArea textarea, .stTextInput input, .stSelectbox div[data-baseweb="select"] > div {
  background: var(--panel) !important; border-color: var(--line) !important; color: var(--text) !important;
  font-family: 'IBM Plex Mono', monospace !important; font-size: .82rem !important; }
.stTextArea > label, .stTextInput > label, .stSelectbox > label {
  font-family: 'IBM Plex Mono', monospace !important; font-size: .66rem !important;
  letter-spacing: .13em !important; text-transform: uppercase !important; color: var(--muted) !important; }
.stCheckbox label p { font-family: 'IBM Plex Mono', monospace !important; font-size: .72rem !important;
  color: var(--muted) !important; }
.idle { border: 1px dashed var(--line); border-radius: 8px; padding: 2.4rem 1.5rem;
  text-align: center; margin-top: 1.6rem; }
.idle .t { font-family: 'IBM Plex Mono', monospace; font-size: .8rem; color: var(--muted); margin-bottom: .45rem; }
.idle .d { font-size: .82rem; color: #5c6379; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="masthead">
  <h1>Agentic NL2SQL</h1>
  <div class="sub">five agents &middot; error-classification routing &middot;
  <b>44.20%</b> execution accuracy on BIRD-SQL Mini-Dev, schema-only</div>
</div>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Execution -- two modes, so the same UI works behind an API locally and
# in-process on single-process hosts.
# ---------------------------------------------------------------------------
def run_via_api(db_id: str, question: str, evidence):
    import requests

    resp = requests.post(
        f"{API_URL}/query",
        json={"db_id": db_id, "question": question, "evidence": evidence},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()


@st.cache_resource(show_spinner=False)
def get_graph():
    from src.graph import build_graph

    return build_graph()


def run_direct(db_id: str, question: str, evidence):
    from src.agents.state import initial_state

    state = initial_state(db_id, question, evidence=evidence, max_retries=settings.max_retries)
    result = get_graph().invoke(state)
    return {
        "sql": result.get("sql_query"),
        "result": result.get("execution_result"),
        "success": result.get("success", False),
        "retries": result.get("retry_count", 0),
        "trace": result.get("trace", []),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def esc(v) -> str:
    return html.escape(str(v))




def render_node(step: dict) -> str:
    node = step.get("node", "unknown")
    label = AGENT_LABELS.get(node, node.replace("_", " ").title())
    retry = step.get("retry_count", 0)
    css = ""
    tags = []
    detail = ""

    if retry and node != "executor":
        css = "correction"
        tags.append(("pass", f"pass {retry + 1}"))

    if node == "schema_retriever":
        tables = step.get("retrieved_tables", [])
        chips = "".join('<span class="chip">%s</span>' % esc(t) for t in tables)
        tags.append(("", "cache hit" if step.get("cache_hit") else "cache miss"))
        detail = "<div>retrieved %d tables</div>%s" % (len(tables), chips)

    elif node == "query_planner":
        plan = step.get("plan") or {}
        bits = []
        if plan.get("tables"):
            bits.append('tables <span class="hl">%s</span>' % esc(", ".join(plan["tables"])))
        if plan.get("joins"):
            bits.append("%d join(s)" % len(plan["joins"]))
        if plan.get("aggregation"):
            bits.append('aggregation <span class="hl">%s</span>' % esc(plan["aggregation"]))
        if plan.get("group_by"):
            bits.append('group by <span class="hl">%s</span>' % esc(", ".join(plan["group_by"])))
        detail = " &middot; ".join(bits) or "plan produced"
        note = plan.get("notes")
        if note:
            detail += '<div style="margin-top:.35rem;opacity:.75">%s</div>' % esc(note[:220])

    elif node == "sql_generator":
        sql = " ".join((step.get("sql") or "").split())
        detail = '<span class="hl">%s%s</span>' % (esc(sql[:180]), "…" if len(sql) > 180 else "")

    elif node == "executor":
        if step.get("status") == "success":
            css = "ok"
            rows = step.get("row_count", 0)
            tags.append(("ok", "success"))
            detail = 'returned <span class="hl">%d</span> row%s' % (rows, "" if rows == 1 else "s")
        else:
            css = "bad"
            tags.append(("bad", "failed"))
            detail = esc(str(step.get("error", ""))[:200])

    elif node in ("classify_error", "error_classifier"):
        css = "correction"
        tags.append(("pass", esc(step.get("error_class", "?"))))
        detail = 'classified via <span class="hl">%s</span>' % esc(step.get("source", "?"))

    else:
        detail = esc(str({k: v for k, v in step.items() if k != "node"})[:200])

    tag_html = "".join(
        '<span class="node-tag %s">%s</span>' % (cls, esc(txt)) for cls, txt in tags
    )
    return (
        '<div class="node %s"><div class="node-body">'
        '<div class="node-head"><span class="node-name">%s</span>%s</div>'
        '<div class="node-detail">%s</div>'
        "</div></div>"
    ) % (css, esc(label), tag_html, detail)


ROUTE_TARGET = {
    "SCHEMA_ERROR": "Schema Retriever",
    "SYNTAX_ERROR": "SQL Generator",
    "LOGIC_ERROR": "Query Planner",
}


def render_plan(trace: list) -> str:
    out = []
    for step in trace:
        out.append(render_node(step))
        if step.get("node") in ("classify_error", "error_classifier"):
            target = ROUTE_TARGET.get(step.get("error_class"), "end")
            out.append('<div class="routing">&#8635; routed back to %s</div>' % esc(target))
    return '<div class="plan">%s</div>' % "".join(out)


def render_result(rows) -> str:
    if not rows:
        return '<div class="empty-note">Query returned no rows.</div>'
    body = "".join(
        "<tr>%s</tr>" % "".join("<td>%s</td>" % esc(c) for c in row)
        for row in rows[:25]
    )
    more = ""
    if len(rows) > 25:
        more = '<div class="empty-note" style="margin-top:.5rem">showing 25 of %d rows</div>' % len(rows)
    return '<table class="rt">%s</table>%s' % (body, more)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
if not DATABASES:
    st.error(
        "No databases found under %s/dev_databases. See data/README.md for setup."
        % settings.benchmark_data_path
    )
    st.stop()

c1, c2 = st.columns([1, 2.4])
with c1:
    default_ix = DATABASES.index("superhero") if "superhero" in DATABASES else 0
    db_id = st.selectbox("Database", DATABASES, index=default_ix)
with c2:
    question = st.text_area("Question", value=EXAMPLES.get(db_id, ""), height=76)

# A checkbox rather than st.expander: Streamlit's expander relies on a material
# icon font that renders as raw ligature text when it fails to load, colliding
# with the label.
evidence = ""
if st.checkbox("Add a domain hint"):
    st.markdown(
        '<div class="hint-note">BIRD ships a hint field carrying domain definitions. '
        "Ignored unless the server runs with INCLUDE_EVIDENCE_IN_PROMPTS=true — the "
        "benchmark figure above was measured without it.</div>",
        unsafe_allow_html=True,
    )
    evidence = st.text_input("Hint", value="", label_visibility="collapsed")

if "queries_used" not in st.session_state:
    st.session_state.queries_used = 0

allowed, block_reason, _, _ = demo_limits.check(st.session_state.queries_used)

run = st.button("Run query", disabled=not allowed)

if demo_limits.ENABLED and not allowed:
    st.warning(block_reason)

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
if run:
    started = time.monotonic()
    try:
        with st.spinner("Running the pipeline…"):
            if DIRECT_MODE:
                data = run_direct(db_id, question, evidence or None)
            else:
                data = run_via_api(db_id, question, evidence or None)
        elapsed = time.monotonic() - started

        # Counted only after the query completes, so a crash or an API failure
        # doesn't consume someone's quota.
        st.session_state.queries_used += 1
        demo_limits.record()

        ok = data.get("success")
        retries = data.get("retries", 0)
        rows = data.get("result") or []
        trace = data.get("trace", [])

        st.markdown(
            """
<div class="stats">
  <div class="stat"><span class="k">Status</span><span class="v %s">%s</span></div>
  <div class="stat"><span class="k">Corrections</span><span class="v %s">%d</span></div>
  <div class="stat"><span class="k">Agent steps</span><span class="v">%d</span></div>
  <div class="stat"><span class="k">Rows</span><span class="v">%d</span></div>
  <div class="stat"><span class="k">Elapsed</span><span class="v">%.1fs</span></div>
</div>
"""
            % (
                "ok" if ok else "bad",
                "Success" if ok else "Failed",
                "warn" if retries else "",
                retries,
                len(trace),
                len(rows),
                elapsed,
            ),
            unsafe_allow_html=True,
        )

        left, right = st.columns([1.05, 1])

        with left:
            st.markdown('<div class="eyebrow">Generated SQL</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="sqlbox"><pre>%s</pre></div>'
                % highlight_sql(data.get("sql") or "-- no query produced --"),
                unsafe_allow_html=True,
            )

            st.markdown(
                '<div class="eyebrow" style="margin-top:1.4rem">Result</div>',
                unsafe_allow_html=True,
            )
            st.markdown(render_result(rows), unsafe_allow_html=True)

        with right:
            st.markdown('<div class="eyebrow">Execution plan</div>', unsafe_allow_html=True)
            st.markdown(render_plan(trace), unsafe_allow_html=True)

    except Exception as e:
        if not DIRECT_MODE and "Connection" in type(e).__name__:
            st.error(
                "No API at %s. Start it with `uvicorn api.main:app --reload`, "
                "or set UI_DIRECT_MODE=true to run the pipeline in this process." % API_URL
            )
        else:
            st.error("%s: %s" % (type(e).__name__, e))
else:
    st.markdown(
        """
<div class="idle">
  <div class="t">Pick a database, ask a question, run it.</div>
  <div class="d">The generated SQL, the result, and every agent hop appear here.</div>
</div>
""",
        unsafe_allow_html=True,
    )

# Rendered last so the count reflects the query that just ran. Streamlit
# re-executes the script top-to-bottom, so anything placed above the run block
# would display the pre-query value.
if demo_limits.ENABLED:
    _, _, sess_left, day_left = demo_limits.check(st.session_state.queries_used)
    st.markdown(
        '<div class="quota">%d of %d queries left this session &middot; '
        "%d left today across all visitors</div>"
        % (sess_left, demo_limits.SESSION_LIMIT, day_left),
        unsafe_allow_html=True,
    )