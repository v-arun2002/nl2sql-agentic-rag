from src.config import settings
from google import genai

print(f"Key length: {len(settings.gemini_api_key)}")
print(f"First 6 chars: {settings.gemini_api_key[:6]}")

client = genai.Client(api_key=settings.gemini_api_key)
response = client.models.generate_content(model="gemini-3.6-flash", contents="Say hello in one word")
print("SUCCESS:", response.text)