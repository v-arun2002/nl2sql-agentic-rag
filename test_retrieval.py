from src.retrieval.vector_store import SchemaVectorStore

store = SchemaVectorStore()
results = store.retrieve_relevant_tables(
    "debit_card_specializing",
    "In 2012, who had the least consumption in LAM?",
    top_k=3,
)

print(f"Retrieved {len(results)} tables\n")
for r in results:
    print("table_name:", r.get("table_name"))
    print("schema_text length:", len(r.get("schema_text") or ""))
    print("schema_text:", repr(r.get("schema_text"))[:300])
    print("-" * 40)