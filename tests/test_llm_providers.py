"""
Reasoning-model headroom in the OpenAI-compatible provider path.

Free and offline: a fake client records every request. The case under test is
the one that broke the classifier -- Groq's gpt-oss-20b accepts max_tokens,
spends the whole 20-token budget reasoning, and returns empty content with
finish_reason='length'.
"""

from types import SimpleNamespace

import pytest

from src import llm_providers
from src.config import settings


class FakeClient:
    """Answers chat.completions.create from a script of (content, finish_reason)."""

    def __init__(self, replies, reject_max_tokens=False):
        self.replies = list(replies)
        self.reject_max_tokens = reject_max_tokens
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_max_tokens and "max_tokens" in kwargs:
            raise RuntimeError("Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.")
        content, finish = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


def _gen(client, max_output_tokens=20):
    return llm_providers._generate_openai_compatible(client, "m", "sys", "user", max_output_tokens, False)


def test_empty_on_length_retries_once_with_headroom():
    client = FakeClient([("", "length"), ("LOGIC_ERROR", "stop")])
    assert _gen(client) == "LOGIC_ERROR"
    assert [c["max_tokens"] for c in client.calls] == [20, 20 + settings.reasoning_token_budget]


def test_successful_first_call_is_unchanged():
    # The guarantee that lets this ship without moving other roles: a call
    # that already returns content makes exactly one request, budget as-is.
    client = FakeClient([("SCHEMA_ERROR", "stop")])
    assert _gen(client) == "SCHEMA_ERROR"
    assert len(client.calls) == 1
    assert client.calls[0]["max_tokens"] == 20


def test_empty_for_another_reason_is_not_retried():
    client = FakeClient([("", "stop")])
    with pytest.raises(RuntimeError, match="returned empty content"):
        _gen(client)
    assert len(client.calls) == 1


def test_headroom_is_tried_once_not_forever():
    client = FakeClient([("", "length"), ("", "length")])
    with pytest.raises(RuntimeError, match="returned empty content"):
        _gen(client)
    assert len(client.calls) == 2


def test_max_completion_tokens_path_is_unchanged():
    # OpenAI reasoning models (the planner and generator) reject max_tokens
    # and already get headroom on max_completion_tokens -- same as before.
    client = FakeClient([("SELECT 1", "stop")], reject_max_tokens=True)
    assert _gen(client) == "SELECT 1"
    assert client.calls[-1]["max_completion_tokens"] == 20 + settings.reasoning_token_budget
    assert len(client.calls) == 2  # the rejected max_tokens try, then the real one


# --- billing exhaustion: never retried ----------------------------------------
#
# The real response from an exhausted OpenAI account, verbatim. A 429 -- the
# same status as a per-minute rate limit, which is why it used to be retried.
QUOTA_MSG = (
    "Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to "
    "continue using the API at https://platform.openai.com/settings/organization/billing/.', "
    "'type': 'insufficient_quota', 'param': None, 'code': 'credit_balance_exhausted'}}"
)
RATE_MSG = "Error code: 429 - {'error': {'message': 'Rate limit reached', 'code': 'rate_limit_exceeded'}}"


def _counting(exc_or_value_seq):
    calls = []

    def fn():
        calls.append(1)
        item = exc_or_value_seq[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return fn, calls


def test_backoff_does_not_retry_quota_exhaustion(monkeypatch):
    monkeypatch.setattr(llm_providers.time, "sleep", lambda s: pytest.fail("must not back off on exhausted credit"))
    fn, calls = _counting([RuntimeError(QUOTA_MSG)])
    with pytest.raises(llm_providers.QuotaExhaustedError):
        llm_providers._with_backoff(fn)
    assert len(calls) == 1


def test_backoff_still_retries_a_real_rate_limit(monkeypatch):
    monkeypatch.setattr(llm_providers.time, "sleep", lambda s: None)
    fn, calls = _counting([RuntimeError(RATE_MSG), RuntimeError(RATE_MSG), "ok"])
    assert llm_providers._with_backoff(fn) == "ok"
    assert len(calls) == 3


def test_is_quota_exhausted_follows_the_cause_chain():
    try:
        try:
            raise RuntimeError(QUOTA_MSG)
        except RuntimeError as inner:
            raise ValueError("wrapped by some layer") from inner
    except ValueError as outer:
        assert llm_providers.is_quota_exhausted(outer)
    assert not llm_providers.is_quota_exhausted(RuntimeError(RATE_MSG))


def _response(status, text):
    import httpx2
    return httpx2.Response(status, text=text, request=httpx2.Request("POST", "https://api.openai.com/v1/chat/completions"))


@pytest.mark.parametrize(
    "status,text,expected",
    [
        (429, QUOTA_MSG, False),   # exhausted credit: the SDK must NOT retry
        (429, RATE_MSG, True),     # real rate limit: SDK default, retried as before
        (500, "server error", True),
        (408, "timeout", True),
        (400, "bad request", False),
    ],
)
def test_sdk_retry_decision(status, text, expected):
    client = llm_providers._OpenAICompatible(api_key="test-key")
    assert client._should_retry(_response(status, text)) is expected
