import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Agentic NL2SQL", layout="wide")
st.title("Agentic NL2SQL")
st.caption("Multi-agent SQL generation with error-classification-based self-correction")

col1, col2 = st.columns([1, 2])
with col1:
    db_id = st.text_input("Database ID", value="california_schools")
with col2:
    question = st.text_area("Question", value="How many schools are there?", height=80)

evidence = st.text_input(
    "Evidence (optional domain hint -- only used if INCLUDE_EVIDENCE_IN_PROMPTS=true server-side)",
    value="",
)

if st.button("Run Query", type="primary"):
    with st.spinner("Running multi-agent pipeline..."):
        try:
            resp = requests.post(
                f"{API_URL}/query",
                json={"db_id": db_id, "question": question, "evidence": evidence or None},
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()

            st.subheader("Generated SQL")
            st.code(data.get("sql") or "-- no query produced --", language="sql")

            st.subheader("Result")
            st.write(data.get("result"))

            status_col, retry_col = st.columns(2)
            status_col.metric("Status", "Success" if data.get("success") else "Failed")
            retry_col.metric("Correction attempts", data.get("retries", 0))

            st.subheader("Agent Trace")
            for step in data.get("trace", []):
                st.json(step)

        except requests.exceptions.RequestException as e:
            st.error(f"Could not reach the API at {API_URL}: {e}")
