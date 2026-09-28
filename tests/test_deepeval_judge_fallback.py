"""Coverage for the DeepEval LLM-judge's Gemini->Groq fallback.

`app/eval/deepeval_suite.py`'s `GeminiWithGroqFallback` is meant to mirror
this project's own documented Gemini-primary/Groq-fallback policy
(docs/deviations.md, D-01) for the qualitative eval's judge model, instead of
letting a Gemini quota/rate-limit/5xx error kill the whole `deepeval_suite`
run outright (as it did before this existed: a real Gemini 429
RESOURCE_EXHAUSTED propagated straight out of `run_qualitative_eval`).

These tests never touch the network: `_gemini.generate`/`a_generate` and the
Groq client are monkeypatched with fakes, so they run fast and deterministically
under `pytest` with no API keys required.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from app.eval.deepeval_suite import GeminiWithGroqFallback, _is_fallback_worthy


def _make_judge(monkeypatch) -> GeminiWithGroqFallback:
    """A GeminiWithGroqFallback whose real GeminiModel construction is
    skipped (no network, no real key needed) via object.__new__, matching
    this codebase's existing test pattern for bypassing __init__."""
    judge = object.__new__(GeminiWithGroqFallback)
    judge._gemini = types.SimpleNamespace()  # placeholder; methods patched per-test below
    judge._groq_model_name = "openai/gpt-oss-120b"
    judge._groq_api_key = "test-groq-key"
    judge._groq_client = None
    judge.name = "gemini-3.1-pro-preview (fallback: groq/openai/gpt-oss-120b)"
    return judge


class _FakeQuotaError(Exception):
    """Stands in for google.genai.errors.ClientError's 429 RESOURCE_EXHAUSTED."""

    def __str__(self):
        return "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: ... model: gemini-3.1-pro"


def test_is_fallback_worthy_recognizes_quota_and_rate_limit_errors():
    assert _is_fallback_worthy(_FakeQuotaError())
    assert _is_fallback_worthy(Exception("503 Service Unavailable"))
    assert _is_fallback_worthy(Exception("rate limit exceeded, please retry"))


def test_is_fallback_worthy_rejects_unrelated_errors():
    """A bad-schema or programming error must NOT trigger a fallback that
    would just fail identically against a different provider."""
    assert not _is_fallback_worthy(ValueError("invalid literal for schema field 'foo'"))
    assert not _is_fallback_worthy(KeyError("GOOGLE_API_KEY"))


class _FakeStructuredGroq:
    """Stands in for `ChatGroq(...).with_structured_output(schema)`."""

    def __init__(self, sentinel):
        self._sentinel = sentinel

    def invoke(self, prompt):
        return self._sentinel

    async def ainvoke(self, prompt):
        return self._sentinel


class _FakeGroqClient:
    def __init__(self, sentinel):
        self._sentinel = sentinel
        self.with_structured_output_calls: list[dict] = []

    def with_structured_output(self, schema, method=None, include_raw=False):
        # Recorded so tests can assert method="json_schema" is actually used
        # (see test_a_generate_falls_back_to_groq_on_quota_error below) --
        # langchain_groq's own default, "function_calling", is a real,
        # verified bug: it deterministically fails against openai/gpt-oss-120b
        # for DeepEval's actual generate_statements prompt/schema shape (3/3
        # real failures reproduced against the live Groq API), while
        # "json_schema" succeeded every time with the identical prompt.
        self.with_structured_output_calls.append({"schema": schema, "method": method, "include_raw": include_raw})
        return _FakeStructuredGroq(self._sentinel)


def test_a_generate_falls_back_to_groq_on_quota_error(monkeypatch):
    judge = _make_judge(monkeypatch)
    sentinel = object()  # stands in for a parsed schema instance

    async def failing_a_generate(prompt, schema=None):
        raise _FakeQuotaError()

    judge._gemini.a_generate = failing_a_generate
    groq_client = _FakeGroqClient(sentinel)
    monkeypatch.setattr(judge, "_groq", lambda: groq_client)

    result, cost = asyncio.run(judge.a_generate("prompt", schema=object))

    assert result is sentinel
    assert cost == 0.0
    # Must use method="json_schema", not langchain_groq's default
    # "function_calling" -- see the comment on _FakeGroqClient above for why.
    assert groq_client.with_structured_output_calls == [
        {"schema": object, "method": "json_schema", "include_raw": False}
    ]


def test_a_generate_does_not_fall_back_on_non_retryable_error(monkeypatch):
    judge = _make_judge(monkeypatch)
    called = {"groq": False}

    async def failing_a_generate(prompt, schema=None):
        raise ValueError("invalid literal for schema field 'foo'")

    def fake_groq():
        called["groq"] = True
        return _FakeGroqClient(object())

    judge._gemini.a_generate = failing_a_generate
    monkeypatch.setattr(judge, "_groq", fake_groq)

    with pytest.raises(ValueError):
        asyncio.run(judge.a_generate("prompt", schema=object))

    assert called["groq"] is False


def test_a_generate_uses_gemini_result_when_it_succeeds(monkeypatch):
    """No error at all -> Groq must never be constructed or called."""
    judge = _make_judge(monkeypatch)
    called = {"groq": False}

    async def succeeding_a_generate(prompt, schema=None):
        return "gemini-answer", 0.0042

    def fake_groq():
        called["groq"] = True
        raise AssertionError("Groq should not be used when Gemini succeeds")

    judge._gemini.a_generate = succeeding_a_generate
    monkeypatch.setattr(judge, "_groq", fake_groq)

    result, cost = asyncio.run(judge.a_generate("prompt", schema=object))

    assert result == "gemini-answer"
    assert cost == 0.0042
    assert called["groq"] is False


def test_generate_falls_back_to_groq_on_quota_error(monkeypatch):
    """Sync counterpart of the async test above."""
    judge = _make_judge(monkeypatch)
    sentinel = object()

    def failing_generate(prompt, schema=None):
        raise _FakeQuotaError()

    judge._gemini.generate = failing_generate
    groq_client = _FakeGroqClient(sentinel)
    monkeypatch.setattr(judge, "_groq", lambda: groq_client)

    result, cost = judge.generate("prompt", schema=object)

    assert result is sentinel
    assert cost == 0.0
    assert groq_client.with_structured_output_calls == [
        {"schema": object, "method": "json_schema", "include_raw": False}
    ]


def test_groq_lazy_builder_raises_clearly_when_no_key_configured():
    """If Gemini fails and there's no GROQ_API_KEY either, the failure must
    say so plainly rather than raising an opaque langchain_groq auth error."""
    judge = object.__new__(GeminiWithGroqFallback)
    judge._groq_model_name = "openai/gpt-oss-120b"
    judge._groq_api_key = None
    judge._groq_client = None

    import os

    old = os.environ.pop("GROQ_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="GROQ_API_KEY is not set"):
            judge._groq()
    finally:
        if old is not None:
            os.environ["GROQ_API_KEY"] = old
