import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    benchmark_data_path: str = os.getenv("BENCHMARK_DATA_PATH", "./data/bird-mini-dev")
    include_evidence_in_prompts: bool = os.getenv("INCLUDE_EVIDENCE_IN_PROMPTS", "false").lower() == "true"
    chroma_persist_dir: str = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))

    # Reasoning models (OpenAI's GPT-5 family and o-series) spend tokens on
    # internal reasoning BEFORE emitting any visible output, and their token
    # cap covers reasoning + output COMBINED. Without headroom, the model can
    # burn the entire budget thinking and return an empty string -- with no
    # error raised, since the API call itself succeeded. This budget is added
    # on top of the caller's requested output size for models that require
    # the max_completion_tokens parameter. Raise it if empty responses
    # persist; lower it to cut cost.
    reasoning_token_budget: int = int(os.getenv("REASONING_TOKEN_BUDGET", "3000"))

    # Per-role provider + model. Mix and match freely across the three
    # providers wired up in src/llm_providers.py.
    #
    # NOTE on Gemini: it's fully wired up and works, but gemini-3.6-flash's
    # free tier is capped at 20 requests PER DAY -- unusable for a 500-question
    # benchmark. Groq's free tier (1,000 RPD for llama-3.3-70b-versatile,
    # 14,400 for llama-3.1-8b-instant) is the practical free choice, so the
    # defaults below route there instead.
    planner_provider: str = os.getenv("PLANNER_PROVIDER", "groq")
    planner_model: str = os.getenv("PLANNER_MODEL", "llama-3.3-70b-versatile")

    generator_provider: str = os.getenv("GENERATOR_PROVIDER", "openai")
    generator_model: str = os.getenv("GENERATOR_MODEL", "gpt-5-mini")

    classifier_provider: str = os.getenv("CLASSIFIER_PROVIDER", "groq")
    classifier_model: str = os.getenv("CLASSIFIER_MODEL", "llama-3.1-8b-instant")


settings = Settings()