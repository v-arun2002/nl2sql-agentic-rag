"""
Multi-provider LLM dispatch.

Three providers are wired up: Gemini, Groq (OpenAI-compatible endpoint,
very fast LPU inference), and OpenAI.

Each agent node picks its provider/model independently via src/config.py,
so you can mix and match by task.

Provider quirks handled here rather than leaking into agent code:
  1. Newer OpenAI models reject the legacy `max_tokens` param and require
     `max_completion_tokens` instead.
  2. Those same models are REASONING models -- their token cap covers
     internal reasoning plus visible output, so a budget sized only for the
     expected output can be fully consumed by reasoning, returning an empty
     string with no error.
  3. Requests that hang open (rather than fail) would block a long run
     forever, since backoff only catches failures -- hence explicit timeouts.
"""

import time

from google import genai
from google.genai import types as genai_types
from openai import OpenAI

from src.config import settings

_gemini_client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None

# timeout + max_retries: without an explicit timeout, a request that opens but
# never returns will block the entire benchmark indefinitely -- our backoff
# layer only catches calls that FAIL, not ones that hang. 120s is generous for
# a reasoning model; max_retries=2 lets the SDK absorb transient network blips
# before our own backoff ever sees an error.
_openai_client = (
    OpenAI(api_key=settings.openai_api_key, timeout=120.0, max_retries=2)
    if settings.openai_api_key
    else None
)
_groq_client = (
    OpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        timeout=120.0,
        max_retries=2,
    )
    if settings.groq_api_key
    else None
)


def _with_backoff(fn, max_attempts: int = 5, initial_delay: float = 2.0):
    delay = initial_delay
    last_error = None
    for _ in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            if "429" not in msg and "resource_exhausted" not in msg and "rate_limit" not in msg:
                raise  # not a rate-limit error -- don't retry blindly
            last_error = e
            time.sleep(delay)
            delay *= 2
    raise last_error


def _generate_gemini(model: str, system_prompt: str, user_prompt: str, max_output_tokens: int, json_mode: bool) -> str:
    def _call(use_json_mode: bool) -> str:
        config_kwargs = {"system_instruction": system_prompt, "max_output_tokens": max_output_tokens}
        if use_json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        response = _gemini_client.models.generate_content(
            model=model, contents=user_prompt, config=genai_types.GenerateContentConfig(**config_kwargs)
        )
        return response.text

    if not json_mode:
        return _with_backoff(lambda: _call(False))
    try:
        return _with_backoff(lambda: _call(True))
    except Exception:
        return _with_backoff(lambda: _call(False))  # model may not support the mime-type constraint


def _generate_openai_compatible(
    client: OpenAI, model: str, system_prompt: str, user_prompt: str, max_output_tokens: int, json_mode: bool
) -> str:
    def _call(use_json_mode: bool, tokens_param: str) -> str:
        # Models on the max_completion_tokens param are reasoning models --
        # give them room to think on top of the requested output size.
        budget = max_output_tokens
        if tokens_param == "max_completion_tokens":
            budget += settings.reasoning_token_budget

        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tokens_param: budget,
        }
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        choice = client.chat.completions.create(**kwargs).choices[0]
        content = choice.message.content

        if not content or not content.strip():
            # Loud failure instead of returning "" -- an empty string would
            # flow downstream as an empty SQL query and silently score as
            # wrong with no diagnosable cause.
            raise RuntimeError(
                f"Model {model!r} returned empty content (finish_reason={choice.finish_reason!r}, "
                f"budget={budget}). If finish_reason is 'length', raise REASONING_TOKEN_BUDGET."
            )
        return content

    def _call_with_token_param_fallback(use_json_mode: bool) -> str:
        # Try the legacy param first, and only switch if the provider
        # specifically complains -- rather than hardcoding either name and
        # breaking on whichever provider didn't make the change.
        try:
            return _call(use_json_mode, "max_tokens")
        except Exception as e:
            msg = str(e)
            if "max_tokens" in msg and "max_completion_tokens" in msg:
                return _call(use_json_mode, "max_completion_tokens")
            raise

    if not json_mode:
        return _with_backoff(lambda: _call_with_token_param_fallback(False))
    try:
        return _with_backoff(lambda: _call_with_token_param_fallback(True))
    except Exception:
        # Not every Groq-hosted model supports response_format=json_object --
        # fall back to plain text; callers already handle non-JSON gracefully.
        return _with_backoff(lambda: _call_with_token_param_fallback(False))


def generate_text(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 500,
    json_mode: bool = False,
) -> str:
    if provider == "gemini":
        if not _gemini_client:
            raise RuntimeError("GEMINI_API_KEY not set")
        return _generate_gemini(model, system_prompt, user_prompt, max_output_tokens, json_mode)

    if provider == "groq":
        if not _groq_client:
            raise RuntimeError("GROQ_API_KEY not set")
        return _generate_openai_compatible(_groq_client, model, system_prompt, user_prompt, max_output_tokens, json_mode)

    if provider == "openai":
        if not _openai_client:
            raise RuntimeError("OPENAI_API_KEY not set")
        return _generate_openai_compatible(_openai_client, model, system_prompt, user_prompt, max_output_tokens, json_mode)

    raise ValueError(f"Unknown provider: {provider!r} (expected 'gemini', 'groq', or 'openai')")