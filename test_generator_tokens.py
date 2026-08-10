from openai import OpenAI
from src.config import settings

client = OpenAI(api_key=settings.openai_api_key)

resp = client.chat.completions.create(
    model=settings.generator_model,
    messages=[
        {"role": "system", "content": "You are a SQLite query generation agent. Respond with ONLY the SQL query."},
        {"role": "user", "content": (
            "Question: What was the difference in gas consumption between CZK-paying "
            "customers and EUR-paying customers in 2012?\n\n"
            "Schema:\n- customers(CustomerID, Segment, Currency)\n"
            "- yearmonth(CustomerID, Date, Consumption)"
        )},
    ],
    max_completion_tokens=500,
)

print("finish_reason:", resp.choices[0].finish_reason)
print("content:", repr(resp.choices[0].message.content))
print("usage:", resp.usage)